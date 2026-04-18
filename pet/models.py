from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

DB_PATH = Path(__file__).parent / "pet.db"
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Session = sessionmaker(bind=engine)


PET_TYPES = {"chicken", "goose", "dog", "turtle"}
PET_COLORS = {"natural", "white", "red", "orange", "green", "blue", "purple", "pink"}


def utcnow():
    return datetime.now(timezone.utc)


class Pet(Base):
    __tablename__ = "pets"

    id = Column(Integer, primary_key=True)
    # user_id references community.users.id but we don't enforce it (separate DB)
    user_id = Column(Integer, unique=True, nullable=False, index=True)
    pet_type = Column(String(16), nullable=False)
    name = Column(String(32), default="")
    color = Column(String(16), default="natural", nullable=False)
    last_fed_at = Column(DateTime, default=utcnow, nullable=False)
    alive = Column(Boolean, default=True, nullable=False)
    died_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "pet_type": self.pet_type,
            "name": self.name,
            "color": self.color or "natural",
            "last_fed_at": self.last_fed_at.isoformat() if self.last_fed_at else None,
            "alive": self.alive,
            "died_at": self.died_at.isoformat() if self.died_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def init_db():
    Base.metadata.create_all(engine)
    _one_time_import_from_community()


def _one_time_import_from_community():
    """If the old community pets table exists and pet.db is empty, copy the rows."""
    import sqlite3
    community_db = Path(__file__).parent.parent / "community" / "community.db"
    if not community_db.exists():
        return
    with sqlite3.connect(str(DB_PATH)) as dst:
        count = dst.execute("SELECT COUNT(*) FROM pets").fetchone()[0]
        if count > 0:
            return
        with sqlite3.connect(str(community_db)) as src:
            t = src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='pets'"
            ).fetchone()
            if not t:
                return
            cols = [r[1] for r in src.execute("PRAGMA table_info(pets)").fetchall()]
            needed = ["user_id", "pet_type", "name", "color", "last_fed_at",
                      "alive", "died_at", "created_at"]
            if not all(c in cols for c in needed):
                return
            rows = src.execute(
                "SELECT user_id, pet_type, name, color, last_fed_at, "
                "alive, died_at, created_at FROM pets"
            ).fetchall()
        for r in rows:
            try:
                dst.execute(
                    "INSERT INTO pets (user_id, pet_type, name, color, last_fed_at,"
                    " alive, died_at, created_at) VALUES (?,?,?,?,?,?,?,?)", r
                )
            except sqlite3.IntegrityError:
                pass
        dst.commit()
