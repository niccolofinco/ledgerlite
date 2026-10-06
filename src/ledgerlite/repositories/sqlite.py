"""SQLite repository (standard library only).

Design notes:
* amounts are stored as INTEGER minor units, dates as ISO-8601 text;
* integrity is enforced twice: by the domain objects and by the schema
  (CHECK / UNIQUE / FOREIGN KEY), so a bug in one layer cannot corrupt data;
* ``transactions.reverses`` is UNIQUE: a transaction can be reversed at most once;
* every write runs inside ``BEGIN IMMEDIATE ... COMMIT`` and is rolled back
  entirely on error.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime

from ..domain.errors import ConflictError, NotFoundError
from ..domain.models import Account, AccountType, Posting, Transaction
from .base import StatementEntry

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id     TEXT PRIMARY KEY,
    code   TEXT NOT NULL UNIQUE,
    name   TEXT NOT NULL,
    type   TEXT NOT NULL CHECK (type IN ('asset','liability','equity','income','expense')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS transactions (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    id              TEXT NOT NULL UNIQUE,
    date            TEXT NOT NULL,
    description     TEXT NOT NULL,
    reverses        TEXT UNIQUE REFERENCES transactions(id),
    idempotency_key TEXT UNIQUE,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS postings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_seq INTEGER NOT NULL REFERENCES transactions(seq),
    position        INTEGER NOT NULL,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    amount          INTEGER NOT NULL CHECK (amount <> 0)
);

CREATE INDEX IF NOT EXISTS idx_postings_account ON postings(account_id);
CREATE INDEX IF NOT EXISTS idx_postings_tx ON postings(transaction_seq);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
"""


class SqliteRepository:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        # isolation_level=None -> autocommit; transactions are managed explicitly.
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # ---- accounts -------------------------------------------------------
    def add_account(self, account: Account) -> None:
        try:
            with self._transaction():
                self._conn.execute(
                    "INSERT INTO accounts (id, code, name, type, active) VALUES (?, ?, ?, ?, ?)",
                    (account.id, account.code, account.name, account.type.value, int(account.active)),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"account {account.code!r} already exists") from exc

    def update_account(self, account: Account) -> None:
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE accounts SET name = ?, type = ?, active = ? WHERE id = ?",
                (account.name, account.type.value, int(account.active), account.id),
            )
            if cursor.rowcount == 0:
                raise NotFoundError(f"account {account.id!r} not found")

    def get_account(self, account_id: str) -> Account | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return _account(row) if row else None

    def get_account_by_code(self, code: str) -> Account | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM accounts WHERE code = ?", (code,)).fetchone()
        return _account(row) if row else None

    def list_accounts(self) -> list[Account]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM accounts ORDER BY code").fetchall()
        return [_account(r) for r in rows]

    # ---- transactions ---------------------------------------------------
    def add_transaction(self, transaction: Transaction) -> None:
        try:
            with self._transaction():
                cursor = self._conn.execute(
                    "INSERT INTO transactions (id, date, description, reverses, idempotency_key,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        transaction.id,
                        transaction.date.isoformat(),
                        transaction.description,
                        transaction.reverses,
                        transaction.idempotency_key,
                        transaction.created_at.isoformat(),
                    ),
                )
                self._conn.executemany(
                    "INSERT INTO postings (transaction_seq, position, account_id, amount)"
                    " VALUES (?, ?, ?, ?)",
                    [
                        (cursor.lastrowid, position, p.account_id, p.amount)
                        for position, p in enumerate(transaction.postings)
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(
                "transaction violates a ledger constraint "
                "(duplicate id, idempotency key or reversal, or unknown account)"
            ) from exc

    def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self._fetch_one("WHERE t.id = ?", (transaction_id,))

    def get_transaction_by_idempotency_key(self, key: str) -> Transaction | None:
        return self._fetch_one("WHERE t.idempotency_key = ?", (key,))

    def get_reversal_of(self, transaction_id: str) -> Transaction | None:
        return self._fetch_one("WHERE t.reverses = ?", (transaction_id,))

    def list_transactions(
        self,
        *,
        account_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Transaction]:
        clauses: list[str] = []
        params: list[object] = []
        if account_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM postings p WHERE p.transaction_seq = t.seq"
                " AND p.account_id = ?)"
            )
            params.append(account_id)
        if date_from is not None:
            clauses.append("t.date >= ?")
            params.append(date_from.isoformat())
        if date_to is not None:
            clauses.append("t.date <= ?")
            params.append(date_to.isoformat())
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._fetch_many(f"{where} ORDER BY t.date, t.seq LIMIT ? OFFSET ?", (*params, limit, offset))

    # ---- aggregates -----------------------------------------------------
    def balance_of(self, account_id: str, up_to: date | None = None) -> int:
        sql = (
            "SELECT COALESCE(SUM(p.amount), 0) FROM postings p"
            " JOIN transactions t ON t.seq = p.transaction_seq WHERE p.account_id = ?"
        )
        params: list[object] = [account_id]
        if up_to is not None:
            sql += " AND t.date <= ?"
            params.append(up_to.isoformat())
        with self._lock:
            return int(self._conn.execute(sql, params).fetchone()[0])

    def balances(self, up_to: date | None = None) -> dict[str, int]:
        sql = (
            "SELECT p.account_id, SUM(p.amount) FROM postings p"
            " JOIN transactions t ON t.seq = p.transaction_seq"
        )
        params: list[object] = []
        if up_to is not None:
            sql += " WHERE t.date <= ?"
            params.append(up_to.isoformat())
        sql += " GROUP BY p.account_id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def statement_entries(
        self,
        account_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[StatementEntry]:
        sql = (
            "SELECT t.date, t.id, t.description, p.amount FROM postings p"
            " JOIN transactions t ON t.seq = p.transaction_seq WHERE p.account_id = ?"
        )
        params: list[object] = [account_id]
        if date_from is not None:
            sql += " AND t.date >= ?"
            params.append(date_from.isoformat())
        if date_to is not None:
            sql += " AND t.date <= ?"
            params.append(date_to.isoformat())
        sql += " ORDER BY t.date, t.seq, p.position"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            StatementEntry(date.fromisoformat(r[0]), r[1], r[2], int(r[3])) for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- helpers --------------------------------------------------------
    def _fetch_one(self, where: str, params: tuple) -> Transaction | None:
        found = self._fetch_many(where, params)
        return found[0] if found else None

    def _fetch_many(self, tail: str, params: tuple) -> list[Transaction]:
        with self._lock:
            rows = self._conn.execute(f"SELECT t.* FROM transactions t {tail}", params).fetchall()
            if not rows:
                return []
            placeholders = ",".join("?" * len(rows))
            posting_rows = self._conn.execute(
                "SELECT transaction_seq, account_id, amount FROM postings"
                f" WHERE transaction_seq IN ({placeholders}) ORDER BY transaction_seq, position",
                [r["seq"] for r in rows],
            ).fetchall()
        postings: dict[int, list[Posting]] = {}
        for pr in posting_rows:
            postings.setdefault(pr["transaction_seq"], []).append(
                Posting(pr["account_id"], pr["amount"])
            )
        return [
            Transaction(
                id=r["id"],
                date=date.fromisoformat(r["date"]),
                description=r["description"],
                postings=tuple(postings[r["seq"]]),
                created_at=datetime.fromisoformat(r["created_at"]),
                reverses=r["reverses"],
                idempotency_key=r["idempotency_key"],
            )
            for r in rows
        ]


def _account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        type=AccountType(row["type"]),
        active=bool(row["active"]),
    )
