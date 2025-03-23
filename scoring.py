"""
scoring.py

This module calculates user scores based on tournament results and provides
helper functions to determine the current round status and unlocked rounds.
"""

import datetime
from collections import defaultdict

from config import logger
from db import SessionLocal, User, TournamentResult, UserScore
from constants import ROUND_ORDER, ROUND_WEIGHTS  # Shared constant for round ordering

def calculate_scoring():
    """
    Calculates and updates the scores for each user based on their correct picks.
    
    By default, each round win is worth 1 point.
    """
    session = SessionLocal()
    try:
        # Clear previous user scores
        session.query(UserScore).delete()

        # Aggregate winners by round (ignoring any extra descriptor after a hyphen)
        results = session.query(TournamentResult).all()
        winners_by_round = {}
        for result in results:
            if result.winner:
                # Use only the base round name (e.g., "Round of 64") for scoring
                base_round = result.round_name.split('-')[0].strip()
                winners_by_round.setdefault(base_round, set()).add(result.winner.strip())

        # Calculate and update score for each user based on their picks
        users = session.query(User).all()
        for user in users:
            total_points = 0.0
            for pick in user.picks:
                for round_name, winners_set in winners_by_round.items():
                    if pick.team_name.strip() in winners_set:
                        total_points += ROUND_WEIGHTS.get(round_name, 1)
            # Create or update the user's score record with current timestamp
            user_score = UserScore(
                user_id=user.user_id,
                points=total_points,
                last_updated=datetime.datetime.utcnow().isoformat()
            )
            session.add(user_score)
        session.commit()
    except Exception as e:
        logger.error(f"Error calculating scoring: {e}")
        session.rollback()
    finally:
        session.close()
        
def get_round_game_status():
    """
    Determine the current round based on TournamentResult data.
    
    Groups games by their base round and returns the first round with any game missing
    a non-empty winner. If all rounds are complete, returns the last round.
    
    Debug logging is included.
    
    Returns:
        tuple: (current_round, round_games)
    """
    session = SessionLocal()
    try:
        results = session.query(TournamentResult).all()
        rounds = {}
        for game in results:
            base = game.round_name.split('-', 1)[0].strip()
            rounds.setdefault(base, []).append({
                "game_id": game.game_id,
                "team1": game.team1,
                "team2": game.team2,
                "winner": game.winner
            })
        for r in ROUND_ORDER:
            if r in rounds:
                incomplete = [g for g in rounds[r] if not (g["winner"] and g["winner"].strip())]
                logger.info(f"Round '{r}': {len(rounds[r])} games, {len(incomplete)} incomplete.")
        current = None
        # Find the first round in the defined order that has any incomplete game.
        for r in ROUND_ORDER:
            if r in rounds and any(not (g["winner"] and g["winner"].strip()) for g in rounds[r]):
                current = r
                logger.info(f"Current round determined as '{current}'.")
                break
        if not current:
            # If all rounds are complete, pick the last round.
            for r in reversed(ROUND_ORDER):
                if r in rounds:
                    current = r
                    logger.info(f"All rounds complete. Current round set to '{current}'.")
                    break
        return current, rounds
    finally:
        session.close()

def get_unlocked_rounds(bracket_data):
    """
    Determines which rounds are unlocked based on the tournament bracket data.
    A round is considered unlocked if it is the first round or if all games in the previous round are decided.
    
    Args:
        bracket_data (dict): Mapping of base rounds (e.g., "Round of 64") to lists of game dictionaries.
    
    Returns:
        list: Base rounds that are currently unlocked.
    """
    unlocked = []
    for i, base_round in enumerate(ROUND_ORDER):
        games = bracket_data.get(base_round, [])
        if not games:
            continue
        if i == 0:
            unlocked.append(base_round)
        else:
            prev_round = ROUND_ORDER[i - 1]
            prev_games = bracket_data.get(prev_round, [])
            if prev_games and all(game.get('winner') for game in prev_games):
                unlocked.append(base_round)
    return unlocked
