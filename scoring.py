# scoring.py

import datetime
from config import logger
from db import SessionLocal, User, UserPick, TournamentResult, UserScore

def calculate_scoring(round_weights=None):
    """
    Recalculate scoring for each user, awarding points if they picked the winning team in a round.
    We no longer offer any partial-points logic.
    """
    session = SessionLocal()
    try:
        if round_weights is None:
            round_weights = {
                "Round of 64": 1,
                "Round of 32": 2,
                "Sweet 16": 4,
                "Elite 8": 8,
                "Final Four": 16,
                "Championship": 32
            }

        # Clear existing scores
        session.query(UserScore).delete()

        # Gather bracket results
        results = session.query(TournamentResult).all()
        # We'll store winners by round in a dict: { "Round of 64": {"Team A", ...}, ... }
        winners_by_round = {}
        for r in results:
            if r.winner:
                winners_by_round.setdefault(r.round_name, set()).add(r.winner)

        # Tally points
        users = session.query(User).all()
        for user in users:
            total_points = 0.0
            for pick in user.picks:
                # For each round's winners, see if the user's pick is in that set
                for rd_name, winners_set in winners_by_round.items():
                    if pick.team_name in winners_set:
                        total_points += round_weights.get(rd_name, 1)

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
    Identify the current round (the first that is not fully decided).
    Returns (current_round_name, round_games_dict).
    """
    from collections import defaultdict
    session = SessionLocal()
    try:
        results = session.query(TournamentResult).all()

        round_games = defaultdict(list)
        for g in results:
            round_games[g.round_name].append({
                'game_id': g.game_id,
                'team1': g.team1,
                'team2': g.team2,
                'winner': g.winner
            })

        current_round = None
        for rd_name, games in round_games.items():
            # if any game in this round has no winner, that's the current
            if any(not gm['winner'] for gm in games):
                current_round = rd_name
                break

        return current_round, round_games
    finally:
        session.close()
