"""
scoring.py

This module contains functions to calculate user scores based on tournament results,
determine the current visible round(s) using recursive logic, and simulate the best‑ and
worst‑case final scores by exploring all remaining outcomes. Points are allocated per round
based on configured weights (ROUND_WEIGHTS), and only rounds that are visible (i.e. fully or
partially completed) are considered in scoring. For rounds in progress, any game with a played
result is treated as final.
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
    Only considers rounds that are currently complete or partially complete up to (and including)
    the current round. Games finished in future rounds are not counted.
    Each correct pick awards points defined by ROUND_WEIGHTS.
    """
    session = SessionLocal()
    try:
        # Clear out any previous scores.
        session.query(UserScore).delete()
        session.commit()

        results = session.query(TournamentResult).all()
        # Determine current round and the set of visible rounds.
        current_round, visible_rounds = get_round_game_status()
        # Allowed rounds: those that occur before or equal to the current round.
        allowed_rounds = set(ROUND_ORDER[:ROUND_ORDER.index(current_round)+1])
        
        # Group winning teams by base round, but only if that round is allowed.
        winners_by_round = {}
        for result in results:
            if result.winner:
                base_round = result.round_name.split('-', 1)[0].strip()
                if base_round in allowed_rounds:
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
    
    A round is visible if it exists in the database. Rounds are processed sequentially using
    the order defined in ROUND_ORDER. The current round is defined as the first round that has
    at least one game without a recorded winner.
    
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
                # Stop if not every game in this round is complete.
                if not all(g.get("winner") and g["winner"].strip() for g in rounds[r]):
                    break

        # Identify the current round: the first visible round with an incomplete game.
        current_round = None
        for r in ROUND_ORDER:
            if r in visible_rounds:
                if any(not (g.get("winner") and g["winner"].strip()) for g in visible_rounds[r]):
                    current_round = r
                    break
        if not current_round and visible_rounds:
            current_round = list(visible_rounds.keys())[-1]
        return current_round, visible_rounds
    finally:
        session.close()

