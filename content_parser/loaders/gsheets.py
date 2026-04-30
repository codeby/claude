"""Google Sheets loader: pull a column/range of values for plugin inputs.

Auth: service-account JSON. The user creates a service account in Google Cloud,
downloads its JSON key, stores it in the GOOGLE_SHEETS_CREDENTIALS secret, and
shares each target spreadsheet with the service account's email address.

Usage:
    loader = GoogleSheetsLoader.from_secrets({"GOOGLE_SHEETS_CREDENTIALS": "..."})
    loaded = loader.load(sheet_id_or_url, tab="Communities", range_a1="A2:A100")
    print(loaded.values)  # ['durov_says', 'telegram', ...]
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ..core.errors import AuthError, PluginError


SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


# Sheet ID embedded in /d/<id>/ in the spreadsheet URL.
_URL_SHEET_ID_RE = re.compile(r"/d/([A-Za-z0-9_-]{20,})")
# Bare sheet ID (no slashes, just the alphanumeric portion).
_BARE_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
# A1 range like "Sheet1!A2:B10" or just "A2:B10" — light validation.
_RANGE_A1_RE = re.compile(r"^[A-Za-z0-9_]+(?::[A-Za-z0-9_]+)?$|^[^!]+!.+$")


@dataclass
class LoadedRange:
    """Result of a Google Sheets load."""
    sheet_title: str
    tab_title: str
    range_a1: str
    values: list[str]
    sheet_url: str

    @property
    def count(self) -> int:
        return len(self.values)


class GoogleSheetsLoader:
    """Thin wrapper around gspread for read-only sheet access."""

    def __init__(self, credentials_json: dict | str):
        self._credentials_json = self.validate_credentials(credentials_json)
        self._gc = self._build_client()

    @staticmethod
    def validate_credentials(credentials_json: dict | str) -> dict:
        """Parse + shape-check service account JSON. Raises AuthError on bad input.

        Does NOT build a gspread client — safe to call without network.
        Returns the parsed dict for the caller to reuse.
        """
        if isinstance(credentials_json, str):
            try:
                credentials_json = json.loads(credentials_json)
            except json.JSONDecodeError as e:
                raise AuthError(
                    "GOOGLE_SHEETS_CREDENTIALS is not valid JSON. "
                    "Paste the full service-account JSON file contents."
                ) from e

        if not isinstance(credentials_json, dict):
            raise AuthError("GOOGLE_SHEETS_CREDENTIALS must be a JSON object.")
        if credentials_json.get("type") != "service_account":
            raise AuthError(
                "GOOGLE_SHEETS_CREDENTIALS is not a service account JSON "
                "(missing or wrong 'type' field). OAuth client JSONs won't work — "
                "use a service account key."
            )
        for required in ("client_email", "private_key"):
            if not credentials_json.get(required):
                raise AuthError(
                    f"GOOGLE_SHEETS_CREDENTIALS missing field {required!r} — "
                    "is this a service account JSON?"
                )
        return credentials_json

    @classmethod
    def from_secrets(cls, secrets: dict[str, str]) -> GoogleSheetsLoader:
        raw = secrets.get("GOOGLE_SHEETS_CREDENTIALS")
        if not raw:
            raise AuthError("GOOGLE_SHEETS_CREDENTIALS is required.")
        return cls(raw)

    def service_account_email(self) -> str | None:
        """Email to share spreadsheets with. Useful for UX hints."""
        return self._credentials_json.get("client_email")

    # ------------------------------------------------------------------

    def _build_client(self) -> Any:
        try:
            from google.oauth2.service_account import Credentials  # noqa: PLC0415
            import gspread  # noqa: PLC0415
        except ImportError as e:
            raise PluginError(
                "Install gspread + google-auth: pip install gspread google-auth"
            ) from e

        try:
            creds = Credentials.from_service_account_info(
                self._credentials_json, scopes=SHEETS_SCOPES
            )
        except Exception as e:
            raise AuthError(f"Cannot build credentials: {e}") from e
        return gspread.authorize(creds)

    # ------------------------------------------------------------------

    def load(
        self,
        sheet: str,
        *,
        tab: str | None = None,
        range_a1: str = "A:A",
        skip_header: bool = False,
    ) -> LoadedRange:
        """Read a range from a sheet, return a flat list of non-empty values.

        sheet: spreadsheet ID or full URL.
        tab: worksheet (tab) name. None or empty string → first sheet.
        range_a1: A1 notation. Defaults to entire column A.
        skip_header: drop the first row (e.g. when header is in row 1).
        """
        sheet_id = self._extract_sheet_id(sheet)
        if not range_a1 or not _RANGE_A1_RE.match(range_a1):
            raise PluginError(
                f"Invalid range {range_a1!r}. Use A1 notation, e.g. 'A2:A100' or 'A:A'."
            )

        # Defensive: callers (cron config, CLI) may pass empty string for "first sheet".
        tab = tab.strip() if isinstance(tab, str) else tab
        if not tab:
            tab = None

        try:
            spreadsheet = self._gc.open_by_key(sheet_id)
        except Exception as e:
            self._raise_friendly_open_error(e, sheet_id)

        try:
            worksheet = spreadsheet.worksheet(tab) if tab else spreadsheet.sheet1
        except Exception as e:
            available = self._list_tab_names(spreadsheet)
            raise PluginError(
                f"Tab {tab!r} not found in spreadsheet. Available: {available}"
            ) from e

        try:
            rows = worksheet.get(range_a1) or []
        except Exception as e:
            raise PluginError(f"Cannot read range {range_a1!r}: {e}") from e

        if skip_header and rows:
            rows = rows[1:]

        # Flatten: each row becomes its non-empty cells; concatenate cells of all rows.
        # For a single-column range this collapses cleanly to one value per row.
        values: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for cell in row:
                v = (cell or "").strip()
                if v and v not in seen:
                    seen.add(v)
                    values.append(v)

        return LoadedRange(
            sheet_title=spreadsheet.title,
            tab_title=worksheet.title,
            range_a1=range_a1,
            values=values,
            sheet_url=f"https://docs.google.com/spreadsheets/d/{sheet_id}",
        )

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _extract_sheet_id(sheet: str) -> str:
        v = (sheet or "").strip()
        if not v:
            raise PluginError("Sheet ID or URL is required.")

        # Bare ID (no URL).
        if _BARE_SHEET_ID_RE.match(v):
            return v

        # URL form: validate host strictly first, then pull the ID from the path.
        # Without the host check, a URL like https://evil.com/spreadsheets/d/<id>/
        # would silently get its `/d/<id>/` substring matched and accepted.
        if v.lower().startswith(("http://", "https://")):
            parsed = urlparse(v)
            host = (parsed.hostname or "").lower()
            if host != "docs.google.com":
                raise PluginError(
                    f"Cannot extract spreadsheet ID from {v!r}. "
                    "URL host must be docs.google.com."
                )
            m = _URL_SHEET_ID_RE.search(parsed.path)
            if m:
                return m.group(1)

        raise PluginError(
            f"Cannot extract spreadsheet ID from {v!r}. "
            "Expected a docs.google.com/spreadsheets/d/<ID>/... URL or the ID itself."
        )

    @staticmethod
    def _list_tab_names(spreadsheet: Any) -> list[str]:
        try:
            return [ws.title for ws in spreadsheet.worksheets()]
        except Exception:
            return []

    @staticmethod
    def _raise_friendly_open_error(e: Exception, sheet_id: str) -> None:
        msg = str(e)
        # gspread raises APIError; check for common substrings.
        if "404" in msg or "not found" in msg.lower():
            raise PluginError(
                f"Spreadsheet {sheet_id!r} not found. Check the URL/ID is correct."
            ) from e
        if "403" in msg or "permission" in msg.lower() or "denied" in msg.lower():
            raise AuthError(
                f"Service account doesn't have access to spreadsheet {sheet_id!r}. "
                "Share the sheet with the service account email."
            ) from e
        raise PluginError(f"Cannot open spreadsheet: {e}") from e
