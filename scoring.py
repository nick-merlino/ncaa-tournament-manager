"""
scoring.py

This module computes user scores based on tournament results.
It is structured in seven steps:

  1. Base Score Calculation: Fully implemented in calculate_scoring().
  2. Determining the Current Tournament State: Implemented via get_round_game_status() 
     and get_round_game_status_by_region().
  3. Building the Bracket: (Used only for initial seeding.)
  4. (Active set logic removed in favor of dynamic matchups.)
  5. Regional Simulation of Future Rounds: Now builds matchups dynamically from entered games.
  6. Interregional Simulation: Called exactly once after regional simulation.
  7. Final Score Calculation for Future Rounds: Implemented in calculate_worst_case_scores()
     and calculate_best_case_scores().

Logging (prefixed with "[Morey Trace]") is output only if "Morey" appears in the username.
"""

from pprint import pprint
import json
import datetime
from collections import defaultdict
from config import logger
from constants import ROUND_ORDER, ROUND_WEIGHTS, FIRST_ROUND_PAIRINGS
from db import SessionLocal, TournamentResult, User, UserScore

# Define the final round for each region.
MAX_REGIONAL_ROUND = "Elite 8"

# ---------------------------
# Step 1: Base Score Calculation
# ---------------------------
def calculate_scoring():
    """
    Calculates base scores for users based on finished games.
    For each finished game, if a user's pick matches the winner, add that round's weight.
    """
    session = SessionLocal()
    try:
        session.query(UserScore).delete()
        session.commit()
        results = session.query(TournamentResult).all()
        current_round, visible = get_round_game_status()  # global current round info
        if current_round in ROUND_ORDER:
            allowed_rounds = set(ROUND_ORDER[:ROUND_ORDER.index(current_round) + 1])
        else:
            allowed_rounds = set(ROUND_ORDER)
        winners_by_round = defaultdict(set)
        for game in results:
            if game.winner and game.winner.strip():
                base_round = game.round_name.split('-', 1)[0].strip()
                if base_round in allowed_rounds:
                    winners_by_round[base_round].add(game.winner.strip())
        users = session.query(User).all()
        for user in users:
            total = 0.0
            for pick in user.picks:
                for rnd, winners in winners_by_round.items():
                    if pick.team_name.strip() in winners:
                        total += ROUND_WEIGHTS.get(rnd, 1)
            score_obj = UserScore(
                user_id=user.user_id,
                points=total,
                last_updated=datetime.datetime.utcnow().isoformat()
            )
            session.add(score_obj)
        session.commit()
    except Exception as e:
        logger.error(f"Error calculating scoring: {e}")
        session.rollback()
    finally:
        session.close()

# ---------------------------
# Step 2: Determining the Current Tournament State
# ---------------------------
def get_round_game_status():
    """
    Returns a global view of finished game data:
      - current: the first round in ROUND_ORDER where not all games are complete.
      - visible: a dictionary keyed by round names with lists of game dicts.
    """
    session = SessionLocal()
    try:
        results = session.query(TournamentResult).all()
        rounds = defaultdict(list)
        for game in results:
            base_round = game.round_name.split('-', 1)[0].strip()
            rounds[base_round].append({
                "game_id": game.game_id,
                "team1": game.team1,
                "team2": game.team2,
                "winner": game.winner.strip() if game.winner else ""
            })
        visible = {}
        current = None
        for r in ROUND_ORDER:
            if r in rounds:
                visible[r] = rounds[r]
                if not all(g.get("winner") for g in rounds[r]):
                    current = r
                    break
        if not current and visible:
            current = list(visible.keys())[-1]
        elif not current:
            current = ROUND_ORDER[0]
        return current, visible
    finally:
        session.close()


