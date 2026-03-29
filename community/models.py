import random
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean, DateTime,
    ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

DB_PATH = Path(__file__).parent / "community.db"
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Session = sessionmaker(bind=engine)


def _random_color():
    colors = [
        "#c2593e", "#b8924a", "#6d8f5e", "#5a8fa8", "#8f5ea8",
        "#a85a5a", "#5aa88f", "#a87a5a", "#5a6da8", "#a85a8f",
    ]
    return random.choice(colors)


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, nullable=False)
    display_name = Column(String(64), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    avatar_color = Column(String(7), default=_random_color)
    bio = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)
    last_seen = Column(DateTime, default=utcnow)
    active_background_id = Column(Integer, ForeignKey("user_backgrounds.id"), nullable=True)

    active_background = relationship("UserBackground", foreign_keys=[active_background_id])

    def to_dict(self):
        d = {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "avatar_color": self.avatar_color,
            "bio": self.bio,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "active_background_id": self.active_background_id,
        }
        if self.active_background:
            d["active_background"] = self.active_background.css_data
        return d


class Friendship(Base):
    __tablename__ = "friendships"
    __table_args__ = (
        UniqueConstraint("requester_id", "addressee_id", name="uq_friendship"),
    )

    id = Column(Integer, primary_key=True)
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    addressee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(10), default="pending")  # pending, accepted, declined
    created_at = Column(DateTime, default=utcnow)
    responded_at = Column(DateTime, nullable=True)

    requester = relationship("User", foreign_keys=[requester_id])
    addressee = relationship("User", foreign_keys=[addressee_id])

    def to_dict(self):
        return {
            "id": self.id,
            "requester_id": self.requester_id,
            "addressee_id": self.addressee_id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "requester": self.requester.to_dict() if self.requester else None,
            "addressee": self.addressee.to_dict() if self.addressee else None,
        }


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=True)
    image_path = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    read_at = Column(DateTime, nullable=True)

    sender = relationship("User", foreign_keys=[sender_id])
    receiver = relationship("User", foreign_keys=[receiver_id])

    def to_dict(self):
        return {
            "id": self.id,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "content": self.content,
            "image_path": self.image_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }


class PortfolioItem(Base):
    __tablename__ = "portfolio_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(128), nullable=False)
    description = Column(Text, default="")
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(10), nullable=False)  # mp3, mp4, pdf
    file_size = Column(Integer, nullable=False)
    is_public = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

    owner = relationship("User", foreign_keys=[user_id])

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "description": self.description,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "is_public": self.is_public,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserBackground(Base):
    __tablename__ = "user_backgrounds"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(64), nullable=False)
    quiz_answers = Column(Text, nullable=False)  # JSON
    css_data = Column(Text, nullable=False)  # JSON
    created_at = Column(DateTime, default=utcnow)

    owner = relationship("User", foreign_keys=[user_id])

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "quiz_answers": self.quiz_answers,
            "css_data": self.css_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class GroupChat(Base):
    __tablename__ = "group_chats"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    avatar_color = Column(String(7), default=_random_color)
    created_at = Column(DateTime, default=utcnow)

    creator = relationship("User", foreign_keys=[creator_id])
    members = relationship("GroupMember", back_populates="group", cascade="all, delete-orphan")

    def to_dict(self, include_members=False):
        d = {
            "id": self.id,
            "name": self.name,
            "creator_id": self.creator_id,
            "avatar_color": self.avatar_color,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_members:
            d["members"] = [m.user.to_dict() for m in self.members if m.user]
        return d


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_member"),
    )

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("group_chats.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    joined_at = Column(DateTime, default=utcnow)

    group = relationship("GroupChat", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])


class GroupMessage(Base):
    __tablename__ = "group_messages"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("group_chats.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=True)
    image_path = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)

    sender = relationship("User", foreign_keys=[sender_id])

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender.display_name if self.sender else None,
            "sender_color": self.sender.avatar_color if self.sender else None,
            "content": self.content,
            "image_path": self.image_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def init_db():
    Base.metadata.create_all(engine)
    # Add active_background_id column to users if missing (SQLite migration)
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in cursor.fetchall()]
    if "active_background_id" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN active_background_id INTEGER")
        conn.commit()
    conn.close()
