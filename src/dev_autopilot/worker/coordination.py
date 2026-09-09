"""Single-host transaction coordination for durable worker queue mutations."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SQLiteCoordinator:
    """A cross-process, re-entrant mutex backed by SQLite ``BEGIN IMMEDIATE``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS coordinator_lock (id INTEGER PRIMARY KEY CHECK (id = 1))")
            connection.execute("INSERT OR IGNORE INTO coordinator_lock(id) VALUES (1)")
        self._local = threading.local()

    @contextmanager
    def lock(self) -> Iterator[None]:
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth = depth
            return
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._local.depth = 1
            yield
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._local.depth = 0
            connection.close()