def get_round_game_status_by_region():
    """
    Returns region-specific game data.
    Output:
      - current_by_region: { region: current_round }
      - visible_by_region: { region: { round_name: [list of game dicts] } }
      
    This version derives the region for a game from the tournament_bracket.json
    if the TournamentResult's region attribute is missing or set to "Unknown."
    """
    session = SessionLocal()
    try:
        results = session.query(TournamentResult).all()
        
        with open("tournament_bracket.json", "r") as f:
            tournament_data = json.load(f)
        team_to_region = {}
        regions_data = tournament_data.get("regions", [])
        for region in regions_data:
            region_name = region.get("region_name", "Unknown")
            for team in region.get("teams", []):
                team_name = team.get("team_name", "").strip()
                if team_name:
                    team_to_region[team_name] = region_name

        rounds_by_region = {}
        for game in results:
            region = getattr(game, 'region', None)
            if not region or region.strip().lower() == "unknown":
                region = team_to_region.get(game.team1.strip())
                if not region:
                    region = team_to_region.get(game.team2.strip(), "Unknown")
            
            if region not in rounds_by_region:
                rounds_by_region[region] = defaultdict(list)
            base_round = game.round_name.split('-', 1)[0].strip()
            rounds_by_region[region][base_round].append({
                "game_id": game.game_id,
                "team1": game.team1,
                "team2": game.team2,
                "winner": game.winner.strip() if game.winner else "",
                "region": region
            })
        visible_by_region = {}
        current_by_region = {}
        for region, rounds in rounds_by_region.items():
            visible = {}
            current = None
            for r in ROUND_ORDER:
                if r in rounds:
                    visible[r] = rounds[r]
                    if not all(g.get("winner") for g in rounds[r]):
                        current = r
                        break
            if not current and visible:
                current = list(visible.keys())[-1]
            elif not current:
                current = ROUND_ORDER[0]
            visible_by_region[region] = visible
            current_by_region[region] = current
        return current_by_region, visible_by_region
    finally:
        session.close()

# ---------------------------
# Step 3: Building the Bracket (For initial seeding)
# ---------------------------
def build_regional_bracket(region):
    """
    Builds the tournament bracket for a given region.
    This function is used for seeding purposes.
    
    Process:
      1. Map each seed to its team name.
      2. Generate the Round of 64 using FIRST_ROUND_PAIRINGS.
      3. Iteratively build subsequent rounds by pairing winners.
    
    Returns:
      A dictionary with round names as keys and lists of matchup tuples as values.
    """
    teams = region.get("teams", [])
    seed_to_team = {int(team["seed"]): team["team_name"].strip() for team in teams}
    
    round64 = []
    for pairing in FIRST_ROUND_PAIRINGS:
        team1 = seed_to_team.get(pairing[0])
        team2 = seed_to_team.get(pairing[1])
        if team1 and team2:
            round64.append((team1, team2))
        else:
            logger.error(f"Missing team for seeds: {pairing} in region {region.get('region_name')}")
            raise ValueError("Incomplete bracket data in region")
    
    bracket = {"Round of 64": round64}
    current_round_index = ROUND_ORDER.index("Round of 64")
    
    while current_round_index < len(ROUND_ORDER) - 1:
        base_round = ROUND_ORDER[current_round_index]
        next_round = ROUND_ORDER[current_round_index + 1]
        prev_matchups = bracket.get(base_round, [])
        if len(prev_matchups) % 2 != 0 or len(prev_matchups) == 0:
            break
        next_matchups = []
        for i in range(0, len(prev_matchups), 2):
            teams_set = set(prev_matchups[i]) | set(prev_matchups[i+1])
            next_matchups.append(tuple(sorted(teams_set)))
        bracket[next_round] = next_matchups
        current_round_index += 1
    
    return bracket

