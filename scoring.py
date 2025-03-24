"""
scoring.py

This module calculates user scores based on tournament results and determines the current
round status and visible rounds using recursive logic.

The round visibility logic ensures that a round is only visible if every game in all previous
rounds (across all regions) is complete.
"""

import datetime
from collections import defaultdict
from config import logger
from db import SessionLocal, TournamentResult, User, UserScore
from constants import ROUND_ORDER, ROUND_WEIGHTS

def calculate_scoring():
    """
    Calculates and updates user scores based on tournament results.
    Each round win contributes points based on ROUND_WEIGHTS.
    """
    session = SessionLocal()
    try:
        session.query(UserScore).delete()
        results = session.query(TournamentResult).all()
        winners_by_round = {}
        for result in results:
            if result.winner:
                base_round = result.round_name.split('-', 1)[0].strip()
                winners_by_round.setdefault(base_round, set()).add(result.winner.strip())
        users = session.query(User).all()
        for user in users:
            total_points = 0.0
            for pick in user.picks:
                for round_name, winners_set in winners_by_round.items():
                    if pick.team_name.strip() in winners_set:
                        total_points += ROUND_WEIGHTS.get(round_name, 1)
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
    Determine the current round and visible rounds based on TournamentResult data.

    A round is visible only if every game in all previous rounds (across all regions) is complete.
    For example, round 2 is visible only if every game in round 1 is complete, and round 3 is visible
    only if rounds 1 and 2 are complete.

    Returns:
        tuple: (current_round, visible_rounds)
          - current_round (str): The first visible round that is incomplete, or if all visible rounds are
            complete, the last visible round.
          - visible_rounds (dict): A dictionary mapping base round names (e.g. "Round of 64") to lists
            of game dictionaries. Only rounds up to (and including) the first incomplete round are visible.
    """
    session = SessionLocal()
    try:
        results = session.query(TournamentResult).all()
        rounds = {}
        # Group games by their base round (ignoring region differences).
        for game in results:
            base = game.round_name.split('-', 1)[0].strip()
            rounds.setdefault(base, []).append({
                "game_id": game.game_id,
                "team1": game.team1,
                "team2": game.team2,
                "winner": game.winner
            })
        # Build visible_rounds recursively: add rounds only if all games in previous rounds are complete.
        visible_rounds = {}
        for r in ROUND_ORDER:
            if r not in rounds:
                continue
            visible_rounds[r] = rounds[r]
            if not all(g["winner"] and g["winner"].strip() for g in rounds[r]):
                break
        # Determine current round: the first visible round that is incomplete, or if all complete, the last visible.
        current = None
        for r in ROUND_ORDER:
            if r in visible_rounds:
                if any(not (g["winner"] and g["winner"].strip()) for g in visible_rounds[r]):
                    current = r
                    break
        if not current and visible_rounds:
            current = list(visible_rounds.keys())[-1]
        return current, visible_rounds
    finally:
        session.close()
