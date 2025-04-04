"""
scoring.py

This module computes user scores based on tournament results.
It is structured in seven steps:

  1. Base Score Calculation: Fully implemented in calculate_scoring().
  2. Determining the Current Tournament State: Implemented via get_round_game_status() 
     and get_round_game_status_by_region().
  3. Building the Bracket: Implemented in build_regional_bracket().
  4. Determining the Active Set: Implemented in get_active_set().
  5. Regional Simulation of Future Rounds: Implemented in simulate_round_worst,
     simulate_round_best, simulate_bracket_worst_case_region, and simulate_bracket_best_case_region.
  6. Interregional Simulation: Implemented in simulate_interregional_bracket().
  7. Final Score Calculation for Future Rounds: Implemented in calculate_worst_case_scores()
     and calculate_best_case_scores().

Logging (prefixed with "[Jody Trace]") is output only if "Jody" appears in the username.
"""

from pprint import pprint
import json
import datetime
from collections import defaultdict
from config import logger
from constants import ROUND_ORDER, ROUND_WEIGHTS, FIRST_ROUND_PAIRINGS
from db import SessionLocal, TournamentResult, User, UserScore

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
                # First round where not all games have a winner
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
        
        # Load tournament bracket data to build a mapping from team names to region names.
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
            # Attempt to get the region from the game object.
            region = getattr(game, 'region', None)
            # If region is missing or unknown, derive it from team names.
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
# Step 3: Building the Bracket
# ---------------------------
def build_regional_bracket(region):
    """
    Builds the tournament bracket for a given region.
    
    Process:
      1. Map each seed to its team name.
      2. Generate the Round of 64 using FIRST_ROUND_PAIRINGS.
      3. Iteratively build subsequent rounds (e.g., Round of 32, Sweet 16, Elite 8) by pairing the previous round's matchups.
    
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
# Step 4: Determining the Active Set
# ---------------------------
def get_active_set(region, visible_by_region):
    """
    For a given region, return the set of teams still in contention based on finished and partially finished rounds.
    
    If a round is fully complete, the active set becomes the winners of that round.
    If a round is partially complete, the active set is the union of winners (if available) and both teams for unfinished games.
    """
    region_visible = visible_by_region.get(region, {})
    active = set()
    for r in ROUND_ORDER:
        if r not in region_visible:
            break
        games = region_visible[r]
        if games and all(game.get("winner") for game in games):
            active = {game["winner"].strip() for game in games}
        else:
            for game in games:
                if game.get("winner"):
                    active.add(game["winner"].strip())
                else:
                    active.add(game.get("team1", "").strip())
                    active.add(game.get("team2", "").strip())
            break
    return active

# ---------------------------
# Step 5: Regional Simulation of Future Rounds
# ---------------------------
def find_finished_winner(matchup, finished_games):
    """
    Given a matchup (a tuple or list of team names) and a list of finished game dictionaries,
    returns the winner from the finished game that matches the matchup. The matchup is considered
    a match if the set of teams in the finished game equals the set of teams in the matchup.
    """
    matchup_set = set(matchup)
    for game in finished_games:
        game_set = {game.get("team1", "").strip(), game.get("team2", "").strip()}
        if matchup_set == game_set:
            winner = game.get("winner", "").strip()
            if winner:
                return winner
    return None

def simulate_round_best(matchups, active_set, round_name, finished_games=None, username=None, player_pick_set=None):
    """
    Simulate one round under best-case assumptions.
    
    For each matchup:
      - If a finished game result exists in finished_games, use it.
      - Otherwise, if there is a team in the matchup that is in the overall active set and also in the player's picks,
        choose one arbitrarily from that triple intersection and award bonus points.
      - If no such team exists, then check if there is any team in the matchup that is in the overall active set.
        If so, choose one (no bonus, since it’s not in the player's picks).
      - Otherwise, choose arbitrarily from the matchup.
    
    Bonus points (based on ROUND_WEIGHTS) are awarded only if the winning team is:
      (a) in the matchup,
      (b) in the overall active set, and
      (c) in the player's picks.
    
    Returns:
      A tuple (propagated_winner, round_bonus, winners).
    """
    round_bonus = 0
    winners = []
    for matchup in matchups:
        # Use finished game result if available.
        if finished_games:
            winner = find_finished_winner(matchup, finished_games)
            if winner:
                winners.append(winner)
                continue

        # Determine candidate teams that are in the matchup, in the overall active set, and in the player's picks.
        candidate = set(matchup).intersection(active_set).intersection(player_pick_set) if player_pick_set else set()
        if candidate:
            chosen = list(candidate)[0]
            round_bonus += int(ROUND_WEIGHTS.get(round_name, 1))
            if username and "Jody" in username:
                logger.info(f"[Jody Trace] (Best) Player's team {chosen} (in active set and matchup {matchup}) wins, awarding bonus {ROUND_WEIGHTS.get(round_name, 1)}")
        else:
            # Fallback: choose from teams in the matchup that are in the overall active set.
            candidate_active = set(matchup).intersection(active_set)
            if candidate_active:
                chosen = list(candidate_active)[0]
                if username and "Jody" in username:
                    logger.info(f"[Jody Trace] (Best) Active team {chosen} wins matchup {matchup} but is not in player's picks; no bonus awarded")
            else:
                chosen = list(matchup)[0]
                if username and "Jody" in username:
                    logger.info(f"[Jody Trace] (Best) No active team in matchup {matchup}; arbitrarily choosing: {chosen}")
        winners.append(chosen)
    propagated = winners[0] if winners else None
    return propagated, round_bonus, winners
def simulate_round_worst(matchups, active_set, round_name, finished_games=None, username=None, player_pick_set=None):
    """
    Simulate one round under worst-case assumptions.

    For each matchup:
      - If a finished game result exists in finished_games, use it.
      - Otherwise, determine the set of active teams in the matchup ('inter')
        and the subset of those that the player picked ('player_inter').
      - If there exists any active team that the player did NOT pick,
        choose one from that set (forcing a loss, no bonus).
      - Otherwise, if all active teams in the matchup are the player's teams,
        choose one (and award bonus points, since the player's team must progress).
      - If no active team is present in the matchup, choose arbitrarily.
    
    In worst-case simulation bonus points are only added when the matchup is
    entirely composed of the player's teams.
    
    Returns:
      A tuple (propagated_winner, round_bonus, winners)
    """
    round_bonus = 0  # Default bonus is 0 unless all active teams are player's.
    winners = []
    for matchup in matchups:
        # Use finished game result if available.
        if finished_games:
            winner = find_finished_winner(matchup, finished_games)
            if winner:
                winners.append(winner)
                continue

        # Determine active teams from the matchup.
        inter = set(matchup).intersection(active_set)
        # Determine which of these active teams are in the player's picks.
        player_inter = set(matchup).intersection(player_pick_set) if player_pick_set else set()

        # If any active team is NOT a player's team, choose that to force a loss.
        non_player_candidates = inter - player_inter
        if non_player_candidates:
            chosen = list(non_player_candidates)[0]
            if username and "Jody" in username:
                logger.info(f"[Jody Trace] (Worst) In matchup {matchup}, choosing non-player team: {chosen}")
        elif inter:
            # All active teams in the matchup are player's teams.
            chosen = list(inter)[0]
            round_bonus += int(ROUND_WEIGHTS.get(round_name, 1))
            if username and "Jody" in username:
                logger.info(f"[Jody Trace] (Worst) In matchup {matchup}, only player's teams available; choosing {chosen} and awarding bonus {ROUND_WEIGHTS.get(round_name, 1)}")
        else:
            # No active team present; choose arbitrarily.
            chosen = list(matchup)[0]
            if username and "Jody" in username:
                logger.info(f"[Jody Trace] (Worst) No active team in matchup {matchup}; arbitrarily choosing: {chosen}")
        winners.append(chosen)
    propagated = winners[0] if winners else None
    return propagated, round_bonus, winners


def simulate_bracket_worst_case_region(region, bracket, player_pick_set, visible_by_region, current_round, username=None):
    """
    Simulate the remaining rounds in a region under worst-case assumptions.

    The simulation uses the overall active set from finished rounds and, for each round,
    attempts to force a loss for the player by selecting an active team not picked by the player.
    However, if in any matchup all active teams belong to the player (i.e. the player has both teams),
    then one of those is chosen and bonus points are awarded.
    
    Returns:
      (total_bonus, final_survivor)
    """
    # Get the active set for the region.
    active_set = get_active_set(region, visible_by_region)
    if username and "Jody" in username:
        logger.info(f"[Jody Trace] (Worst Bracket - {region}) Starting active_set: {active_set}")
    total_bonus = 0  # Worst-case bonus starts at 0.
    final_survivor = None
    start_index = ROUND_ORDER.index(current_round)
    for r in ROUND_ORDER[start_index:]:
        if r not in bracket:
            continue
        matchups = bracket[r]
        finished_games = visible_by_region.get(region, {}).get(r, [])
        propagated, bonus, winners = simulate_round_worst(matchups, active_set, r, finished_games, username, player_pick_set=player_pick_set)
        total_bonus += bonus  # bonus is only added when the player's teams cover the matchup.
        final_survivor = propagated
        if username and "Jody" in username:
            logger.info(f"[Jody Trace] (Worst Bracket - {region}) After round {r}, winners: {winners}")
        # Update active_set to be the winners for the next round.
        active_set = set(winners)
        if username and "Jody" in username:
            logger.info(f"[Jody Trace] (Worst Bracket - {region}) After round {r}, active_set: {active_set}, cumulative bonus: {total_bonus}")
    return total_bonus, final_survivor



def simulate_bracket_best_case_region(region, bracket, player_pick_set, visible_by_region, current_round, username=None, simulate_overall=False):
    """
    Simulate the remaining rounds in a region under best-case assumptions.
    
    If simulate_overall is True, the simulation uses the overall active set (ignoring player's picks)
    to determine the regional winner.
    If simulate_overall is False, the simulation filters the active set by player's picks.
    
    Bonus points are only awarded in a matchup if the winning team is in player_pick_set.
    
    Returns:
      (total_bonus, final_survivor)
    """
    active_set = get_active_set(region, visible_by_region)
    if not simulate_overall:
        active_set = active_set.intersection(player_pick_set)
    
    if not active_set:
        if username and "Jody" in username:
            logger.info(f"[Jody Trace] (Best Bracket - {region}) No active teams available. Skipping simulation.")
        return 0, None
    
    if username and "Jody" in username:
        logger.info(f"[Jody Trace] (Best Bracket - {region}) Starting active_set: {active_set}")
    total_bonus = 0
    final_survivor = None
    start_index = ROUND_ORDER.index(current_round)
    for r in ROUND_ORDER[start_index:]:
        if r not in bracket:
            continue
        matchups = bracket[r]
        finished_games = visible_by_region.get(region, {}).get(r, [])
        propagated, bonus, winners = simulate_round_best(matchups, active_set, r, finished_games, username, player_pick_set=player_pick_set)
        total_bonus += bonus
        final_survivor = propagated
        if username and "Jody" in username:
            logger.info(f"[Jody Trace] (Best Bracket - {region}) After round {r}, winners: {winners}")
        active_set = set(winners)
        if not simulate_overall:
            active_set = active_set.intersection(player_pick_set)
        if username and "Jody" in username:
            logger.info(f"[Jody Trace] (Best Bracket - {region}) After round {r}, active_set: {active_set}, cumulative bonus: {total_bonus}")
    return total_bonus, final_survivor



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
    if "Jody" in "".join(regional_champs.values()):
        logger.info(f"[Jody Trace] (Interregional) Final Four: {final_four}, Championship: {championship}")
    return {"Final Four": final_four, "Championship": championship}

# ---------------------------
# Step 7: Final Score Calculation for Future Rounds
# ---------------------------
def calculate_worst_case_scores():
    """
    Calculates worst-case final scores for all users by combining the base score (from finished games)
    with the worst-case bonus from simulating the remaining rounds (regional and interregional).
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
            for region in regions:
                region_name = region.get("region_name", "Unknown")
                bracket = build_regional_bracket(region)
                current_round = current_by_region.get(region_name, ROUND_ORDER[0])
                bonus, winner = simulate_bracket_worst_case_region(region_name, bracket, player_pick_set, visible_by_region, current_round, username=user.full_name)
                bonus_total += bonus
                if winner:
                    regional_winners[region_name] = winner
                if user.full_name and "Jody" in user.full_name:
                    logger.info(f"[Jody Trace] (Worst) Region {region_name}: bonus {bonus}, winner {winner}")
            # For simplicity, assume surviving regions are the raw winners.
            if len(regional_winners) == 4:
                inter = simulate_interregional_bracket(regional_winners)
                current_global_round, global_visible = get_round_game_status()
                ff_finished_games = global_visible.get("Final Four", [])
                ff_survivor, ff_bonus, _ = simulate_round_worst(inter["Final Four"], player_pick_set, "Final Four", finished_games=ff_finished_games, username=user.full_name)
                bonus_total += ff_bonus
                if user.full_name and "Jody" in user.full_name:
                    logger.info(f"[Jody Trace] (Worst) Interregional: bonus {ff_bonus}, FF survivor {ff_survivor}")
            worst_scores[user.full_name] = base_score + bonus_total
            if user.full_name and "Jody" in user.full_name:
                logger.info(f"[Jody Trace] (Worst) Final score for {user.full_name}: base {base_score} + bonus {bonus_total}")
        return worst_scores
    except Exception as e:
        logger.error(f"Error calculating worst-case scores: {e}")
        session.rollback()
        return {}
    finally:
        session.close()


def calculate_best_case_scores():
    """
    Calculates best-case final scores for all users by combining the base score (from finished games)
    with the best-case bonus from simulating the remaining rounds (regional and interregional).
    
    This version runs two simulations per region:
      1. Overall simulation (simulate_overall=True) to determine the overall regional winners for interregional rounds.
      2. Player-filtered simulation (simulate_overall=False) to calculate bonus points, awarding bonus only when the player's team wins a matchup.
    
    Interregional rounds (Final Four and Championship) are simulated using the overall regional winners.
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
            
            player_regional_bonus = 0
            overall_regional_winners = {}
            for region in regions:
                region_name = region.get("region_name", "Unknown")
                bracket = build_regional_bracket(region)
                current_round = current_by_region.get(region_name, ROUND_ORDER[0])
                
                # Overall simulation: ignore player's picks to determine regional winner.
                overall_bonus, overall_winner = simulate_bracket_best_case_region(
                    region_name, bracket, player_pick_set, visible_by_region, current_round, username=user.full_name, simulate_overall=True
                )
                overall_regional_winners[region_name] = overall_winner
                
                # Player-filtered simulation: compute bonus only if the player's team wins matchups.
                bonus, winner = simulate_bracket_best_case_region(
                    region_name, bracket, player_pick_set, visible_by_region, current_round, username=user.full_name, simulate_overall=False
                )
                player_regional_bonus += bonus
                if user.full_name and "Jody" in user.full_name:
                    logger.info(f"[Jody Trace] (Best) Region {region_name}: bonus {bonus}, winner {winner}")
            
            # Use overall regional winners to simulate interregional rounds.
            if len(overall_regional_winners) == 4:
                inter = simulate_interregional_bracket(overall_regional_winners)
                current_global_round, global_visible = get_round_game_status()
                ff_finished_games = global_visible.get("Final Four", [])
                ff_survivor, ff_bonus, _ = simulate_round_best(inter["Final Four"], player_pick_set, "Final Four", finished_games=ff_finished_games, username=user.full_name, player_pick_set=player_pick_set)
                champ_finished_games = global_visible.get("Championship", [])
                champ_survivor, champ_bonus, _ = simulate_round_best(inter["Championship"], player_pick_set, "Championship", finished_games=champ_finished_games, username=user.full_name, player_pick_set=player_pick_set)
                player_interregional_bonus = ff_bonus + champ_bonus
            else:
                player_interregional_bonus = 0
            
            best_scores[user.full_name] = base_score + player_regional_bonus + player_interregional_bonus
            if user.full_name and "Jody" in user.full_name:
                logger.info(f"[Jody Trace] (Best) Final score for {user.full_name}: base {base_score} + bonus {player_regional_bonus + player_interregional_bonus}")
        return best_scores
    except Exception as e:
        logger.error(f"Error calculating best-case scores: {e}")
        session.rollback()
        return {}
    finally:
        session.close()