def calculate_best_case_scores():
    """
    Sequential best-case simulation.
    
    For each future round (starting at the current round), iterate over all games
    in that round. For each game not already fixed by DB results:
      - If the user's surviving teams include at least one team from the game,
        assume the best outcome and award the bonus (i.e. add ROUND_WEIGHTS for that round).
      - If the user holds both teams, choose the lexicographically larger team
        to "advance" (to maximize potential).
      - Otherwise (if the user holds none), nothing advances from that game.
      
    The survivors set is updated round‐by‐round and the total bonus is added to the user's
    current score.
    
    Returns:
        dict: Mapping of each user's full name to their best-case final score.
    """
    session = SessionLocal()
    try:
        # Get current user scores and picks.
        users = session.query(User).options(joinedload(User.picks)).all()
        current_scores = {}
        user_picks = {}
        for user in users:
            score_obj = session.query(UserScore).filter_by(user_id=user.user_id).first()
            current_scores[user.full_name] = score_obj.points if score_obj else 0.0
            user_picks[user.full_name] = {pick.team_name.strip() for pick in user.picks}

        # Get visible rounds and current round.
        current_round, visible_rounds = get_round_game_status()
        if not current_round:
            current_round = ROUND_ORDER[0]
        current_index = ROUND_ORDER.index(current_round)

        # Prune survivors from completed rounds (do not “fall back” if eliminated).
        user_survivors = {}
        for user in users:
            name = user.full_name
            survivors = set(user_picks.get(name, set()))
            for rnd in ROUND_ORDER:
                if rnd not in visible_rounds:
                    break
                games = visible_rounds[rnd]
                # If the round is fully complete, update survivors to only the winning teams.
                if all(g.get("winner") and g["winner"].strip() for g in games):
                    new_survivors = {g["winner"].strip() for g in games if g["winner"].strip() in survivors}
                    survivors = new_survivors
                else:
                    # For an incomplete round, subtract teams that lost in games already played.
                    for game in games:
                        game_teams = {game["team1"].strip(), game["team2"].strip()}
                        if game.get("winner") and game["winner"].strip():
                            w = game["winner"].strip()
                            survivors -= (game_teams - {w})
                    break
            user_survivors[name] = survivors

        # Build simulation bracket (regional rounds and interregional rounds).
        with open("tournament_bracket.json", "r") as f:
            bracket = json.load(f)
        regions = bracket.get("regions", [])
        regional_sim = {}
        for region in regions:
            region_name = region.get("region_name", "Unknown")
            teams_by_seed = {int(team["seed"]): team["team_name"].strip() for team in region.get("teams", [])}
            # Round of 64:
            r64 = []
            for pairing in FIRST_ROUND_PAIRINGS:
                teamA = teams_by_seed.get(pairing[0])
                teamB = teams_by_seed.get(pairing[1])
                r64.append({"teams": {teamA, teamB}})
            # Round of 32:
            r32 = []
            for i in range(4):
                teams = r64[2 * i]["teams"].union(r64[2 * i + 1]["teams"])
                r32.append({"teams": teams})
            # Sweet 16:
            s16 = []
            for i in range(2):
                teams = r32[2 * i]["teams"].union(r32[2 * i + 1]["teams"])
                s16.append({"teams": teams})
            # Elite 8:
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
                games.extend(regional_sim.get(region.get("region_name", "Unknown"), {}).get(rnd, []))
            simulated_bracket[rnd] = games
        # Final Four and Championship:
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

        # Consider all rounds from the current round onward.
        future_rounds = ROUND_ORDER[current_index:]

        def simulate_round_best_seq(survivors, rounds):
            bonus = 0
            current_survivors = survivors.copy()
            for rnd in rounds:
                round_bonus = 0
                new_survivors = set()
                # Get any fixed outcomes from DB for this round.
                db_games = visible_rounds.get(rnd, [])
                for game in simulated_bracket.get(rnd, []):
                    fixed_outcome = None
                    for db_game in db_games:
                        db_teams = {db_game["team1"].strip(), db_game["team2"].strip()}
                        if db_teams == game["teams"]:
                            if db_game.get("winner") and db_game["winner"].strip():
                                fixed_outcome = db_game["winner"].strip()
                            break
                    if fixed_outcome is not None:
                        # Game is already played; if the fixed outcome is in our survivors,
                        # then that team advances (but no bonus is awarded because it's fixed).
                        if fixed_outcome in current_survivors:
                            new_survivors.add(fixed_outcome)
                    else:
                        # Game not yet played. If the user holds at least one team, they can win it.
                        common = current_survivors.intersection(game["teams"])
                        if common:
                            round_bonus += ROUND_WEIGHTS.get(rnd, 1)
                            # For best-case, if the user holds both teams, choose the branch that maximizes potential.
                            chosen = max(common) if len(common) == 2 else next(iter(common))
                            new_survivors.add(chosen)
                        # Otherwise, nothing advances from this game.
                bonus += round_bonus
                current_survivors = new_survivors
                if not current_survivors:
                    break
            return bonus

        best_case_scores = {}
        for name, survivors in user_survivors.items():
            bonus = simulate_round_best_seq(survivors, future_rounds)
            best_case_scores[name] = current_scores.get(name, 0) + bonus

        return best_case_scores

    except Exception as e:
        logger.error(f"Error calculating best-case scores: {e}")
        return {}
    finally:
        session.close()

