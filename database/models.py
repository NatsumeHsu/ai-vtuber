"""ORM mappings compatible with the existing SQLite schema."""
from sqlalchemy import Column, Integer, Text, ForeignKey, Index, text, Boolean, Float
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

def timestamp():
    return Column(Text, nullable=False, server_default=text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"))

class ChatSession(Base):
    __tablename__ = 'chat_sessions'
    id = Column(Integer, primary_key=True)
    created_at = timestamp()

class ChatTurn(Base):
    __tablename__ = 'chat_turns'
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey('chat_sessions.id'), nullable=False)
    speaker = Column(Text, nullable=False)
    user_text = Column(Text, nullable=False)
    assistant_text = Column(Text, nullable=False)
    created_at = timestamp()
    __table_args__ = (Index('chat_turns_session', 'session_id', 'id'),)

class DiscordTextContext(Base):
    __tablename__ = 'discord_text_contexts'
    message_id = Column(Text, primary_key=True)
    guild_id = Column(Text, nullable=False)
    channel_id = Column(Text, nullable=False)
    user_id = Column(Text, nullable=False)
    user_text = Column(Text, nullable=False)
    context_json = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    assistant_text = Column(Text)
    created_at = timestamp()

class AudienceMessage(Base):
    __tablename__ = 'audience_messages'
    id = Column(Integer, primary_key=True)
    viewer_key = Column(Text, nullable=False)
    speaker = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    user_text = Column(Text, nullable=False)
    assistant_text = Column(Text)
    status = Column(Text, nullable=False)
    external_id = Column(Text, unique=True)
    legacy_turn_id = Column(Integer, unique=True)
    created_at = timestamp()
    __table_args__ = (Index('audience_viewer_message', 'viewer_key', 'id'),)

class AudienceProfile(Base):
    __tablename__ = 'audience_profiles'
    viewer_key = Column(Text, primary_key=True)
    profile_json = Column(Text, nullable=False)
    last_message_id = Column(Integer, nullable=False)
    updated_at = timestamp()

class AudienceMeta(Base):
    __tablename__ = 'audience_meta'
    key = Column(Text, primary_key=True)


class SongVideo(Base):
    __tablename__ = 'song_videos'
    video_id = Column(Text, primary_key=True)
    title = Column(Text, nullable=False, default='')
    play_count = Column(Integer, nullable=False, default=0)
    last_requested_by = Column(Text, nullable=False, default='')
    last_completed_at = Column(Text)


class SongRequest(Base):
    __tablename__ = 'song_requests'
    __table_args__ = {'sqlite_autoincrement': True}
    id = Column(Integer, primary_key=True)
    video_id = Column(Text, ForeignKey('song_videos.video_id'), nullable=False, unique=True)
    requested_by = Column(Text, nullable=False)
    source = Column(Text, nullable=False, default='web')
    created_at = timestamp()


class SongPlayback(Base):
    __tablename__ = 'song_playback'
    id = Column(Integer, primary_key=True)
    current_id = Column(Integer)
    paused = Column(Boolean, nullable=False, default=False)
    volume = Column(Integer, nullable=False, default=70)
    position = Column(Float, nullable=False, default=0)
