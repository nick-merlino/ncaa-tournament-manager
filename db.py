# db.py
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from config import DATABASE_URL

Base = declarative_base()

class User(Base):
    """
    Represents a single participant (e.g. "Nick Merlino").
    """
    __tablename__ = 'users'
    user_id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String, unique=True, nullable=False)
    picks = relationship("UserPick", back_populates="user")

class UserPick(Base):
    """
    Each participant's picks, labeled by seed or another scheme (e.g. "Seed 1").
    """
    __tablename__ = 'user_picks'
    pick_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    seed_label = Column(String, nullable=False)  # e.g. "Seed 1"
    team_name = Column(String, nullable=False)

    user = relationship("User", back_populates="picks")

class TournamentResult(Base):
    """
    Stores the bracket's matchups and winners (to be set via the web interface).
    """
    __tablename__ = 'tournament_results'
    game_id = Column(Integer, primary_key=True)
    round_name = Column(String, nullable=False)
    team1 = Column(String, nullable=False)
    team2 = Column(String, nullable=False)
    winner = Column(String, nullable=True)  # may be NULL if not decided yet

class UserScore(Base):
    """
    Summed points for each user, updated after we run the scoring routine.
    """
    __tablename__ = 'user_scores'
    score_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    points = Column(Float, default=0.0)
    last_updated = Column(String)

# Setup engine and sessionmaker
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    """Create tables if they do not exist."""
    Base.metadata.create_all(engine)
