"""Read-only container health check for an existing Dev Autopilot database."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from dev_autopilot.db import SCHEMA_VERSION


def check_database(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, f"database does not exist: {path}"
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        return False, f"database unreadable: {exc}"
    if row is None or row[0] is None:
        return False, "database has no schema version"
    if int(row[0]) > SCHEMA_VERSION:
        return False, f"unsupported schema version: {row[0]}"
    if integrity != ("ok",):
        return False, "database integrity check failed"
    return True, f"healthy schema_version={row[0]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    healthy, detail = check_database(args.db)
    print(("OK " if healthy else "FAIL ") + detail)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
