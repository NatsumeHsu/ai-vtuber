"""Compatibility and transaction tests for existing local databases."""
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select, func
from database.engine import Database
from database.models import AudienceMessage, ChatTurn
from database.audience_memory import AudienceMemory
from database.storage import StorageMixin

class DatabaseTests(unittest.TestCase):
    def test_legacy_history_preserves_ids_and_imports_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                CREATE TABLE chat_sessions(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
                CREATE TABLE chat_turns(id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL,
                    speaker TEXT NOT NULL, user_text TEXT NOT NULL, assistant_text TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
                INSERT INTO chat_sessions VALUES(7, '2026-10-01T00:00:00.000Z');
                INSERT INTO chat_turns VALUES(42, 7, '觀眾', '舊訊息', '舊回覆', '2026-10-01T00:01:00.000Z');
                """)
            app = StorageMixin()
            app.database_path, app.history_turns = path, 10
            app.init_database()
            self.assertEqual(app.session_id, 7)
            self.assertEqual(app.history[-1]['content'], '舊回覆')
            AudienceMemory(path, {})
            memory = AudienceMemory(path, {})
            with memory.database.session() as db:
                rows = list(db.scalars(select(AudienceMessage)))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].legacy_turn_id, 42)
                self.assertEqual(rows[0].created_at, '2026-10-01T00:01:00.000Z')
                self.assertEqual(db.get(ChatTurn, 42).user_text, '舊訊息')
            app.save_chat_turn('觀眾', '新訊息', '新回覆')
            app.clear_history()
            self.assertGreater(app.session_id, 7)
            app.init_database()
            self.assertEqual(app.history, [])

    def test_concurrent_duplicate_messages_are_saved_once(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = AudienceMemory(Path(folder) / 'test.sqlite3', {})
            with ThreadPoolExecutor(max_workers=4) as workers:
                ids = list(workers.map(lambda _: memory.record('viewer', '觀眾', '你好', 'twitch',
                    external_id='same-message'), range(8)))
            self.assertEqual(sum(value is not None for value in ids), 1)
            with memory.database.session() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(AudienceMessage)), 1)

    def test_failed_transaction_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / 'test.sqlite3')
            with self.assertRaises(RuntimeError):
                with database.session() as db:
                    db.add(AudienceMessage(viewer_key='viewer', speaker='觀眾', source='local',
                                           user_text='你好', status='received'))
                    db.flush()
                    raise RuntimeError('abort')
            with database.session() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(AudienceMessage)), 0)

if __name__ == '__main__':
    unittest.main()