# ---------------------------
# Step 5: Dynamic Regional Simulation of Future Rounds
# ---------------------------
def simulate_dynamic_bracket_worst(region_name, visible_by_region, player_pick_set, current_round, username=None):
    """
    Simulate the remaining rounds in a region under worst-case assumptions dynamically.
    Starting from the current round’s entered games, each matchup is a pair of teams.
    
    For each matchup:
      - If the game is finished (its 'winner' field is non-empty), that winner is used.
      - Otherwise, if one team is not in the player's picks, choose that team to force a loss.
      - If both teams are in the player's picks, choose arbitrarily and award bonus points.
    
    Winners from the round are paired for the next round until one winner remains.
    
    If the current round equals MAX_REGIONAL_ROUND and all games are entered,
    the simulation stops and returns the entered champion.
    
    Returns:
      (total_bonus, final_winner)
    """
    total_bonus = 0
    region_games = visible_by_region.get(region_name, {})
    current_games = region_games.get(current_round, [])
    current_matchups = []
    for game in current_games:
        team1 = game.get("team1", "").strip()
        team2 = game.get("team2", "").strip()
        if team1 and team2:
            current_matchups.append((team1, team2))
    round_index = ROUND_ORDER.index(current_round)
    
    # If at final regional round and all games are complete, collapse results.
    if current_games and all(game.get("winner", "").strip() for game in current_games):
        if ROUND_ORDER[round_index] == MAX_REGIONAL_ROUND:
            finished_winners = [game.get("winner", "").strip() for game in current_games]
            # Deterministically pair winners until one remains.
            while len(finished_winners) > 1:
                new_list = []
                for i in range(0, len(finished_winners), 2):
                    if i+1 < len(finished_winners):
                        new_list.append(finished_winners[i])
                    else:
                        new_list.append(finished_winners[i])
                finished_winners = new_list
            return total_bonus, finished_winners[0]
    
    final_winner = None
    while current_matchups:
        round_name = ROUND_ORDER[round_index]
        new_winners = []
        for matchup in current_matchups:
            finished_result = None
            for game in current_games:
                game_matchup = (game.get("team1", "").strip(), game.get("team2", "").strip())
                if set(game_matchup) == set(matchup) and game.get("winner", "").strip():
                    finished_result = game.get("winner", "").strip()
                    break
            if finished_result:
                chosen = finished_result
            else:
                player_in = set(matchup).intersection(player_pick_set) if player_pick_set else set()
                non_player = set(matchup) - player_in
                if non_player:
                    chosen = list(non_player)[0]
                else:
                    chosen = list(matchup)[0]
                    total_bonus += int(ROUND_WEIGHTS.get(round_name, 1))
            new_winners.append(chosen)
            if username and "Morey" in username:
                logger.info(f"[Morey Trace] (Worst) {round_name} matchup {matchup} => winner: {chosen}")
        if len(new_winners) < 2:
            final_winner = new_winners[0] if new_winners else None
            break
        next_matchups = []
        for i in range(0, len(new_winners), 2):
            if i+1 < len(new_winners):
                next_matchups.append((new_winners[i], new_winners[i+1]))
        current_matchups = next_matchups
        current_games = []  # Future rounds: no entered games.
        round_index += 1
        final_winner = new_winners[0] if new_winners else None

    return total_bonus, final_winner

def simulate_dynamic_bracket_best_combined(region_name, visible_by_region, player_pick_set, current_round, username=None):
    """
    Combined simulation for best-case in a region that returns both the overall winner and bonus.
    Starting from the current round’s entered games, each matchup is a pair of teams.
    
    For each matchup:
      - If the game is finished, that winner is used.
      - Otherwise, if one team is in the player's picks, choose that team (and award bonus points).
      - If neither is in the player's picks, choose arbitrarily.
    
    Winners are paired until one champion remains.
    
    Returns:
      (total_bonus, overall_winner)
    """
    total_bonus = 0
    region_games = visible_by_region.get(region_name, {})
    current_games = region_games.get(current_round, [])
    current_matchups = []
    for game in current_games:
        team1 = game.get("team1", "").strip()
        team2 = game.get("team2", "").strip()
        if team1 and team2:
            current_matchups.append((team1, team2))
    round_index = ROUND_ORDER.index(current_round)
    overall_winner = None

    while current_matchups:
        round_name = ROUND_ORDER[round_index]
        new_winners = []
        for matchup in current_matchups:
            finished_result = None
            for game in current_games:
                game_matchup = (game.get("team1", "").strip(), game.get("team2", "").strip())
                if set(game_matchup) == set(matchup) and game.get("winner", "").strip():
                    finished_result = game.get("winner", "").strip()
                    break
            if finished_result:
                chosen = finished_result
            else:
                candidate = set(matchup).intersection(player_pick_set) if player_pick_set else set()
                if candidate:
                    chosen = list(candidate)[0]
                    total_bonus += int(ROUND_WEIGHTS.get(round_name, 1))
                else:
                    chosen = list(matchup)[0]
            new_winners.append(chosen)
            if username and "Morey" in username:
                logger.info(f"[Morey Trace] (Best) {round_name} matchup {matchup} => winner: {chosen}")
        if len(new_winners) < 2:
            overall_winner = new_winners[0] if new_winners else None
            break
        next_matchups = []
        for i in range(0, len(new_winners), 2):
            if i+1 < len(new_winners):
                next_matchups.append((new_winners[i], new_winners[i+1]))
        current_matchups = next_matchups
        current_games = []  # Future rounds: no entered games.
        round_index += 1
        overall_winner = new_winners[0] if new_winners else None

    return total_bonus, overall_winner

