"""Focused SQLite tests for balance and manually configured FX rates."""

import os
from datetime import datetime, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import database


class FinanceDatabaseTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "finance_test.sqlite3")
        self.database_path_patch = patch.dict(os.environ, {"DATABASE_PATH": self.db_path})
        self.database_path_patch.start()
        database.initialize_database()
        self.occurred_at = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.database_path_patch.stop()
        self.temp_dir.cleanup()

    def add(self, user_id: int, kind: str, amount: str, currency: str) -> None:
        database.add_transaction(
            telegram_user_id=user_id,
            kind=kind,
            amount=amount,
            currency=currency,
            category="test",
            description="synthetic test entry",
            occurred_at=self.occurred_at,
        )

    def test_empty_balance_starts_at_zero_in_both_currencies(self) -> None:
        totals = database.get_balance_totals(1001)

        self.assertEqual(Decimal(totals["UZS"]), Decimal("0"))
        self.assertEqual(Decimal(totals["USD"]), Decimal("0"))

    def test_net_balance_is_calculated_separately_by_currency(self) -> None:
        self.add(1001, "income", "1000000", "UZS")
        self.add(1001, "expense", "225000", "UZS")
        self.add(1001, "income", "100.25", "USD")
        self.add(1001, "expense", "21.10", "USD")

        totals = database.get_balance_totals(1001)

        self.assertEqual(Decimal(totals["UZS"]), Decimal("775000"))
        self.assertEqual(Decimal(totals["USD"]), Decimal("79.15"))

    def test_balance_is_isolated_between_users(self) -> None:
        self.add(1001, "income", "500000", "UZS")
        self.add(1002, "expense", "120000", "UZS")
        database.set_usd_to_uzs_rate(1001, "12950")
        database.set_usd_to_uzs_rate(1002, "13000")

        user_one_balance = database.get_balance_totals(1001)
        user_two_balance = database.get_balance_totals(1002)

        self.assertEqual(Decimal(user_one_balance["UZS"]), Decimal("500000"))
        self.assertEqual(Decimal(user_two_balance["UZS"]), Decimal("-120000"))
        self.assertEqual(database.get_usd_to_uzs_rate(1001), "12950")
        self.assertEqual(database.get_usd_to_uzs_rate(1002), "13000")

    def test_rate_is_missing_until_user_sets_one(self) -> None:
        self.assertIsNone(database.get_usd_to_uzs_rate(1001))

    def test_rate_is_stored_exactly_as_user_provided(self) -> None:
        database.set_usd_to_uzs_rate(1001, "12950.125")

        self.assertEqual(database.get_usd_to_uzs_rate(1001), "12950.125")

    def test_rate_rejects_non_positive_or_invalid_values(self) -> None:
        for invalid_rate in ("0", "-1", "not-a-rate", "NaN", "Infinity", "-Infinity"):
            with self.subTest(rate=invalid_rate):
                with self.assertRaises(ValueError):
                    database.set_usd_to_uzs_rate(1001, invalid_rate)

    def test_transaction_rejects_non_positive_or_invalid_amounts(self) -> None:
        for invalid_amount in ("0", "-1", "not-an-amount", "NaN", "Infinity", "-Infinity"):
            with self.subTest(amount=invalid_amount):
                with self.assertRaises(ValueError):
                    self.add(1001, "expense", invalid_amount, "UZS")

        totals = database.get_balance_totals(1001)
        self.assertEqual(Decimal(totals["UZS"]), Decimal("0"))
        self.assertEqual(Decimal(totals["USD"]), Decimal("0"))

    def test_decimal_balance_does_not_use_binary_float_arithmetic(self) -> None:
        self.add(1001, "income", "9007199254740993.12", "USD")
        self.add(1001, "expense", "9007199254740992.02", "USD")
        self.add(1001, "income", "0.10", "UZS")
        self.add(1001, "income", "0.20", "UZS")
        self.add(1001, "expense", "0.30", "UZS")

        totals = database.get_balance_totals(1001)

        self.assertEqual(Decimal(totals["USD"]), Decimal("1.10"))
        self.assertEqual(Decimal(totals["UZS"]), Decimal("0.00"))

    def test_transaction_listing_is_scoped_and_paginates(self) -> None:
        first_id = database.add_transaction(
            1001, "expense", "10", "UZS", "food", "first", self.occurred_at
        )
        second_id = database.add_transaction(
            1001, "income", "20", "USD", "salary", "second", self.occurred_at
        )
        self.add(1002, "expense", "999", "UZS")

        all_rows = database.list_transactions(1001, limit=50, offset=0)
        page = database.list_transactions(1001, limit=1, offset=1)

        self.assertEqual({row["id"] for row in all_rows}, {first_id, second_id})
        self.assertEqual(len(page), 1)
        self.assertEqual(page[0]["telegram_user_id"], 1001)
        self.assertNotIn(1002, {row["telegram_user_id"] for row in all_rows})

    def test_transaction_read_update_and_delete_require_owner(self) -> None:
        transaction_id = database.add_transaction(
            1001, "expense", "10.00", "UZS", "food", "before", self.occurred_at
        )

        self.assertIsNone(database.get_transaction(1002, transaction_id))
        self.assertFalse(database.update_transaction(
            1002, transaction_id, kind="income", amount="50", currency="USD",
            category="salary", description="unauthorized", occurred_at=self.occurred_at,
        ))
        self.assertFalse(database.delete_transaction(1002, transaction_id))
        still_owned = database.get_transaction(1001, transaction_id)
        self.assertEqual(still_owned["amount"], "10.00")
        self.assertEqual(still_owned["description"], "before")

    def test_transaction_update_normalizes_amount_and_persists_all_fields(self) -> None:
        transaction_id = database.add_transaction(
            1001, "expense", "10", "UZS", "food", "before", self.occurred_at
        )
        new_date = datetime(2026, 11, 22, 8, 30, tzinfo=timezone.utc)

        updated = database.update_transaction(
            1001, transaction_id, kind="income", amount="20.50", currency="USD",
            category="salary", description="corrected", occurred_at=new_date,
        )

        saved = database.get_transaction(1001, transaction_id)
        self.assertTrue(updated)
        self.assertEqual(saved["kind"], "income")
        self.assertEqual(saved["amount"], "20.50")
        self.assertEqual(saved["currency"], "USD")
        self.assertEqual(saved["category"], "salary")
        self.assertEqual(saved["description"], "corrected")
        self.assertIn("2026-11-22", saved["occurred_at"])

    def test_transaction_delete_removes_only_target_record(self) -> None:
        transaction_id = database.add_transaction(
            1001, "expense", "10", "UZS", "food", "remove", self.occurred_at
        )
        remaining_id = database.add_transaction(
            1001, "expense", "20", "UZS", "transport", "keep", self.occurred_at
        )

        self.assertTrue(database.delete_transaction(1001, transaction_id))
        self.assertFalse(database.delete_transaction(1001, transaction_id))
        rows = database.list_transactions(1001, limit=50)
        self.assertEqual([row["id"] for row in rows], [remaining_id])

    def test_transaction_update_rejects_invalid_financial_fields_without_changes(self) -> None:
        transaction_id = database.add_transaction(
            1001, "expense", "10", "UZS", "food", "unchanged", self.occurred_at
        )
        invalid_updates = [
            {"kind": "transfer", "amount": "20", "currency": "UZS"},
            {"kind": "income", "amount": "-1", "currency": "UZS"},
            {"kind": "income", "amount": "NaN", "currency": "UZS"},
            {"kind": "income", "amount": "20", "currency": "EUR"},
        ]
        for fields in invalid_updates:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                database.update_transaction(
                    1001, transaction_id, category="salary", description="changed",
                    occurred_at=self.occurred_at, **fields,
                )

        saved = database.get_transaction(1001, transaction_id)
        self.assertEqual(saved["kind"], "expense")
        self.assertEqual(saved["amount"], "10")
        self.assertEqual(saved["currency"], "UZS")
        self.assertEqual(saved["description"], "unchanged")

    def test_transaction_list_rejects_unsafe_pagination_bounds(self) -> None:
        for kwargs in ({"limit": 0}, {"limit": 51}, {"offset": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                database.list_transactions(1001, **kwargs)


if __name__ == "__main__":
    import unittest

    unittest.main()

