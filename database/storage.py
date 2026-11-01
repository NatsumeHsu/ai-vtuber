"""Conversation and Discord snapshot persistence through ORM models."""
import json
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from database.engine import Database
from database.models import ChatSession, ChatTurn, DiscordTextContext

class StorageMixin:
    def init_database(self):
        self.database = Database(self.database_path)
        with self.database.session() as db:
            current = db.scalars(select(ChatSession).order_by(ChatSession.id.desc()).limit(1)).first()
            if current is None:
                current = ChatSession()
                db.add(current)
                db.flush()
            self.session_id = current.id
            rows = list(db.scalars(select(ChatTurn).where(ChatTurn.session_id == current.id)
                                  .order_by(ChatTurn.id.desc()).limit(max(0, self.history_turns))))
        self.history = []
        for row in reversed(rows):
            self.history.extend([
                {'role': 'user', 'content': json.dumps({'speaker': row.speaker, 'text': row.user_text}, ensure_ascii=False)},
                {'role': 'assistant', 'content': row.assistant_text}])

    def save_chat_turn(self, speaker, text, reply):
        with self.database.session() as db:
            db.add(ChatTurn(session_id=self.session_id, speaker=speaker, user_text=text, assistant_text=reply))

    def save_discord_context(self, payload):
        with self.database.session() as db:
            result = db.execute(insert(DiscordTextContext).values(
                message_id=payload['message_id'], guild_id=payload['guild_id'],
                channel_id=payload['channel_id'], user_id=payload['user_id'], user_text=payload['text'],
                context_json=json.dumps(payload['context'], ensure_ascii=False), status='received')
                .on_conflict_do_nothing(index_elements=['message_id']))
            return bool(result.rowcount)

    def finish_discord_text(self, message_id, status, reply=None):
        with self.database.session() as db:
            row = db.get(DiscordTextContext, str(message_id))
            if row:
                row.status = status
                if reply is not None:
                    row.assistant_text = reply

    def clear_history(self):
        with self.database.session() as db:
            current = ChatSession()
            db.add(current)
            db.flush()
            self.session_id = current.id
        self.history.clear()
