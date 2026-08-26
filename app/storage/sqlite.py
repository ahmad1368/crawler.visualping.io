"""SQLite implementation of the storage repository interface.

The database is the durable store for every page fetched, every extracted
`PasswordMatch`, and every raw content snapshot -- all of it sensitive.
The `*.db` file is gitignored (see issue #1); nothing here should ever log
a match's value, a snapshot's content, or otherwise echo them outside the
database itself.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.models import CrawlSummary, PageResult, PasswordMatch
from app.storage.repository import Repository

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    status_code INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_url TEXT,
    value TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    context_before TEXT NOT NULL,
    context_after TEXT NOT NULL,
    locator TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    url TEXT PRIMARY KEY,
    content BLOB NOT NULL
);
"""


class SqliteRepository(Repository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save_page(self, page: PageResult, snapshot: bytes) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO pages (url, status_code, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    status_code = excluded.status_code,
                    fetched_at = excluded.fetched_at
                """,
                (page.url, page.status_code, page.fetched_at.isoformat()),
            )
            self._conn.execute(
                """
                INSERT INTO snapshots (url, content) VALUES (?, ?)
                ON CONFLICT(url) DO UPDATE SET content = excluded.content
                """,
                (page.url, snapshot),
            )
            for match in page.matches:
                self._insert_match(match, page_url=page.url)

    def save_match(self, match: PasswordMatch) -> None:
        with self._conn:
            self._insert_match(match, page_url=None)

    def _insert_match(self, match: PasswordMatch, page_url: str | None) -> None:
        self._conn.execute(
            """
            INSERT INTO matches (
                page_url, value, source_type, source_url,
                context_before, context_after, locator
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_url,
                match.value,
                match.source_type.value,
                match.source_url,
                match.context_before,
                match.context_after,
                match.locator,
            ),
        )

    def get_snapshot(self, url: str) -> bytes | None:
        row = self._conn.execute(
            "SELECT content FROM snapshots WHERE url = ?", (url,)
        ).fetchone()
        return row[0] if row is not None else None

    def get_report(self) -> CrawlSummary:
        pages_visited = self._conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        unique_passwords_found = self._conn.execute(
            "SELECT COUNT(DISTINCT value) FROM matches"
        ).fetchone()[0]
        (started,) = self._conn.execute("SELECT MIN(fetched_at) FROM pages").fetchone()
        (finished,) = self._conn.execute("SELECT MAX(fetched_at) FROM pages").fetchone()

        return CrawlSummary(
            pages_visited=pages_visited,
            resources_checked=pages_visited,
            unique_passwords_found=unique_passwords_found,
            queue_empty=True,
            started_at=datetime.fromisoformat(started) if started else datetime.now(timezone.utc),
            finished_at=datetime.fromisoformat(finished) if finished else None,
        )
