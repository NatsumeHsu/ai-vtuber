"""Short-lived ORM sessions; no shared connection between worker threads."""
from contextlib import contextmanager, closing
from pathlib import Path
import sqlite3
from sqlalchemy import create_engine, event, URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from database.models import Base

class Database:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(URL.create('sqlite', database=str(self.path)),
                                    connect_args={'timeout': 10}, poolclass=NullPool)
        @event.listens_for(self.engine, 'connect')
        def configure(connection, record):
            cursor = connection.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.close()
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        with self.sessions.begin() as session:
            yield session

    def connect(self):
        """Diagnostic compatibility only; application writes use ORM sessions."""
        return closing(sqlite3.connect(self.path, timeout=10))
