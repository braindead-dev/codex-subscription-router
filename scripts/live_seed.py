#!/usr/bin/env python3
"""Build the Codex home the live move test runs against.

    python3 scripts/live_seed.py [--source ~/.codex] [--dest ~/.codex-mux/live-seed]

Copies the source account's login and model provider settings plus one short,
unforked chat (its rollout, index row, and history projection) into DEST, and
prints the environment for `go test -run TestLiveMove ./internal/mux`.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import sqlite3
import tomllib
from pathlib import Path


def pick_thread(home: Path) -> tuple[str, Path]:
    rows = sqlite3.connect(home / "state_5.sqlite").execute(
        "select id, rollout_path from threads where history_mode = 'paginated' "
        "and archived = 0 and model like 'gpt%' order by updated_at desc limit 300"
    )
    candidates = []
    for thread_id, path in rows:
        rollout = Path(path or "")
        if not rollout.is_file() or len(glob.glob(str(rollout.parent / f"*{thread_id}*"))) != 1:
            continue
        meta = json.loads(rollout.open().readline()).get("payload", {})
        if meta.get("history_base") or meta.get("forked_from_id"):
            continue
        if 50_000 < rollout.stat().st_size < 200_000:
            candidates.append((rollout.stat().st_size, thread_id, rollout))
    if not candidates:
        raise SystemExit("no short unforked chat to seed from")
    _, thread_id, rollout = min(candidates)
    return thread_id, rollout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--dest", type=Path, default=Path.home() / ".codex-mux" / "live-seed")
    args = parser.parse_args()
    source, dest = args.source.expanduser(), args.dest.expanduser()

    thread_id, rollout = pick_thread(source)
    shutil.rmtree(dest, ignore_errors=True)
    relative = rollout.relative_to(source)
    (dest / relative).parent.mkdir(parents=True)
    shutil.copy2(rollout, dest / relative)
    for name in ("auth.json", "opencodex-catalog.json"):
        if (source / name).is_file():
            shutil.copy2(source / name, dest / name)
    config = tomllib.loads((source / "config.toml").read_text())
    kept = [
        line
        for line in (source / "config.toml").read_text().splitlines()
        if "=" in line and line.split("=", 1)[0].strip() in config
        and not line.startswith("notify")
        and not isinstance(config[line.split("=", 1)[0].strip()], dict)
    ]
    (dest / "config.toml").write_text("\n".join(kept) + "\n")
    for database in ("state_5.sqlite", "thread_history_1.sqlite"):
        with sqlite3.connect(source / database) as original, sqlite3.connect(dest / database) as copy:
            original.backup(copy)
    with sqlite3.connect(dest / "state_5.sqlite") as state:
        state.execute("delete from threads where id != ?", (thread_id,))
        state.execute("delete from thread_sections")
        state.execute("update threads set rollout_path = ?", (str(dest / relative),))
    with sqlite3.connect(dest / "thread_history_1.sqlite") as history:
        for table in ("thread_turns", "thread_items", "thread_history_projection_state", "thread_realtime_items"):
            history.execute(f"delete from {table} where thread_id != ?", (thread_id,))
    for database in ("state_5.sqlite", "thread_history_1.sqlite"):
        with sqlite3.connect(dest / database, isolation_level=None) as connection:
            connection.execute("vacuum")
    (dest / "tid").write_text(thread_id + "\n")
    print(f"CODEX_MUX_LIVE_HOME={dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
