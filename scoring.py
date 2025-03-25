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
import json
from constants import ROUND_ORDER, ROUND_WEIGHTS, FIRST_ROUND_PAIRINGS
from sqlalchemy.orm import joinedload

def calculate_scoring():
    """
    Calculates and updates user scores based on tournament results,
    considering only the visible rounds.
    Each round win contributes points based on ROUND_WEIGHTS.
    """
    session = SessionLocal()
    try:
        session.query(UserScore).delete()
        results = session.query(TournamentResult).all()
        # Only consider rounds that are visible
        _, visible_rounds = get_round_game_status()
        visible_round_keys = set(visible_rounds.keys())
        
        winners_by_round = {}
        for result in results:
            if result.winner:
                base_round = result.round_name.split('-', 1)[0].strip()
                # Only add wins from rounds that are visible
                if base_round in visible_round_keys:
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

def calculate_best_case_scores():
    """
    Calculates best-case final scores for each player by exhaustively simulating
    the remainder of the tournament. It first prunes each player's surviving teams
    based on completed rounds, and then, for each future round, it recursively
    explores all choices when multiple teams could advance. This ensures that if a
    player holds conflicting teams, the simulation picks the advancement that yields
    the maximum bonus points.
    
    For debugging, if a user has 15 points and 7 surviving teams, the simulated
    bracket structure is logged.
    
    Returns:
        dict: Mapping of player full name to best-case final score (float).
    """
    from sqlalchemy.orm import joinedload
    import json, itertools
    from constants import ROUND_ORDER, ROUND_WEIGHTS, FIRST_ROUND_PAIRINGS
    session = SessionLocal()
    try:
        # Get users and their picks.
        users = session.query(User).options(joinedload(User.picks)).all()
        current_scores = {}
        user_picks = {}
        for user in users:
            score_obj = session.query(UserScore).filter_by(user_id=user.user_id).first()
            current_scores[user.full_name] = score_obj.points if score_obj else 0.0
            picks = {pick.team_name.strip() for pick in user.picks}
            user_picks[user.full_name] = picks

        # Determine completed rounds via visible_rounds.
        current_round, visible_rounds = get_round_game_status()
        if not current_round:
            current_round = ROUND_ORDER[0]
        current_index = ROUND_ORDER.index(current_round)
        # Prune survivors based on completed rounds.
        user_survivors = {}
        for user in users:
            username = user.full_name
            surviving = set(user_picks.get(username, set()))
            for rnd in ROUND_ORDER:
                if rnd not in visible_rounds:
                    break
                games = visible_rounds[rnd]
                # Only update survivors if every game in the round is complete.
                if not all(game.get("winner") and game["winner"].strip() for game in games):
                    break
                new_survivors = set()
                for game in games:
                    w = game.get("winner", "").strip()
                    if w in surviving:
                        new_survivors.add(w)
                surviving = new_survivors
            # If no survivors from completed rounds, revert to original picks.
            if not surviving:
                surviving = set(user_picks.get(username, set()))
            user_survivors[username] = surviving

        # --- Build the simulated bracket from tournament_bracket.json ---
        with open("tournament_bracket.json", "r") as f:
            bracket = json.load(f)
        regions = bracket.get("regions", [])

        # For each region, simulate rounds: Round of 64, Round of 32, Sweet 16, Elite 8.
        regional_sim = {}
        for region in regions:
            region_name = region["region_name"]
            teams_by_seed = {team["seed"]: team["team_name"].strip() for team in region["teams"]}
            # Build Round of 64 using FIRST_ROUND_PAIRINGS.
            r64 = []
            for pairing in FIRST_ROUND_PAIRINGS:
                teamA = teams_by_seed.get(pairing[0])
                teamB = teams_by_seed.get(pairing[1])
                r64.append({"teams": {teamA, teamB}})
            # Build Round of 32 (each game from two Round of 64 games).
            r32 = []
            for i in range(4):
                teams = r64[2*i]["teams"].union(r64[2*i+1]["teams"])
                r32.append({"teams": teams})
            # Build Sweet 16.
            s16 = []
            for i in range(2):
                teams = r32[2*i]["teams"].union(r32[2*i+1]["teams"])
                s16.append({"teams": teams})
            # Build Elite 8.
            e8 = [{"teams": s16[0]["teams"].union(s16[1]["teams"])}]
            regional_sim[region_name] = {
                "Round of 64": r64,
                "Round of 32": r32,
                "Sweet 16": s16,
                "Elite 8": e8
            }

        # Combine regional games into rounds up through Elite 8.
        simulated_bracket = {}
        for rnd in ["Round of 64", "Round of 32", "Sweet 16", "Elite 8"]:
            games = []
            for region in regions:
                region_name = region["region_name"]
                games.extend(regional_sim[region_name][rnd])
            simulated_bracket[rnd] = games

        # Build interregional rounds.
        final_four = []
        if len(regions) >= 4:
            teams_ff0 = set(regional_sim[regions[0]["region_name"]]["Elite 8"][0]["teams"]).union(
                        set(regional_sim[regions[1]["region_name"]]["Elite 8"][0]["teams"]))
            final_four.append({"teams": teams_ff0})
            teams_ff1 = set(regional_sim[regions[2]["region_name"]]["Elite 8"][0]["teams"]).union(
                        set(regional_sim[regions[3]["region_name"]]["Elite 8"][0]["teams"]))
            final_four.append({"teams": teams_ff1})
        simulated_bracket["Final Four"] = final_four
        if len(final_four) == 2:
            championship_set = set(final_four[0]["teams"]).union(set(final_four[1]["teams"]))
            simulated_bracket["Championship"] = [{"teams": championship_set}]
        else:
            simulated_bracket["Championship"] = []


        # --- Debug logging for a specific test scenario ---
        # If any user has a current score of 15 and exactly 7 surviving teams, log the simulated bracket.
        def convert_sets(obj):
            if isinstance(obj, set):
                return list(obj)
            raise TypeError
        for user in users:
            username = user.full_name
            if current_scores.get(username, 0) == 15 and len(user_survivors.get(username, set())) == 7:
                logger.info("Debug: User '%s' has 15 points and 7 surviving teams. Simulated bracket structure:\n%s",
                            username, json.dumps(simulated_bracket, indent=2, default=convert_sets))

        
        # --- Recursive exhaustive simulation ---
        future_rounds = ROUND_ORDER[current_index:]
        def simulate_round(surviving, round_index):
            if round_index >= len(future_rounds):
                return 0
            rnd = future_rounds[round_index]
            games = simulated_bracket.get(rnd, [])
            weight = ROUND_WEIGHTS.get(rnd, 1)
            possibilities = []
            for game in games:
                common = surviving.intersection(game["teams"])
                if common:
                    possibilities.append(list(common))
                else:
                    possibilities.append([None])
            best = 0
            for choice in itertools.product(*possibilities):
                bonus_this_round = sum(weight if winner is not None else 0 for winner in choice)
                new_surviving = {winner for winner in choice if winner is not None}
                future_bonus = simulate_round(new_surviving, round_index + 1)
                best = max(best, bonus_this_round + future_bonus)
            return best

        best_case_scores = {}
        for user in users:
            username = user.full_name
            current = current_scores.get(username, 0)
            surviving = set(user_survivors.get(username, set()))
            bonus = simulate_round(surviving, 0)
            best_case_scores[username] = current + bonus

        return best_case_scores
    except Exception as e:
        logger.error(f"Error in calculate_best_case_scores: {e}")
        return {}
    finally:
        session.close()
