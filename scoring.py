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
    Determines the current round in progress and groups tournament games by round.
    
    Returns:
        tuple: (current_round, round_games)
            - current_round (str): The first round (from ROUND_ORDER) that has any game without a selected winner.
                                   If all rounds are complete, it defaults to the last round with games.
            - round_games (dict): A dictionary mapping round names to lists of game dictionaries. Each dictionary contains:
                                  'game_id', 'team1', 'team2', and 'winner'.
    """
    session = SessionLocal()
    try:
        results = session.query(TournamentResult).all()
        round_games = defaultdict(list)
        for game in results:
            round_games[game.round_name].append({
                'game_id': game.game_id,
                'team1': game.team1,
                'team2': game.team2,
                'winner': game.winner
            })
        
        current_round = None
        # Identify the first round with any game still missing a winner
        for base_round in ROUND_ORDER:
            games_in_round = []
            for round_name, games in round_games.items():
                if round_name.startswith(base_round):
                    games_in_round.extend(games)
            if games_in_round and any(g['winner'] is None for g in games_in_round):
                current_round = base_round
                break
        
        # If all rounds have a winner selected, choose the last round with games
        if not current_round:
            for base_round in reversed(ROUND_ORDER):
                games_in_round = []
                for round_name, games in round_games.items():
                    if round_name.startswith(base_round):
                        games_in_round.extend(games)
                if games_in_round:
                    current_round = base_round
                    break
        
        return current_round, round_games
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
