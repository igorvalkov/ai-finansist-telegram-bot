"""Small SQLite persistence layer for bot users and financial records."""

import os
import sqlite3
from datetime import datetime
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
    """Store a user-provided USD to UZS rate as text to avoid float rounding."""
    with _connect() as connection:
        connection.execute(
            "INSERT INTO users (telegram_user_id, usd_to_uzs_rate) VALUES (?, ?) "
            "ON CONFLICT (telegram_user_id) DO UPDATE "
            "SET usd_to_uzs_rate = excluded.usd_to_uzs_rate",
            (telegram_user_id, rate),
        )


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
    ensure_user(telegram_user_id)
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO transactions "
            "(telegram_user_id, kind, amount, currency, category, description, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                telegram_user_id,
                kind,
                amount,
                currency,
                category,
                description,
                occurred_at.isoformat(timespec="seconds"),
            ),
        )
        return int(cursor.lastrowid)

