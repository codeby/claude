"""Tests for content_parser.loaders.gsheets — URL parsing, auth, value extraction."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from content_parser.core.errors import AuthError, PluginError
from content_parser.loaders.gsheets import GoogleSheetsLoader, LoadedRange


VALID_CREDS = {
    "type": "service_account",
    "client_email": "bot@project.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    "project_id": "test-project",
}


def _patched_client():
    """Patch the gspread.authorize + Credentials so __init__ doesn't try real auth."""
    return patch.multiple(
        "content_parser.loaders.gsheets",
        # We patch _build_client itself to skip the import path
    )


class CredentialsValidationTest(unittest.TestCase):
    def test_invalid_json_string_raises_auth_error(self):
        with self.assertRaises(AuthError):
            GoogleSheetsLoader("not-json-at-all")

    def test_non_dict_raises_auth_error(self):
        with self.assertRaises(AuthError):
            GoogleSheetsLoader("[]")

    def test_missing_required_field(self):
        bad = dict(VALID_CREDS)
        del bad["private_key"]
        with self.assertRaises(AuthError) as cm:
            GoogleSheetsLoader(json.dumps(bad))
        self.assertIn("private_key", str(cm.exception))

    def test_oauth_client_json_rejected(self):
        # OAuth client credentials have type=authorized_user, not service_account.
        # We refuse them with a clear message instead of confusing field-missing errors.
        oauth_client = {
            "type": "authorized_user",
            "client_id": "...",
            "client_secret": "...",
            "refresh_token": "...",
        }
        with self.assertRaises(AuthError) as cm:
            GoogleSheetsLoader(json.dumps(oauth_client))
        self.assertIn("service account", str(cm.exception).lower())

    def test_from_secrets_missing_token(self):
        with self.assertRaises(AuthError):
            GoogleSheetsLoader.from_secrets({})

    def test_accepts_dict_directly(self):
        with patch.object(GoogleSheetsLoader, "_build_client", return_value=MagicMock()):
            loader = GoogleSheetsLoader(VALID_CREDS)
            self.assertEqual(
                loader.service_account_email(),
                "bot@project.iam.gserviceaccount.com",
            )

    def test_accepts_json_string(self):
        with patch.object(GoogleSheetsLoader, "_build_client", return_value=MagicMock()):
            loader = GoogleSheetsLoader(json.dumps(VALID_CREDS))
            self.assertEqual(
                loader.service_account_email(),
                "bot@project.iam.gserviceaccount.com",
            )

    def test_validate_credentials_does_not_build_client(self):
        # Crucial property for the UI: we want to validate freshly-pasted JSON
        # before saving, without touching the network. _build_client must not run.
        with patch.object(GoogleSheetsLoader, "_build_client") as bc:
            parsed = GoogleSheetsLoader.validate_credentials(json.dumps(VALID_CREDS))
            bc.assert_not_called()
            self.assertEqual(parsed["client_email"], VALID_CREDS["client_email"])


class SheetIdExtractionTest(unittest.TestCase):
    def test_bare_id(self):
        bare_id = "1AbC2DeFG_HiJkLmNoPqRsTuVwXyZ-1234567890"
        self.assertEqual(GoogleSheetsLoader._extract_sheet_id(bare_id), bare_id)

    def test_full_url(self):
        sheet_id = "1AbC2DeFG_HiJkLmNoPqRsTuVwXyZ-1234567890"
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid=0"
        self.assertEqual(GoogleSheetsLoader._extract_sheet_id(url), sheet_id)

    def test_url_with_extra_path(self):
        sheet_id = "1AbC2DeFG_HiJkLmNoPqRsTuVwXyZ-1234567890"
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit?usp=sharing"
        self.assertEqual(GoogleSheetsLoader._extract_sheet_id(url), sheet_id)

    def test_empty_raises(self):
        with self.assertRaises(PluginError):
            GoogleSheetsLoader._extract_sheet_id("")

    def test_garbage_raises(self):
        with self.assertRaises(PluginError):
            GoogleSheetsLoader._extract_sheet_id("not-a-url-or-id")

    def test_non_google_host_rejected(self):
        # Critical: a URL with /d/<id>/ on a non-google host must NOT be accepted.
        # The previous version silently extracted the ID, which was confusing.
        sheet_id = "1AbC2DeFG_HiJkLmNoPqRsTuVwXyZ-1234567890"
        with self.assertRaises(PluginError) as cm:
            GoogleSheetsLoader._extract_sheet_id(
                f"https://evil.example/spreadsheets/d/{sheet_id}/edit"
            )
        self.assertIn("docs.google.com", str(cm.exception).lower())

    def test_lookalike_host_rejected(self):
        sheet_id = "1AbC2DeFG_HiJkLmNoPqRsTuVwXyZ-1234567890"
        with self.assertRaises(PluginError):
            GoogleSheetsLoader._extract_sheet_id(
                f"https://evildocs.google.com.attacker.com/spreadsheets/d/{sheet_id}/edit"
            )

    def test_other_google_subdomain_rejected(self):
        # mail.google.com /spreadsheets/d/... shouldn't sneak through either.
        sheet_id = "1AbC2DeFG_HiJkLmNoPqRsTuVwXyZ-1234567890"
        with self.assertRaises(PluginError):
            GoogleSheetsLoader._extract_sheet_id(
                f"https://mail.google.com/spreadsheets/d/{sheet_id}/"
            )


