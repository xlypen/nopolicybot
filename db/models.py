# Source Generated with Decompyle++
# File: models.cpython-312.pyc (Python 3.12)

from datetime import datetime
from sqlalchemy import BigInteger, Boolean, Column, Float, Index, Integer, JSON, Text, TIMESTAMP
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

