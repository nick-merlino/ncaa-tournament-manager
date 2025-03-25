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
    Calculates best-case final scores for each player by simulating the remainder
    of the tournament using a round-by-round elimination approach.

    First, the simulation determines each player’s surviving teams based on
    completed rounds (from get_round_game_status). Then, for each future round,
    it iterates over the simulated bracket: for each game, if any surviving team
    appears, it awards the round’s bonus and advances one team from that game.
    
    Returns:
        dict: Mapping of player full name to best-case final score (float).
    """
    from sqlalchemy.orm import joinedload
    import json
    from constants import ROUND_ORDER, ROUND_WEIGHTS, FIRST_ROUND_PAIRINGS
    session = SessionLocal()
    try:
        # Retrieve users along with their picks.
        users = session.query(User).options(joinedload(User.picks)).all()
        
        # Build current scores from UserScore.
        current_scores = {}
        for user in users:
            score_obj = session.query(UserScore).filter_by(user_id=user.user_id).first()
            current_scores[user.full_name] = score_obj.points if score_obj else 0.0
        
        # Build mapping of user picks: username -> set of all teams originally picked.
        user_picks = {}
        for user in users:
            picks = {pick.team_name.strip() for pick in user.picks}
            user_picks[user.full_name] = picks
        
        # Determine the current round and visible rounds (completed rounds).
        current_round, visible_rounds = get_round_game_status()
        if not current_round:
            current_round = ROUND_ORDER[0]
        current_index = ROUND_ORDER.index(current_round)
        
        # For each user, determine surviving teams based on completed rounds.
        # If no rounds are complete, use the original picks.
        user_survivors = {}
        for user in users:
            username = user.full_name
            surviving = set(user_picks.get(username, set()))
            # Iterate over visible rounds that are complete.
            for rnd in ROUND_ORDER:
                # Only consider rounds that are in visible_rounds.
                if rnd not in visible_rounds:
                    break
                games = visible_rounds[rnd]
                # Only update survivors if every game in the round is complete.
                if not all(game.get("winner") and game["winner"].strip() for game in games):
                    break
                # In a complete round, only teams that won in that round survive.
                new_survivors = set()
                for game in games:
                    w = game.get("winner", "").strip()
                    if w in surviving:
                        new_survivors.add(w)
                surviving = new_survivors
            # If none survived from completed rounds, keep survivors as empty.
            user_survivors[username] = surviving if surviving else set()
            # If a user had no completed-round survivors, they still have a chance
            # from their original picks.
            if not user_survivors[username]:
                user_survivors[username] = set(user_picks.get(username, set()))
        
        # --- Build the simulated bracket (from the official bracket JSON) ---
        with open("tournament_bracket.json", "r") as f:
            bracket = json.load(f)
        regions = bracket.get("regions", [])
        
        # Build simulated rounds for each region: Round of 64, Round of 32, Sweet 16, Elite 8.
        regional_sim = {}
        for region in regions:
            region_name = region["region_name"]
            teams_by_seed = {team["seed"]: team["team_name"].strip() for team in region["teams"]}
            # Round of 64 using FIRST_ROUND_PAIRINGS.
            r64 = []
            for pairing in FIRST_ROUND_PAIRINGS:
                teamA = teams_by_seed.get(pairing[0])
                teamB = teams_by_seed.get(pairing[1])
                r64.append({"teams": {teamA, teamB}})
            # Round of 32: pair adjacent games.
            r32 = []
            for i in range(4):
                teams = r64[2*i]["teams"].union(r64[2*i+1]["teams"])
                r32.append({"teams": teams})
            # Sweet 16.
            s16 = []
            for i in range(2):
                teams = r32[2*i]["teams"].union(r32[2*i+1]["teams"])
                s16.append({"teams": teams})
            # Elite 8.
            e8 = [{"teams": s16[0]["teams"].union(s16[1]["teams"])}]
            regional_sim[region_name] = {
                "Round of 64": r64,
                "Round of 32": r32,
                "Sweet 16": s16,
                "Elite 8": e8
            }
        
        # Combine regional games for rounds up through Elite 8.
        simulated_bracket = {}
        for rnd in ["Round of 64", "Round of 32", "Sweet 16", "Elite 8"]:
            games = []
            for region in regions:
                region_name = region["region_name"]
                games.extend(regional_sim[region_name][rnd])
            simulated_bracket[rnd] = games
        
        # Build interregional rounds.
        # Final Four: pair regions as ordered in the JSON.
        final_four = []
        if len(regions) >= 4:
            teams_ff0 = regional_sim[regions[0]["region_name"]]["Elite 8"][0]["teams"].union(
                        regional_sim[regions[1]["region_name"]]["Elite 8"][0]["teams"])
            final_four.append({"teams": teams_ff0})
            teams_ff1 = regional_sim[regions[2]["region_name"]]["Elite 8"][0]["teams"].union(
                        regional_sim[regions[3]["region_name"]]["Elite 8"][0]["teams"])
            final_four.append({"teams": teams_ff1})
        simulated_bracket["Final Four"] = final_four
        
        # Championship: union of the two Final Four games.
        if len(final_four) == 2:
            simulated_bracket["Championship"] = [{"teams": final_four[0]["teams"].union(final_four[1]["teams"])}]
        else:
            simulated_bracket["Championship"] = []
        
        # --- Simulate future rounds using round-by-round elimination ---
        # Future rounds to simulate: from the current round (as determined by get_round_game_status)
        # to the end of the tournament.
        future_rounds = ROUND_ORDER[current_index:]
        
        best_case_scores = {}
        for user in users:
            username = user.full_name
            current = current_scores.get(username, 0)
            bonus = 0
            # Start simulation with surviving teams from completed rounds.
            surviving = set(user_survivors.get(username, set()))
            # For each future round, simulate games round-by-round.
            for rnd in future_rounds:
                games = simulated_bracket.get(rnd, [])
                new_survivors = set()
                # Process each game independently.
                for game in games:
                    common = surviving.intersection(game["teams"])
                    if common:
                        # Award bonus for this game (once).
                        bonus += ROUND_WEIGHTS.get(rnd, 1)
                        # Advance one team (simulate best-case outcome).
                        new_survivors.add(next(iter(common)))
                # Update surviving teams for next round.
                surviving = new_survivors
            best_case_scores[username] = current + bonus
        
        return best_case_scores
    except Exception as e:
        logger.error(f"Error in calculate_best_case_scores: {e}")
        return {}
    finally:
        session.close()