# ---------------------------
# Step 6: Interregional Simulation
# ---------------------------
def simulate_interregional_bracket(regional_champs):
    """
    Simulate the interregional bracket given the champions from each region.
    
    Returns:
      dict: { "Final Four": [tuple, tuple], "Championship": [tuple] }
    """
    regions = list(regional_champs.keys())
    if len(regions) != 4:
        logger.error("Interregional simulation requires exactly 4 regions.")
        raise ValueError("Interregional simulation requires 4 regions.")
    final_four = [
        (regional_champs[regions[0]], regional_champs[regions[1]]),
        (regional_champs[regions[2]], regional_champs[regions[3]])
    ]
    championship = [tuple(sorted(final_four[0] + final_four[1]))]
    if "Morey" in "".join(regional_champs.values()):
        logger.info(f"[Morey Trace] (Interregional) Final Four: {final_four}, Championship: {championship}")
    return {"Final Four": final_four, "Championship": championship}

# ---------------------------
# Step 7: Final Score Calculation for Future Rounds
# ---------------------------
def calculate_worst_case_scores():
    """
    Calculates worst-case final scores for all users by combining:
      - Base score (from finished games)
      - Worst-case bonus from regional simulations
      - Worst-case bonus from one interregional simulation (Final Four/Championship)
    """
    with open("tournament_bracket.json", "r") as f:
        tournament_data = json.load(f)
    regions = tournament_data.get("regions", [])
    session = SessionLocal()
    worst_scores = {}
    try:
        current_by_region, visible_by_region = get_round_game_status_by_region()
        users = session.query(User).all()
        for user in users:
            score_obj = session.query(UserScore).filter_by(user_id=user.user_id).first()
            base_score = score_obj.points if score_obj else 0.0
            player_pick_set = {pick.team_name.strip() for pick in user.picks}
            regional_winners = {}
            bonus_total = 0
            # Regional simulation phase: one call per region.
            for region in regions:
                region_name = region.get("region_name", "Unknown")
                current_round = current_by_region.get(region_name, ROUND_ORDER[0])
                bonus, winner = simulate_dynamic_bracket_worst(
                    region_name, visible_by_region, player_pick_set, current_round, username=user.full_name
                )
                bonus_total += bonus
                if winner:
                    regional_winners[region_name] = winner
                if user.full_name and "Morey" in user.full_name:
                    logger.info(f"[Morey Trace] (Worst) Region {region_name}: bonus {bonus}, winner {winner}")
            # Interregional simulation phase: run once for all four regional champions.
            inter_bonus = 0
            if len(regional_winners) == 4:
                inter = simulate_interregional_bracket(regional_winners)
                # Simulate interregional matchups worst-case.
                # Here, award bonus only if an entire matchup is comprised solely of player's picks.
                ff_bonuses = []
                for matchup in inter["Final Four"]:
                    player_in = set(matchup).intersection(player_pick_set) if player_pick_set else set()
                    bonus_val = int(ROUND_WEIGHTS.get("Final Four", 1)) if set(matchup) == player_in else 0
                    ff_bonuses.append(bonus_val)
                    if user.full_name and "Morey" in user.full_name:
                        logger.info(f"[Morey Trace] (Worst) Interregional matchup {matchup} bonus: {bonus_val}")
                inter_bonus = min(ff_bonuses) if ff_bonuses else 0
            worst_scores[user.full_name] = base_score + bonus_total + inter_bonus
            if user.full_name and "Morey" in user.full_name:
                logger.info(f"[Morey Trace] (Worst) Final score for {user.full_name}: base {base_score} + bonus {bonus_total + inter_bonus}")
        return worst_scores
    except Exception as e:
        logger.error(f"Error calculating worst-case scores: {e}")
        session.rollback()
        return {}
    finally:
        session.close()


