"""
scoring.py

This module contains functions to calculate user scores based on tournament results,
determine the current visible round(s) using recursive logic, and simulate the best-case
final scores by exploring all remaining outcomes. Points are allocated per round based on
configured weights (ROUND_WEIGHTS), and only rounds that are visible (i.e. fully completed)
are considered in scoring.
"""

import datetime
import json
import itertools
from collections import defaultdict

from config import logger
from db import SessionLocal, TournamentResult, User, UserScore
from constants import ROUND_ORDER, ROUND_WEIGHTS, FIRST_ROUND_PAIRINGS
from sqlalchemy.orm import joinedload


def calculate_scoring():
    """
    Calculates and updates user scores based on tournament results.
    Only considers rounds that are currently visible (i.e. completed up to the first incomplete round).
    Each correct pick awards points defined by ROUND_WEIGHTS.
    """
    session = SessionLocal()
    try:
        # Clear out any previous scores.
        session.query(UserScore).delete()
        session.commit()

        results = session.query(TournamentResult).all()
        # Determine current round and the set of visible rounds.
        _, visible_rounds = get_round_game_status()
        visible_round_keys = set(visible_rounds.keys())

        # Group winning teams by base round.
        winners_by_round = {}
        for result in results:
            if result.winner:
                base_round = result.round_name.split('-', 1)[0].strip()
                if base_round in visible_round_keys:
                    winners_by_round.setdefault(base_round, set()).add(result.winner.strip())

        users = session.query(User).all()
        for user in users:
            total_points = 0.0
            for pick in user.picks:
                for round_name, winners in winners_by_round.items():
                    if pick.team_name.strip() in winners:
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
    Determines the current round and visible rounds based on TournamentResult data.
    
    A round is visible only if every game in that round is complete.
    Rounds are processed sequentially using the order defined in ROUND_ORDER.
    
    Returns:
        tuple: (current_round, visible_rounds)
          - current_round (str): The first incomplete round among the visible rounds,
            or if all visible rounds are complete, the last visible round.
          - visible_rounds (dict): A dictionary mapping base round names (e.g. "Round of 64")
            to lists of game dictionaries.
    """
    session = SessionLocal()
    try:
        results = session.query(TournamentResult).all()
        rounds = defaultdict(list)
        # Group games by their base round (ignoring region details).
        for game in results:
            base_round = game.round_name.split('-', 1)[0].strip()
            rounds[base_round].append({
                "game_id": game.game_id,
                "team1": game.team1,
                "team2": game.team2,
                "winner": game.winner
            })

        visible_rounds = {}
        # Include rounds sequentially until an incomplete round is encountered.
        for r in ROUND_ORDER:
            if r in rounds:
                visible_rounds[r] = rounds[r]
                if not all(g["winner"] and g["winner"].strip() for g in rounds[r]):
                    break

        # Identify the current round: the first visible round with an incomplete game.
        current_round = None
        for r in ROUND_ORDER:
            if r in visible_rounds:
                if any(not (g["winner"] and g["winner"].strip()) for g in visible_rounds[r]):
                    current_round = r
                    break
        if not current_round and visible_rounds:
            current_round = list(visible_rounds.keys())[-1]
        return current_round, visible_rounds
    finally:
        session.close()


def calculate_best_case_scores():
    """
    Calculates best-case final scores for each user by exhaustively simulating the remainder of the tournament.
    The simulation recursively explores all outcomes for future rounds based on the current surviving teams.
    
    Process:
      1. Retrieve current user picks and scores.
      2. Prune each user's surviving teams from completed rounds.
      3. Load the tournament bracket from JSON and simulate regional rounds (Round of 64, Round of 32, Sweet 16, Elite 8).
      4. Simulate interregional rounds (Final Four and Championship).
      5. Recursively simulate each remaining round to calculate the maximum additional bonus points.
      6. Best-case score = current score + maximum achievable bonus.
    
    Returns:
        dict: Mapping of each user's full name to their best-case final score (float).
    """
    session = SessionLocal()
    try:
        # Retrieve users and their picks.
        users = session.query(User).options(joinedload(User.picks)).all()
        current_scores = {}
        user_picks = {}
        for user in users:
            score_obj = session.query(UserScore).filter_by(user_id=user.user_id).first()
            current_scores[user.full_name] = score_obj.points if score_obj else 0.0
            user_picks[user.full_name] = {pick.team_name.strip() for pick in user.picks}

        # Determine visible rounds.
        current_round, visible_rounds = get_round_game_status()
        if not current_round:
            current_round = ROUND_ORDER[0]
        current_index = ROUND_ORDER.index(current_round)

        # Prune surviving teams based on completed rounds.
        user_survivors = {}
        for user in users:
            name = user.full_name
            survivors = set(user_picks.get(name, set()))
            for rnd in ROUND_ORDER:
                if rnd not in visible_rounds:
                    break
                games = visible_rounds[rnd]
                if not all(game.get("winner") and game["winner"].strip() for game in games):
                    break
                new_survivors = set()
                for game in games:
                    w = game.get("winner", "").strip()
                    if w in survivors:
                        new_survivors.add(w)
                survivors = new_survivors
            if not survivors:
                survivors = set(user_picks.get(name, set()))
            user_survivors[name] = survivors

        # Load tournament bracket JSON for simulation.
        with open("tournament_bracket.json", "r") as f:
            bracket = json.load(f)
        regions = bracket.get("regions", [])

        # Simulate regional rounds.
        regional_sim = {}
        for region in regions:
            region_name = region.get("region_name", "Unknown")
            teams_by_seed = {team["seed"]: team["team_name"].strip() for team in region.get("teams", [])}
            # Build Round of 64 using FIRST_ROUND_PAIRINGS.
            r64 = []
            for pairing in FIRST_ROUND_PAIRINGS:
                teamA = teams_by_seed.get(pairing[0])
                teamB = teams_by_seed.get(pairing[1])
                r64.append({"teams": {teamA, teamB}})
            # Build Round of 32 (combining two Round of 64 games).
            r32 = []
            for i in range(4):
                teams = r64[2 * i]["teams"].union(r64[2 * i + 1]["teams"])
                r32.append({"teams": teams})
            # Build Sweet 16.
            s16 = []
            for i in range(2):
                teams = r32[2 * i]["teams"].union(r32[2 * i + 1]["teams"])
                s16.append({"teams": teams})
            # Build Elite 8.
            e8 = [{"teams": s16[0]["teams"].union(s16[1]["teams"])}]
            regional_sim[region_name] = {
                "Round of 64": r64,
                "Round of 32": r32,
                "Sweet 16": s16,
                "Elite 8": e8
            }

        simulated_bracket = {}
        for rnd in ["Round of 64", "Round of 32", "Sweet 16", "Elite 8"]:
            games = []
            for region in regions:
                region_name = region.get("region_name", "Unknown")
                games.extend(regional_sim.get(region_name, {}).get(rnd, []))
            simulated_bracket[rnd] = games

        # Simulate interregional rounds: Final Four and Championship.
        final_four = []
        if len(regions) >= 4:
            teams_ff0 = set(regional_sim.get(regions[0]["region_name"], {}).get("Elite 8", [{}])[0].get("teams", set())).union(
                        set(regional_sim.get(regions[1]["region_name"], {}).get("Elite 8", [{}])[0].get("teams", set())))
            final_four.append({"teams": teams_ff0})
            teams_ff1 = set(regional_sim.get(regions[2]["region_name"], {}).get("Elite 8", [{}])[0].get("teams", set())).union(
                        set(regional_sim.get(regions[3]["region_name"], {}).get("Elite 8", [{}])[0].get("teams", set())))
            final_four.append({"teams": teams_ff1})
        simulated_bracket["Final Four"] = final_four
        if len(final_four) == 2:
            championship_set = set(final_four[0]["teams"]).union(final_four[1]["teams"])
            simulated_bracket["Championship"] = [{"teams": championship_set}]
        else:
            simulated_bracket["Championship"] = []

        # Recursive simulation: determine the maximum bonus points achievable.
        future_rounds = ROUND_ORDER[current_index:]

        def simulate_round(survivors, round_index):
            if round_index >= len(future_rounds):
                return 0
            rnd = future_rounds[round_index]
            games = simulated_bracket.get(rnd, [])
            weight = ROUND_WEIGHTS.get(rnd, 1)
            possibilities = []
            for game in games:
                common = survivors.intersection(game["teams"])
                if common:
                    possibilities.append(list(common))
                else:
                    possibilities.append([None])
            best_bonus = 0
            for outcome in itertools.product(*possibilities):
                bonus = sum(weight if winner is not None else 0 for winner in outcome)
                new_survivors = {winner for winner in outcome if winner is not None}
                bonus += simulate_round(new_survivors, round_index + 1)
                best_bonus = max(best_bonus, bonus)
            return best_bonus

        best_case_scores = {}
        for name, survivors in user_survivors.items():
            bonus = simulate_round(survivors, 0)
            best_case_scores[name] = current_scores.get(name, 0) + bonus

        return best_case_scores
    except Exception as e:
        logger.error(f"Error calculating best-case scores: {e}")
        return {}
    finally:
        session.close()
