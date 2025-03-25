"""
db.py

This module defines the database models and initialization functions for the NCAA Tournament Picks application.
It uses SQLAlchemy to manage database sessions and models for Users, User Picks, Tournament Results, and User Scores.
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from config import DATABASE_URL

# Create a base class for all ORM models.
Base = declarative_base()

class User(Base):
    """
    Represents a tournament participant.

    Attributes:
        user_id (int): Primary key, unique identifier for the user.
        full_name (str): User's full name (must be unique).
        picks (List[UserPick]): List of picks made by the user.
    """
    __tablename__ = 'users'
    user_id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String, unique=True, nullable=False)
    picks = relationship("UserPick", back_populates="user")

class UserPick(Base):
    """
    Represents a single pick made by a user.

    Attributes:
        pick_id (int): Primary key for the pick.
        user_id (int): Foreign key linking to the associated user.
        seed_label (str): Label for the seed (e.g., "Seed 1").
        team_name (str): The team selected by the user.
    """
    __tablename__ = 'user_picks'
    pick_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    seed_label = Column(String, nullable=False)
    team_name = Column(String, nullable=False)
    user = relationship("User", back_populates="picks")

class TournamentResult(Base):
    """
    Represents a game in the tournament bracket.

    Attributes:
        game_id (int): Primary key for the game.
        round_name (str): The round and region (e.g., "Round of 64 - South").
        team1 (str): Name of the first team.
        team2 (str): Name of the second team.
        winner (str): The winning team; None if undecided.
    """
    __tablename__ = 'tournament_results'
    game_id = Column(Integer, primary_key=True)
    round_name = Column(String, nullable=False)
    team1 = Column(String, nullable=False)
    team2 = Column(String, nullable=False)
    winner = Column(String, nullable=True)

class UserScore(Base):
    """
    Stores the calculated score for a user based on correct picks.

    Attributes:
        score_id (int): Primary key for the score record.
        user_id (int): Foreign key linking to the associated user.
        points (float): Total points accumulated by the user.
        last_updated (str): ISO formatted timestamp for the last update.
    """
    __tablename__ = 'user_scores'
    score_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    points = Column(Float, default=0.0)
    last_updated = Column(String)

# Create the SQLAlchemy engine using the DATABASE_URL from configuration.
engine = create_engine(DATABASE_URL, echo=False)

# Create a session factory bound to the engine.
SessionLocal = sessionmaker(bind=engine)

def init_db():
    """
    Initializes the database by creating all tables defined in the ORM models.
    Call this at application startup to ensure the database schema is in place.
    """
    Base.metadata.create_all(engine)

def clear_matchup_data():
    """
    Clears all matchup data from the TournamentResult table.
    Useful for testing or resetting the tournament data.
    """
    session = SessionLocal()
    try:
        session.query(TournamentResult).delete()
        session.commit()
    finally:
        session.close()