def calculate_best_case_scores():
    """
    Calculates best-case final scores for all users by combining:
      - Base score (from finished games)
      - Best-case bonus from regional simulations
      - Best-case bonus from one interregional simulation (Final Four/Championship)
    
    For each region, the combined best-case simulation is run only once.
    """
    with open("tournament_bracket.json", "r") as f:
        tournament_data = json.load(f)
    regions = tournament_data.get("regions", [])
    session = SessionLocal()
    best_scores = {}
    try:
        current_by_region, visible_by_region = get_round_game_status_by_region()
        users = session.query(User).all()
        for user in users:
            score_obj = session.query(UserScore).filter_by(user_id=user.user_id).first()
            base_score = score_obj.points if score_obj else 0.0
            player_pick_set = {pick.team_name.strip() for pick in user.picks}
            overall_regional_winners = {}
            player_regional_bonus = 0
            # Regional simulation phase for best-case.
            for region in regions:
                region_name = region.get("region_name", "Unknown")
                current_round = current_by_region.get(region_name, ROUND_ORDER[0])
                bonus, winner = simulate_dynamic_bracket_best_combined(
                    region_name, visible_by_region, player_pick_set, current_round, username=user.full_name
                )
                overall_regional_winners[region_name] = winner
                player_regional_bonus += bonus
                if user.full_name and "Morey" in user.full_name:
                    logger.info(f"[Morey Trace] (Best) Region {region_name}: bonus {bonus}, winner {winner}")
            player_interregional_bonus = 0
            # Interregional simulation phase for best-case.
            if len(overall_regional_winners) == 4:
                inter = simulate_interregional_bracket(overall_regional_winners)
                ff_bonus = 0
                for matchup in inter["Final Four"]:
                    candidate = set(matchup).intersection(player_pick_set) if player_pick_set else set()
                    if candidate:
                        ff_bonus += int(ROUND_WEIGHTS.get("Final Four", 1))
                    else:
                        ff_bonus += 0
                    if user.full_name and "Morey" in user.full_name:
                        logger.info(f"[Morey Trace] (Best) Interregional matchup {matchup} bonus: {int(ROUND_WEIGHTS.get('Final Four', 1)) if candidate else 0}")
                champ_bonus = 0
                for matchup in inter["Championship"]:
                    candidate = set(matchup).intersection(player_pick_set) if player_pick_set else set()
                    if candidate:
                        champ_bonus += int(ROUND_WEIGHTS.get("Championship", 1))
                    else:
                        champ_bonus += 0
                    if user.full_name and "Morey" in user.full_name:
                        logger.info(f"[Morey Trace] (Best) Championship matchup {matchup} bonus: {int(ROUND_WEIGHTS.get('Championship', 1)) if candidate else 0}")
                player_interregional_bonus = ff_bonus + champ_bonus
            best_scores[user.full_name] = base_score + player_regional_bonus + player_interregional_bonus
            if user.full_name and "Morey" in user.full_name:
                logger.info(f"[Morey Trace] (Best) Final score for {user.full_name}: base {base_score} + bonus {player_regional_bonus + player_interregional_bonus}")
        return best_scores
    except Exception as e:
        logger.error(f"Error calculating best-case scores: {e}")
        session.rollback()
        return {}
    finally:
        session.close()
