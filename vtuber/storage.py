"""SQLite 對話工作階段、歷史與存檔。"""
from contextlib import closing
import json
import sqlite3


class StorageMixin:
    """SQLite 對話工作階段、歷史與存檔。"""

    def init_database(self):
        """每次操作建立獨立連線，供主執行緒與 AI 執行緒使用。時間以 UTC 儲存。"""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS chat_sessions ('
                       'id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT '
                       "(strftime('%Y-%m-%dT%H:%M:%fZ','now')))")
            db.execute('CREATE TABLE IF NOT EXISTS chat_turns ('
                       'id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, '
                       'speaker TEXT NOT NULL, user_text TEXT NOT NULL, assistant_text TEXT NOT NULL, '
                       "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), "
                       'FOREIGN KEY(session_id) REFERENCES chat_sessions(id))')
            db.execute('CREATE INDEX IF NOT EXISTS chat_turns_session ON chat_turns(session_id, id)')
            row = db.execute('SELECT id FROM chat_sessions ORDER BY id DESC LIMIT 1').fetchone()
            self.session_id = row[0] if row else db.execute('INSERT INTO chat_sessions DEFAULT VALUES').lastrowid
            rows = db.execute(
                'SELECT speaker, user_text, assistant_text FROM chat_turns '
                'WHERE session_id = ? ORDER BY id DESC LIMIT ?',
                (self.session_id, max(0, self.history_turns)),
            ).fetchall()
        self.history = []
        for speaker, text, reply in reversed(rows):
            self.history.extend([
                {'role': 'user', 'content': json.dumps({'speaker': speaker, 'text': text}, ensure_ascii=False)},
                {'role': 'assistant', 'content': reply},
            ])

    def save_chat_turn(self, speaker, text, reply):
        """一筆交易儲存完整問答，即使後續 TTS 失敗仍保留對話。"""
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute(
                'INSERT INTO chat_turns(session_id, speaker, user_text, assistant_text) VALUES (?, ?, ?, ?)',
                (self.session_id, speaker, text, reply),
            )

    def clear_history(self):
        """開始新對話，保留資料庫中的舊紀錄；重啟後也不恢復舊對話。"""
        with closing(sqlite3.connect(self.database_path)) as db, db:
            session_id = db.execute('INSERT INTO chat_sessions DEFAULT VALUES').lastrowid
        self.session_id = session_id
        self.history.clear()
