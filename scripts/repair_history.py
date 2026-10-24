#!/usr/bin/env python3
"""Inspect a chat's stale history index; repair it offline with a backup.

No changes unless --apply is passed. The account must be closed first.
Rollout files are never edited; Codex rebuilds their derived SQLite rows.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import threading
import time

TABLES = ("thread_turns", "thread_items", "thread_history_projection_state", "thread_realtime_items")
UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


@contextmanager
def connect(path: Path, writable=False):
    db = sqlite3.connect(path if writable else path.as_uri() + "?mode=ro", uri=not writable)
    try:
        with db:
            yield db
    finally:
        db.close()


def inspect(home: Path, thread: str) -> list[dict]:
    if not UUID.fullmatch(thread):
        raise ValueError("invalid thread id")
    rows = []
    with connect(home / "thread_history_1.sqlite") as db:
        for path in sorted((home / "sessions").glob(f"*/*/*/*{thread}*.jsonl")):
            link = re.search(r"_([0-9a-f-]{36})\.jsonl$", path.name)
            stream = link[1] if link else thread
            state = db.execute("select next_rollout_byte_offset from thread_history_projection_state where thread_id=?", (stream,)).fetchone()
            info = path.stat()
            if state and state[0] > info.st_size:
                rows.append(dict(stream=stream, path=str(path), size=info.st_size, offset=state[0], mtime=info.st_mtime_ns))
    return rows


def require_closed(home: Path):
    files = [home / name for name in ("state_5.sqlite", "thread_history_1.sqlite")]
    result = subprocess.run(["/usr/sbin/lsof", "-t", *map(str, files)], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("could not check whether the account is in use")
    if result.stdout.strip():
        raise RuntimeError("account databases are in use; close its Codex processes before applying the repair")


def reproject(home: Path, thread: str, codex: Path):
    env = dict(os.environ, CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home))
    with (home / "router-repair-backups" / "reproject.log").open("a") as log:
        process = subprocess.Popen([str(codex), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, env=env)
        messages = queue.Queue()

        def read():
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    pass
            messages.put(None)

        threading.Thread(target=read, daemon=True).start()

        def call(number, method, params):
            process.stdin.write(json.dumps(dict(id=number, method=method, params=params)) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 120
            while True:
                response = messages.get(timeout=max(0, deadline - time.monotonic()))
                if response is None:
                    raise RuntimeError("Codex exited during index rebuild")
                if response.get("id") == number:
                    if "error" in response:
                        raise RuntimeError(response["error"].get("message", "Codex request failed"))
                    return response.get("result")

        try:
            call(1, "initialize", dict(clientInfo=dict(name="router-history-repair", version="1"), capabilities=dict(experimentalApi=True)))
            process.stdin.write('{"method":"initialized"}\n')
            process.stdin.flush()
            # Preparing an ephemeral fork materializes the source index without
            # persisting another chat or starting a model turn.
            call(2, "thread/fork", dict(threadId=thread, ephemeral=True, excludeTurns=True))
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


def repair(home: Path, thread: str, codex: Path) -> Path | None:
    require_closed(home)
    rows = inspect(home, thread)
    if not rows:
        return None
    backup = home / "router-repair-backups" / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup.mkdir(parents=True, mode=0o700)
    with connect(home / "thread_history_1.sqlite") as source, connect(backup / "thread_history_1.sqlite", writable=True) as target:
        source.backup(target)
    os.chmod(backup / "thread_history_1.sqlite", 0o600)
    (backup / "plan.json").write_text(json.dumps(dict(thread=thread, streams=rows), indent=2) + "\n")
    with connect(home / "thread_history_1.sqlite", writable=True) as db:
        db.execute("begin immediate")
        for row in rows:
            info = Path(row["path"]).stat()
            current = db.execute("select next_rollout_byte_offset from thread_history_projection_state where thread_id=?", (row["stream"],)).fetchone()
            if (info.st_size, info.st_mtime_ns) != (row["size"], row["mtime"]) or current != (row["offset"],):
                raise RuntimeError("history changed during repair; no index rows were removed")
            for table in TABLES:
                db.execute(f"delete from {table} where thread_id=?", (row["stream"],))
    try:
        reproject(home, thread, codex)
    except Exception as error:
        raise RuntimeError(f"index rebuild failed; preserved backup: {backup}") from error
    with connect(home / "thread_history_1.sqlite") as db:
        for row in rows:
            current = db.execute("select next_rollout_byte_offset from thread_history_projection_state where thread_id=?", (row["stream"],)).fetchone()
            if current != (Path(row["path"]).stat().st_size,):
                raise RuntimeError(f"Codex did not finish rebuilding the index; backup: {backup}")
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--codex", type=Path, help="native codex.real, required for --apply")
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    if args.apply:
        if not args.codex or not args.codex.is_file():
            parser.error("--apply requires --codex pointing to the native CLI")
        print("backup:", repair(home, args.thread, args.codex.resolve()))
    else:
        print(json.dumps(inspect(home, args.thread), indent=2))


if __name__ == "__main__":
    main()
