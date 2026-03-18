# Source Generated with Decompyle++
# File: models.cpython-312.pyc (Python 3.12)

from datetime import datetime
from sqlalchemy import BigInteger, Boolean, Column, Float, Index, Integer, JSON, String, Text, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id = Column(BigInteger, primary_key = True)
    chat_id = Column(BigInteger, nullable = False)
    username = Column(Text)
    first_name = Column(Text)
    last_name = Column(Text)
    joined_at = Column(TIMESTAMP, default = datetime.utcnow)
    last_seen = Column(TIMESTAMP)
    is_active = Column(Boolean, default = True)
    political_messages = Column(Integer, default = 0)
    warnings_received = Column(Integer, default = 0)
    __table_args__ = (Index('idx_users_chat', 'chat_id'),)


class Message(Base):
    __tablename__ = 'messages'
    id = Column(Integer, primary_key = True, autoincrement = True)
    telegram_id = Column(BigInteger, unique = True)
    chat_id = Column(BigInteger, nullable = False)
    user_id = Column(BigInteger)
    text = Column(Text)
    media_type = Column(Text)
    replied_to = Column(BigInteger)
    sent_at = Column(TIMESTAMP, nullable = False)
    tone_score = Column(Float)
    risk_flags = Column(JSON, default = list)
    __table_args__ = (Index('idx_messages_chat_sent', 'chat_id', 'sent_at'), Index('idx_messages_user_sent', 'user_id', 'sent_at'))


class Edge(Base):
    __tablename__ = 'edges'
    id = Column(Integer, primary_key = True, autoincrement = True)
    chat_id = Column(BigInteger, nullable = False)
    from_user = Column(BigInteger, nullable = False)
    to_user = Column(BigInteger, nullable = False)
    weight = Column(Float, default = 1)
    period_7d = Column(Float, default = 0)
    period_30d = Column(Float, default = 0)
    tone = Column(String(32), default='neutral')
    topics = Column(JSON, default=list)
    summary = Column(Text, default='')
    summary_by_date = Column(JSON, default=list)
    last_updated = Column(TIMESTAMP, default = datetime.utcnow)
    __table_args__ = (Index('idx_edges_chat', 'chat_id'), Index('idx_edges_unique', 'chat_id', 'from_user', 'to_user', unique = True))


class GraphSnapshot(Base):
    __tablename__ = 'graph_snapshots'
    id = Column(Integer, primary_key = True, autoincrement = True)
    chat_id = Column(BigInteger, nullable = False)
    snapshot_at = Column(TIMESTAMP, default = datetime.utcnow)
    payload = Column(JSON, nullable = False)


class UserPortrait(Base):
    __tablename__ = 'user_portraits'
    user_id = Column(BigInteger, primary_key = True)
    chat_id = Column(BigInteger, primary_key = True)
    portrait = Column(Text)
    generated_at = Column(TIMESTAMP, default = datetime.utcnow)


class ChatSettings(Base):
    __tablename__ = 'chat_settings'
    chat_id = Column(BigInteger, primary_key = True)
    settings = Column(JSON, nullable = False, default = dict)


class ProcessedDate(Base):
    """Даты, по которым уже построены связи (social_graph). Замена JSON processed_dates."""
    __tablename__ = "processed_dates"
    chat_id = Column(BigInteger, primary_key = True)
    processed_date = Column(String(10), primary_key = True)


class PersonalityProfileRow(Base):
    """Structured personality profile (P-1) — OCEAN, Dark Triad, communication."""
    __tablename__ = 'personality_profiles'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    generated_at = Column(TIMESTAMP, nullable=False)
    period_days = Column(Integer, nullable=False)
    messages_analyzed = Column(Integer, nullable=False)
    confidence = Column(Float, nullable=False)
    profile_json = Column(JSON, nullable=False)
    model_version = Column(String(50), nullable=False)
    __table_args__ = (Index('idx_personality_profiles_user_chat', 'user_id', 'chat_id', 'generated_at'),)


class PersonalityPortraitRow(Base):
    """Generated visual portrait based on personality profile (IMG-3)."""
    __tablename__ = 'personality_portraits'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    profile_id = Column(Integer, nullable=True)
    generated_at = Column(TIMESTAMP, nullable=False)
    model_used = Column(String(50), nullable=False)
    prompt_used = Column(Text, nullable=False)
    seed_description = Column(Text, nullable=True)
    generation_time_sec = Column(Float, nullable=True)
    image_path = Column(String(500), nullable=False)
    image_hash = Column(String(64), nullable=True)
    style_variant = Column(String(50), nullable=False, default='concept_art')
    __table_args__ = (Index('idx_personality_portraits_user_chat', 'user_id', 'chat_id', 'generated_at'),)

