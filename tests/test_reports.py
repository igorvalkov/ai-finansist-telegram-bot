"""Tests for private period queries and generated Excel reports."""

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

import database
from report_excel import build_financial_report


class ReportDatabaseTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "reports.sqlite3")
        self.path_patch = patch.dict(os.environ, {"DATABASE_PATH": self.db_path})
        self.path_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def add(self, user_id: int, occurred_at: datetime, description: str) -> None:
        database.add_transaction(
            user_id, "expense", "100.25", "UZS", "groceries", description, occurred_at
        )

    def test_period_query_is_owner_scoped_and_uses_tashkent_calendar_dates(self) -> None:
        local_day_start = datetime(2026, 11, 23, 21, 30, tzinfo=timezone.utc)
        previous_local_day = datetime(2026, 11, 23, 18, 59, tzinfo=timezone.utc)
        self.add(1001, local_day_start, "included at UTC+5 midnight boundary")
        self.add(1001, previous_local_day, "excluded previous local date")
        self.add(1002, local_day_start, "other user's record")

        rows = database.get_transactions_for_period(1001, date(2026, 11, 24), date(2026, 11, 25))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["telegram_user_id"], 1001)
        self.assertEqual(rows[0]["description"], "included at UTC+5 midnight boundary")

    def test_period_query_rejects_invalid_range_and_overflow(self) -> None:
        with self.assertRaises(ValueError):
            database.get_transactions_for_period(1001, date(2026, 11, 25), date(2026, 11, 24))
        self.add(1001, datetime(2026, 11, 24, 9, tzinfo=ZoneInfo("Asia/Tashkent")), "one")
        self.add(1001, datetime(2026, 11, 24, 10, tzinfo=ZoneInfo("Asia/Tashkent")), "two")

        with self.assertRaises(ValueError):
            database.get_transactions_for_period(
                1001, date(2026, 11, 24), date(2026, 11, 25), limit=1
            )


class ExcelReportTests(TestCase):
    def test_workbook_keeps_currencies_separate_and_contains_charts(self) -> None:
        records = [
            {
                "id": 1, "kind": "expense", "amount": "150000.25", "currency": "UZS",
                "category": "groceries", "description": "products",
                "occurred_at": "2026-11-24T09:00:00+05:00",
            },
            {
                "id": 2, "kind": "income", "amount": "200000", "currency": "UZS",
                "category": "salary", "description": "salary",
                "occurred_at": "2026-11-24T10:00:00+05:00",
            },
            {
                "id": 3, "kind": "expense", "amount": "12.50", "currency": "USD",
                "category": "transport", "description": "taxi",
                "occurred_at": "2026-11-24T11:00:00+05:00",
            },
            {
                "id": 4, "kind": "income", "amount": "30.75", "currency": "USD",
                "category": "salary", "description": "wage",
                "occurred_at": "2026-11-24T12:00:00+05:00",
            },
        ]

        output, filename = build_financial_report(
            records, "week", date(2026, 11, 23), date(2026, 11, 30), "ru"
        )
        workbook = load_workbook(BytesIO(output.getvalue()), data_only=False)
        summary = workbook["Сводка"]

        self.assertTrue(filename.endswith(".xlsx"))
        self.assertEqual(summary["B6"].value, "200000")
        self.assertEqual(summary["C6"].value, "150000.25")
        self.assertEqual(summary["D6"].value, "49999.75")
        self.assertEqual(summary["B7"].value, "30.75")
        self.assertEqual(summary["C7"].value, "12.50")
        self.assertEqual(summary["D7"].value, "18.25")
        self.assertEqual(len(summary._charts), 6)
        self.assertEqual(workbook["Операции"].max_row, 5)
        workbook.close()

    def test_formula_like_user_text_is_written_as_literal(self) -> None:
        record = {
            "id": 9, "kind": "expense", "amount": "2.10", "currency": "UZS",
            "category": "=HYPERLINK(\"https://example.invalid\",\"bad\")",
            "description": "=SUM(1,1)",
            "occurred_at": "2026-11-24T09:00:00+05:00",
        }

        output, _ = build_financial_report(
            [record], "month", date(2026, 11, 1), date(2026, 12, 1), "uz"
        )
        workbook = load_workbook(BytesIO(output.getvalue()), data_only=False)
        details = workbook["Operatsiyalar"]

        self.assertEqual(details["G2"].data_type, "s")
        self.assertTrue(details["G2"].value.startswith("'=SUM"))
        self.assertEqual(details["F2"].data_type, "s")
        self.assertTrue(details["F2"].value.startswith("'=HYPERLINK"))
        workbook.close()

    def test_report_rejects_out_of_period_record(self) -> None:
        record = {
            "id": 1, "kind": "expense", "amount": "1", "currency": "UZS",
            "category": "other", "description": "old",
            "occurred_at": "2026-10-31T23:00:00+05:00",
        }

        with self.assertRaises(ValueError):
            build_financial_report(
                [record], "month", date(2026, 11, 1), date(2026, 12, 1), "ru"
            )


if __name__ == "__main__":
    import unittest

    unittest.main()

