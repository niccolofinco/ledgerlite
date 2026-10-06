"""SQLite-specific behaviour: durability, schema-level integrity, concurrency."""

import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import date

from ledgerlite.domain.errors import ConflictError
from ledgerlite.domain.models import AccountType
from ledgerlite.repositories import SqliteRepository
from ledgerlite.services import LedgerService

from tests.helpers import NOW, credit, debit, record_simple, seed_chart
from tests.integration.test_repository_contract import account, transaction


class TempDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "ledger.db")

    def open_repo(self):
        repo = SqliteRepository(self.path)
        self.addCleanup(repo.close)
        return repo


class DurabilityTest(TempDatabaseTestCase):
    def test_data_survives_reopening_the_database(self):
        repo = self.open_repo()
        service = LedgerService(repo, clock=lambda: NOW)
        accounts = seed_chart(service)
        tx = record_simple(service, accounts, "CASH", "SALES", 4200, day=date(2026, 9, 1)).transaction
        repo.close()

        reopened = LedgerService(self.open_repo(), clock=lambda: NOW)
        self.assertEqual(reopened.get_transaction(tx.id), tx)
        self.assertEqual(reopened.get_balance(accounts["CASH"].id).balance, 4200)
        self.assertEqual(len(reopened.list_accounts()), 6)

    def test_schema_creation_is_idempotent(self):
        self.open_repo().add_account(account("CASH"))
        self.assertEqual(len(self.open_repo().list_accounts()), 1)


class AtomicityTest(TempDatabaseTestCase):
    def test_failed_insert_rolls_back_the_whole_transaction(self):
        repo = self.open_repo()
        repo.add_account(account("CASH"))
        # the second posting references an unknown account: the FK fails *after*
        # the transaction row and the first posting were inserted
        with self.assertRaises(ConflictError):
            repo.add_transaction(transaction("t1", date(2026, 9, 1), [("id-CASH", 5), ("ghost", -5)]))
        raw = sqlite3.connect(self.path)
        self.addCleanup(raw.close)
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM postings").fetchone()[0], 0)

    def test_repository_remains_usable_after_a_failure(self):
        repo = self.open_repo()
        repo.add_account(account("CASH"))
        repo.add_account(account("SALES", AccountType.INCOME))
        with self.assertRaises(ConflictError):
            repo.add_transaction(transaction("t1", date(2026, 9, 1), [("id-CASH", 5), ("ghost", -5)]))
        repo.add_transaction(transaction("t1", date(2026, 9, 1), [("id-CASH", 5), ("id-SALES", -5)]))
        self.assertEqual(repo.balance_of("id-CASH"), 5)


class SchemaConstraintsTest(TempDatabaseTestCase):
    """The schema defends the data even if the application layer is bypassed."""

    def setUp(self):
        super().setUp()
        repo = self.open_repo()
        repo.add_account(account("CASH"))
        repo.close()
        self.raw = sqlite3.connect(self.path)
        self.raw.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(self.raw.close)

    def test_zero_amount_is_rejected(self):
        self.raw.execute("INSERT INTO transactions (id, date, description, created_at) VALUES ('t', '2026-01-01', 'd', 'x')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw.execute(
                "INSERT INTO postings (transaction_seq, position, account_id, amount) VALUES (1, 0, 'id-CASH', 0)"
            )

    def test_invalid_account_type_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw.execute("INSERT INTO accounts (id, code, name, type) VALUES ('x', 'X', 'X', 'bogus')")

    def test_duplicate_account_code_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw.execute("INSERT INTO accounts (id, code, name, type) VALUES ('x', 'CASH', 'X', 'asset')")


class ConcurrencyTest(TempDatabaseTestCase):
    def test_parallel_writers_on_separate_connections_keep_the_ledger_consistent(self):
        setup_repo = self.open_repo()
        service = LedgerService(setup_repo, clock=lambda: NOW)
        accounts = seed_chart(service)
        workers, per_worker = 4, 15
        errors = []

        def work(worker_index):
            try:
                svc = LedgerService(
                    self.open_repo(), clock=lambda: NOW, id_factory=lambda: f"w{worker_index}-{os.urandom(6).hex()}"
                )
                for _ in range(per_worker):
                    svc.record_transaction(
                        date(2026, 9, 1), "parallel", [debit(accounts["CASH"], 100), credit(accounts["SALES"], 100)]
                    )
            except Exception as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        total = workers * per_worker * 100
        self.assertEqual(service.get_balance(accounts["CASH"].id).balance, total)
        report = service.get_trial_balance()
        self.assertTrue(report.balanced)
        self.assertEqual(report.total_debit, total)

    def test_concurrent_replays_of_the_same_idempotency_key_create_one_transaction(self):
        repo = self.open_repo()
        service = LedgerService(repo, clock=lambda: NOW)
        accounts = seed_chart(service)
        outcomes = []

        def work(index):
            svc = LedgerService(self.open_repo(), clock=lambda: NOW, id_factory=lambda: f"r{index}-{os.urandom(6).hex()}")
            try:
                result = svc.record_transaction(
                    date(2026, 9, 1), "once", [debit(accounts["CASH"], 100), credit(accounts["SALES"], 100)],
                    idempotency_key="same-key",
                )
                outcomes.append("created" if result.created else "replayed")
            except ConflictError:
                outcomes.append("conflict")  # lost the race: the UNIQUE constraint protected us

        threads = [threading.Thread(target=work, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(outcomes.count("created"), 1)
        self.assertEqual(len(service.list_transactions()), 1)
        self.assertEqual(service.get_balance(accounts["CASH"].id).balance, 100)