def calculate_worst_case_scores():
    """
    Sequential worst-case simulation.
    
    For each future round (starting at the current round), iterate over all games.
    For each game not already fixed:
      - Award the bonus (i.e. add ROUND_WEIGHTS for that round) only if the user's surviving teams
        include both teams in the game (a guaranteed win).
      - If so, update survivors by "collapsing" the pairing (choose the lexicographically smaller team).
      - If not, then if the user holds only one team, that team advances without bonus.
    If no survivors remain at any point, no further bonus is added.
    
    Returns:
        dict: Mapping of each user's full name to their worst-case final score.
    """
    session = SessionLocal()
    try:
        # Get user scores and picks.
        users = session.query(User).options(joinedload(User.picks)).all()
        current_scores = {}
        user_picks = {}
        for user in users:
            score_obj = session.query(UserScore).filter_by(user_id=user.user_id).first()
            current_scores[user.full_name] = score_obj.points if score_obj else 0.0
            user_picks[user.full_name] = {pick.team_name.strip() for pick in user.picks}

        # Get visible rounds and current round.
        current_round, visible_rounds = get_round_game_status()
        if not current_round:
            current_round = ROUND_ORDER[0]
        current_index = ROUND_ORDER.index(current_round)

        # Prune survivors as before (do not fall back to original picks).
        user_survivors = {}
        for user in users:
            name = user.full_name
            survivors = set(user_picks.get(name, set()))
            for rnd in ROUND_ORDER:
                if rnd not in visible_rounds:
                    break
                games = visible_rounds[rnd]
                if not all(g.get("winner") and g["winner"].strip() for g in games):
                    for game in games:
                        game_teams = {game["team1"].strip(), game["team2"].strip()}
                        if game.get("winner") and game["winner"].strip():
                            w = game["winner"].strip()
                            survivors -= (game_teams - {w})
                    break
                else:
                    new_survivors = {g["winner"].strip() for g in games if g["winner"].strip() in survivors}
                    survivors = new_survivors
            user_survivors[name] = survivors

        # Build simulation bracket.
        with open("tournament_bracket.json", "r") as f:
            bracket = json.load(f)
        regions = bracket.get("regions", [])
        regional_sim = {}
        for region in regions:
            region_name = region.get("region_name", "Unknown")
            teams_by_seed = {int(team["seed"]): team["team_name"].strip() for team in region.get("teams", [])}
            r64 = []
            for pairing in FIRST_ROUND_PAIRINGS:
                teamA = teams_by_seed.get(pairing[0])
                teamB = teams_by_seed.get(pairing[1])
                r64.append({"teams": {teamA, teamB}})
            r32 = []
            for i in range(4):
                teams = r64[2 * i]["teams"].union(r64[2 * i + 1]["teams"])
                r32.append({"teams": teams})
            s16 = []
            for i in range(2):
                teams = r32[2 * i]["teams"].union(r32[2 * i + 1]["teams"])
                s16.append({"teams": teams})
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
                games.extend(regional_sim.get(region.get("region_name", "Unknown"), {}).get(rnd, []))
            simulated_bracket[rnd] = games
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

        future_rounds = ROUND_ORDER[current_index:]

        def simulate_round_worst_seq(survivors, rounds):
            bonus = 0
            current_survivors = survivors.copy()
            for rnd in rounds:
                round_bonus = 0
                new_survivors = set()
                db_games = visible_rounds.get(rnd, [])
                for game in simulated_bracket.get(rnd, []):
                    fixed_outcome = None
                    for db_game in db_games:
                        db_teams = {db_game["team1"].strip(), db_game["team2"].strip()}
                        if db_teams == game["teams"]:
                            if db_game.get("winner") and db_game["winner"].strip():
                                fixed_outcome = db_game["winner"].strip()
                            break
                    if fixed_outcome is not None:
                        if fixed_outcome in current_survivors:
                            new_survivors.add(fixed_outcome)
                    else:
                        # Worst-case: only award bonus if the user holds both teams.
                        common = current_survivors.intersection(game["teams"])
                        if len(common) == 2:
                            round_bonus += ROUND_WEIGHTS.get(rnd, 1)
                            # For worst-case, collapse by choosing the lexicographically smaller team.
                            chosen = min(common)
                            new_survivors.add(chosen)
                        else:
                            # If only one team is held, that team advances but no bonus is awarded.
                            if common:
                                new_survivors.update(common)
                bonus += round_bonus
                current_survivors = new_survivors
                if not current_survivors:
                    break
            return bonus

        worst_case_scores = {}
        for name, survivors in user_survivors.items():
            bonus = simulate_round_worst_seq(survivors, future_rounds)
            worst_case_scores[name] = current_scores.get(name, 0) + bonus

        return worst_case_scores

    except Exception as e:
        logger.error(f"Error calculating worst-case scores: {e}")
        return {}
    finally:
        session.close()

def calculate_maximum_possible_score():
    """
    Calculates the theoretical maximum score a player can achieve under the one-team-per-seed rule,
    using the round weights and win multipliers defined in constants.py.

    The maximum score is computed by summing, for each round in ROUND_ORDER,
      (win multiplier for the round) * (round weight from ROUND_WEIGHTS).

    Returns:
        float: The maximum theoretical score.
    """
    # Defines the maximum number of teams that can survive per round in a perfect bracket
    multipliers = [16, 16, 8, 4, 2, 1]
    
    max_score = 0
    for i, round_name in enumerate(ROUND_ORDER):
        weight = ROUND_WEIGHTS.get(round_name, 1)
        max_score += weight * multipliers[i]
    return max_score