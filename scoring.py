# scoring.py

import json
import datetime
from config import logger
from db import SessionLocal, User, UserPick, TournamentResult, UserScore

def import_tournament_results_from_json(json_file_path: str):
    session = SessionLocal()
    try:
        with open(json_file_path, 'r') as f:
            data = json.load(f)
        rounds_data = data.get("rounds", [])
        for rd in rounds_data:
            round_name = rd["round"]
            games = rd["games"]
            for game in games:
                game_id = game["game_id"]
                team1 = game["team1"]
                team2 = game["team2"]
                winner = game.get("winner")
                existing_game = session.query(TournamentResult).filter_by(game_id=game_id).first()
                if existing_game:
                    existing_game.round_name = round_name
                    existing_game.team1 = team1
                    existing_game.team2 = team2
                    existing_game.winner = winner
                else:
                    new_game = TournamentResult(
                        game_id=game_id,
                        round_name=round_name,
                        team1=team1,
                        team2=team2,
                        winner=winner
                    )
                    session.add(new_game)
        session.commit()
    except Exception as e:
        logger.error(f"Error importing tournament results: {e}")
        session.rollback()
    finally:
        session.close()

def calculate_scoring(round_weights=None):
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
        session.query(UserScore).delete()
        results = session.query(TournamentResult).all()
        winners_by_round = {}
        for r in results:
            if r.winner:
                winners_by_round.setdefault(r.round_name, set()).add(r.winner)
        users = session.query(User).all()
        for user in users:
            total_points = 0.0
            for pick in user.picks:
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
    Returns (current_round, round_games) where:
      - round_games is a dict mapping round names to lists of dictionaries,
        each with keys: 'game_id', 'team1', 'team2', 'winner'
      - current_round is the first round (by our order) that has any game with no winner.
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
        # We'll consider rounds in the order defined below:
        ROUND_ORDER = ["Round of 64", "Round of 32", "Sweet 16", "Elite 8", "Final Four", "Championship"]
        for base_round in ROUND_ORDER:
            # Find all games whose round_name starts with base_round
            games = []
            for rn, gs in round_games.items():
                if rn.startswith(base_round):
                    games.extend(gs)
            if games and any(g['winner'] is None for g in games):
                current_round = base_round
                break
        # If all rounds are complete, default to the last round
        if not current_round:
            for base_round in reversed(ROUND_ORDER):
                games = []
                for rn, gs in round_games.items():
                    if rn.startswith(base_round):
                        games.extend(gs)
                if games:
                    current_round = base_round
                    break
        return current_round, round_games
    finally:
        session.close()

def get_unlocked_rounds(bracket_data):
    """
    Given bracket_data as a dict mapping base rounds (e.g. "Round of 64")
    to a list of game dictionaries, return a list of base rounds that are unlocked.
    A round is unlocked if it is the first round or if the previous round's games are all decided.
    """
    ROUND_ORDER = ["Round of 64", "Round of 32", "Sweet 16", "Elite 8", "Final Four", "Championship"]
    unlocked = []
    for i, base_round in enumerate(ROUND_ORDER):
        games = bracket_data.get(base_round, [])
        if not games:
            continue
        if i == 0:
            unlocked.append(base_round)
        else:
            prev_round = ROUND_ORDER[i - 1]
            if prev_round in unlocked:
                prev_games = bracket_data.get(prev_round, [])
                if all(g['winner'] for g in prev_games):
                    unlocked.append(base_round)
    return unlocked
