"""Small SQLite persistence layer for bot users and financial records."""

import os
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(__file__).parent / "data" / "finance_bot.sqlite3"


def _db_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", str(DEFAULT_DB_PATH)))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    """Create the initial schema if this is a fresh local database."""
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_user_id INTEGER PRIMARY KEY,
                language TEXT CHECK (language IN ('ru', 'uz')),
                usd_to_uzs_rate TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
                amount TEXT NOT NULL,
                currency TEXT NOT NULL CHECK (currency IN ('UZS', 'USD')),
                category TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                occurred_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_user_id)
                    REFERENCES users (telegram_user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_user_date
                ON transactions (telegram_user_id, occurred_at);
            """
        )


def ensure_user(telegram_user_id: int) -> dict[str, Any]:
    with _connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)",
            (telegram_user_id,),
        )
        row = connection.execute(
            "SELECT telegram_user_id, language, usd_to_uzs_rate FROM users "
            "WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
    return dict(row)


def get_language(telegram_user_id: int) -> str | None:
    user = ensure_user(telegram_user_id)
    return user["language"]


def set_language(telegram_user_id: int, language: str) -> None:
    if language not in {"ru", "uz"}:
        raise ValueError("Unsupported language")
    with _connect() as connection:
        connection.execute(
            "INSERT INTO users (telegram_user_id, language) VALUES (?, ?) "
            "ON CONFLICT (telegram_user_id) DO UPDATE SET language = excluded.language",
            (telegram_user_id, language),
        )


def set_usd_to_uzs_rate(telegram_user_id: int, rate: str) -> None:
    """Store a positive user-provided rate as text to avoid float rounding."""
    parsed_rate = _parse_positive_decimal(rate, "exchange rate")
    normalized_rate = format(parsed_rate, "f")
    with _connect() as connection:
        connection.execute(
            "INSERT INTO users (telegram_user_id, usd_to_uzs_rate) VALUES (?, ?) "
            "ON CONFLICT (telegram_user_id) DO UPDATE "
            "SET usd_to_uzs_rate = excluded.usd_to_uzs_rate",
            (telegram_user_id, normalized_rate),
        )


def get_usd_to_uzs_rate(telegram_user_id: int) -> str | None:
    """Return this user's stored USD-to-UZS rate, if one has been set."""
    rate = ensure_user(telegram_user_id)["usd_to_uzs_rate"]
    if rate is None:
        return None
    parsed_rate = _parse_positive_decimal(rate, "stored exchange rate")
    return format(parsed_rate, "f")


def get_balance_totals(telegram_user_id: int) -> dict[str, str]:
    """Return net balances per currency without using SQLite floating sums."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT kind, currency, amount FROM transactions "
            "WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchall()

    balances = {"UZS": Decimal("0"), "USD": Decimal("0")}
    amounts: list[tuple[str, str, Decimal]] = []
    max_adjusted = 0
    min_exponent = 0
    for row in rows:
        kind = row["kind"]
        currency = row["currency"]
        if kind not in {"income", "expense"} or currency not in balances:
            raise ValueError("Stored transaction has an invalid kind or currency")
        amount = _parse_positive_decimal(row["amount"], "stored transaction amount")
        max_adjusted = max(max_adjusted, amount.adjusted())
        min_exponent = min(min_exponent, amount.as_tuple().exponent)
        amounts.append((kind, currency, amount))

    # Decimal arithmetic obeys the active context. Widen it based on the
    # largest stored amount and row count so totals remain exact as records grow.
    precision = max(28, max_adjusted - min_exponent + 1 + len(str(len(amounts))) + 2)
    with localcontext() as context:
        context.prec = precision
        for kind, currency, amount in amounts:
            balances[currency] += amount if kind == "income" else -amount

    return {currency: format(balance, "f") for currency, balance in balances.items()}


def _parse_positive_decimal(value: str, label: str) -> Decimal:
    """Parse bounded plain decimal text and reject unsafe/malformed values."""
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f"Invalid {label}")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"Invalid {label}")
    parts = parsed.as_tuple()
    if parsed.adjusted() > 18 or parts.exponent < -18 or parts.exponent > 18:
        raise ValueError(f"Invalid {label}")
    return parsed


def add_transaction(
    telegram_user_id: int,
    kind: str,
    amount: str,
    currency: str,
    category: str,
    description: str,
    occurred_at: datetime,
) -> int:
    """Save a confirmed transaction and return its database ID."""
    if kind not in {"income", "expense"}:
        raise ValueError("Unsupported transaction kind")
    if currency not in {"UZS", "USD"}:
        raise ValueError("Unsupported currency")
    parsed_amount = _parse_positive_decimal(amount, "transaction amount")
    normalized_amount = format(parsed_amount, "f")
    ensure_user(telegram_user_id)
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO transactions "
            "(telegram_user_id, kind, amount, currency, category, description, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                telegram_user_id,
                kind,
                normalized_amount,
                currency,
                category,
                description,
                occurred_at.isoformat(timespec="seconds"),
            ),
        )
        return int(cursor.lastrowid)

