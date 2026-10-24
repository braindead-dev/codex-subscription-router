import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import repair_history as repair


class RepairHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.thread = "01a12eaa-7e7d-7253-b7d6-f1bae2faf00e"
        self.stream = "01a15d0f-7b34-7540-add7-76c9351b1ace"
        self.file = self.home / "sessions/2026/10/20" / f"rollout-{self.thread}_{self.stream}.jsonl"
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"type":"session_meta"}\n')
        with repair.connect(self.home / "thread_history_1.sqlite", writable=True) as db:
            for table in repair.TABLES:
                extra = ", next_rollout_byte_offset integer" if table == "thread_history_projection_state" else ", payload text"
                db.execute(f"create table {table} (thread_id text primary key{extra})")
                value = self.file.stat().st_size + 10 if table == "thread_history_projection_state" else "keep"
                db.execute(f"insert into {table} values (?,?)", (self.stream, value))
                db.execute(f"insert into {table} values (?,?)", (self.thread, value))
        sqlite3.connect(self.home / "state_5.sqlite").close()

    def test_dry_run_never_changes_files_or_database(self):
        before = (self.home / "thread_history_1.sqlite").read_bytes()
        rows = repair.inspect(self.home, self.thread)
        self.assertEqual([row["stream"] for row in rows], [self.stream])
        self.assertEqual((self.home / "thread_history_1.sqlite").read_bytes(), before)
        self.assertFalse((self.home / "router-repair-backups").exists())

    def test_busy_account_is_rejected_before_any_write(self):
        with mock.patch.object(repair.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="123\n")):
            with self.assertRaisesRegex(RuntimeError, "in use"):
                repair.repair(self.home, self.thread, Path("codex"))
        self.assertFalse((self.home / "router-repair-backups").exists())

    def test_repair_backs_up_and_only_rebuilds_the_affected_stream(self):
        content = self.file.read_bytes()

        def rebuild(home, thread, codex):
            with repair.connect(home / "thread_history_1.sqlite", writable=True) as db:
                for table in repair.TABLES:
                    self.assertEqual(db.execute(f"select count(*) from {table} where thread_id=?", (self.stream,)).fetchone()[0], 0)
                    self.assertEqual(db.execute(f"select count(*) from {table} where thread_id=?", (self.thread,)).fetchone()[0], 1)
                db.execute("insert into thread_history_projection_state values (?,?)", (self.stream, self.file.stat().st_size))

        with mock.patch.object(repair, "require_closed"), mock.patch.object(repair, "reproject", side_effect=rebuild):
            backup = repair.repair(self.home, self.thread, Path("codex"))
        with repair.connect(backup / "thread_history_1.sqlite") as db:
            for table in repair.TABLES:
                self.assertEqual(db.execute(f"select count(*) from {table}").fetchone()[0], 2)
        self.assertEqual(self.file.read_bytes(), content)
        self.assertEqual(json.loads((backup / "plan.json").read_text())["thread"], self.thread)
        self.assertEqual(repair.inspect(self.home, self.thread), [])

    def test_failed_native_rebuild_reports_a_preserved_backup(self):
        with mock.patch.object(repair, "require_closed"), mock.patch.object(repair, "reproject", side_effect=RuntimeError("native failure")):
            with self.assertRaisesRegex(RuntimeError, "preserved backup"):
                repair.repair(self.home, self.thread, Path("codex"))
        self.assertEqual(len(list((self.home / "router-repair-backups").glob("*/thread_history_1.sqlite"))), 1)


if __name__ == "__main__":
    unittest.main()