class LoadTest(unittest.TestCase):
    """Mock gspread to verify load() behavior end-to-end."""

    def _build_loader(self):
        gc = MagicMock()
        with patch.object(GoogleSheetsLoader, "_build_client", return_value=gc):
            loader = GoogleSheetsLoader(VALID_CREDS)
        return loader, gc

    def _wire_worksheet(self, gc, *, sheet_title="My Sheet", tab_title="Communities", rows=None):
        worksheet = MagicMock()
        worksheet.title = tab_title
        worksheet.get.return_value = rows or []

        spreadsheet = MagicMock()
        spreadsheet.title = sheet_title
        spreadsheet.worksheet.return_value = worksheet
        spreadsheet.sheet1 = worksheet
        spreadsheet.worksheets.return_value = [worksheet]

        gc.open_by_key.return_value = spreadsheet
        return spreadsheet, worksheet

    def test_basic_load_returns_flat_values(self):
        loader, gc = self._build_loader()
        self._wire_worksheet(gc, rows=[
            ["durov_says"],
            ["telegram"],
            ["awesome_community"],
        ])

        result = loader.load(
            "1AbC2DeFG_HiJkLmNoPqRsTuVwXyZ-1234567890",
            tab="Communities",
            range_a1="A:A",
        )
        self.assertIsInstance(result, LoadedRange)
        self.assertEqual(result.values, ["durov_says", "telegram", "awesome_community"])
        self.assertEqual(result.count, 3)
        self.assertEqual(result.sheet_title, "My Sheet")
        self.assertEqual(result.tab_title, "Communities")

    def test_skip_header(self):
        loader, gc = self._build_loader()
        self._wire_worksheet(gc, rows=[
            ["Channel name"],     # header
            ["durov"],
            ["telegram"],
        ])
        result = loader.load("ID" * 10, range_a1="A:A", skip_header=True)
        self.assertEqual(result.values, ["durov", "telegram"])

    def test_dedup_within_range(self):
        loader, gc = self._build_loader()
        self._wire_worksheet(gc, rows=[
            ["durov"],
            ["telegram"],
            ["durov"],     # duplicate
            ["    "],      # blank — dropped
            ["new_chan"],
        ])
        result = loader.load("ID" * 10, range_a1="A:A")
        self.assertEqual(result.values, ["durov", "telegram", "new_chan"])

    def test_multi_column_range_flattens(self):
        loader, gc = self._build_loader()
        self._wire_worksheet(gc, rows=[
            ["durov", "extra1"],
            ["telegram", ""],
            ["", "extra3"],
        ])
        result = loader.load("ID" * 10, range_a1="A:B")
        # Cells flattened in row-major order, deduped
        self.assertEqual(result.values, ["durov", "extra1", "telegram", "extra3"])

    def test_invalid_range_rejected(self):
        loader, gc = self._build_loader()
        with self.assertRaises(PluginError):
            loader.load("ID" * 10, range_a1="not a range!")

    def test_tab_not_found_lists_available(self):
        loader, gc = self._build_loader()
        spreadsheet, worksheet = self._wire_worksheet(gc, tab_title="ActualTab")
        spreadsheet.worksheet.side_effect = Exception("WorksheetNotFound")
        # worksheets() still works for diagnostic
        spreadsheet.worksheets.return_value = [
            MagicMock(title="ActualTab"),
            MagicMock(title="Other"),
        ]

        with self.assertRaises(PluginError) as cm:
            loader.load("ID" * 10, tab="WrongName", range_a1="A:A")
        self.assertIn("WrongName", str(cm.exception))
        self.assertIn("ActualTab", str(cm.exception))

    def test_403_maps_to_auth_error(self):
        loader, gc = self._build_loader()
        gc.open_by_key.side_effect = Exception("403 permission denied")
        with self.assertRaises(AuthError) as cm:
            loader.load("ID" * 10, range_a1="A:A")
        self.assertIn("Share the sheet", str(cm.exception))

    def test_404_maps_to_plugin_error(self):
        loader, gc = self._build_loader()
        gc.open_by_key.side_effect = Exception("404 not found")
        with self.assertRaises(PluginError) as cm:
            loader.load("ID" * 10, range_a1="A:A")
        self.assertIn("not found", str(cm.exception).lower())

    def test_default_tab_is_first_sheet(self):
        loader, gc = self._build_loader()
        spreadsheet, worksheet = self._wire_worksheet(gc, rows=[["x"]])
        loader.load("ID" * 10, range_a1="A:A")
        # We didn't pass tab=, so it should NOT call .worksheet(), only access .sheet1
        spreadsheet.worksheet.assert_not_called()

    def test_empty_string_tab_falls_back_to_first_sheet(self):
        # Cron configs may pass tab="" rather than tab=None.
        loader, gc = self._build_loader()
        spreadsheet, worksheet = self._wire_worksheet(gc, rows=[["x"]])
        loader.load("ID" * 10, tab="", range_a1="A:A")
        spreadsheet.worksheet.assert_not_called()

    def test_whitespace_tab_falls_back(self):
        loader, gc = self._build_loader()
        spreadsheet, worksheet = self._wire_worksheet(gc, rows=[["x"]])
        loader.load("ID" * 10, tab="   ", range_a1="A:A")
        spreadsheet.worksheet.assert_not_called()

    def test_loaded_range_has_no_raw_rows_attr(self):
        # raw_rows was dropped — make sure nothing accidentally re-adds it.
        loader, gc = self._build_loader()
        self._wire_worksheet(gc, rows=[["x"]])
        result = loader.load("ID" * 10, range_a1="A:A")
        self.assertFalse(hasattr(result, "raw_rows"))


if __name__ == "__main__":
    unittest.main()
