import json
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

SCHEMA = '''
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, mode TEXT NOT NULL, source TEXT NOT NULL,
 status TEXT NOT NULL, image_count INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
 completed_at TEXT, result_json TEXT, raw_result TEXT, error TEXT, elapsed_ms INTEGER
);
CREATE TABLE IF NOT EXISTS images (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL, size INTEGER NOT NULL, width INTEGER NOT NULL,
 height INTEGER NOT NULL, mime_type TEXT NOT NULL,
 UNIQUE(task_id, sequence)
);
CREATE INDEX IF NOT EXISTS tasks_created ON tasks(created_at DESC);
'''


class Database:
    def __init__(self, path: Path):
        self.path = path

    @asynccontextmanager
    async def connect(self):
        async with aiosqlite.connect(self.path, timeout=10) as db:
            db.row_factory = aiosqlite.Row
            await db.execute('PRAGMA foreign_keys=ON')
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def initialize(self):
        async with self.connect() as db:
            await db.execute('PRAGMA journal_mode=WAL')
            await db.executescript(SCHEMA)

    async def rows(self, sql: str, parameters=()) -> list[dict]:
        async with self.connect() as db:
            async with db.execute(sql, parameters) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def execute(self, sql: str, parameters=()) -> None:
        async with self.connect() as db:
            await db.execute(sql, parameters)


def public_task(row: dict) -> dict:
    result = dict(row)
    result['result'] = json.loads(result.pop('result_json')) if result.get('result_json') else None
    result.pop('result_json', None)
    return result
