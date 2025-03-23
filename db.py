"""
db.py

This module defines the database models for the NCAA Tournament Picks application using SQLAlchemy.
It includes models for users, user picks, tournament matchups, and user scores.
It also initializes the database engine and provides helper functions for initializing and clearing matchup data.
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from config import DATABASE_URL

# Base class for all ORM models.
Base = declarative_base()

class User(Base):
    """
    Represents a participant in the tournament.
    
    Attributes:
        user_id (int): Primary key; unique identifier for the user.
        full_name (str): The full name of the user (must be unique).
        picks (List[UserPick]): A list of picks made by the user.
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
        seed_label (str): The label of the seed (e.g., "Seed 1").
        team_name (str): The team selected by the user for this seed.
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
        round_name (str): The round and region of the game (e.g., "Round of 64 - South").
        team1 (str): Name of the first team.
        team2 (str): Name of the second team.
        winner (str, optional): The winning team; None if the game has not been decided.
    """
    __tablename__ = 'tournament_results'
    game_id = Column(Integer, primary_key=True)
    round_name = Column(String, nullable=False)
    team1 = Column(String, nullable=False)
    team2 = Column(String, nullable=False)
    winner = Column(String, nullable=True)

class UserScore(Base):
    """
    Stores the calculated score for each user based on their correct picks.
    
    Attributes:
        score_id (int): Primary key for the score record.
        user_id (int): Foreign key linking to the associated user.
        points (float): The total points accumulated by the user.
        last_updated (str): Timestamp (in ISO format) when the score was last updated.
    """
    __tablename__ = 'user_scores'
    score_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    points = Column(Float, default=0.0)
    last_updated = Column(String)

# Create the SQLAlchemy engine using the DATABASE_URL from config.
engine = create_engine(DATABASE_URL, echo=False)

# Create a session factory bound to the engine.
SessionLocal = sessionmaker(bind=engine)

def init_db():
    """
    Initializes the database by creating all tables defined in the ORM models.
    This function should be called at the start of the application.
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
