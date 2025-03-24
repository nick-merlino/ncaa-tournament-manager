"""
main.py

Main Flask application for the NCAA Tournament Bracket and Picks application.
The web interface displays tournament matchups and supports updating game results.
Visible rounds are determined recursively: a round is only visible if every game
in all regions is complete.
"""

import os
import sys
import json
from collections import defaultdict
from flask import Flask, render_template, request, jsonify, redirect, url_for

from config import logger, DATABASE_URL
from db import init_db, SessionLocal, TournamentResult, UserPick
from google_integration import fetch_picks_from_sheets, update_local_db_with_picks, GoogleSheetsError
from scoring import calculate_scoring, get_round_game_status
from report import generate_report
from constants import ROUND_ORDER, FIRST_ROUND_PAIRINGS

app = Flask(__name__)

TOURNAMENT_BRACKET_JSON = "tournament_bracket.json"


def import_bracket_from_json(json_file):
    """
    Imports the tournament bracket from a JSON file if no matchup data exists.
    The JSON is expected to have 4 regions each with 16 teams.
    """
    session = SessionLocal()
    try:
        if session.query(TournamentResult).count() > 0:
            logger.info("Matchup data exists. Skipping bracket import.")
            return True
        with open(json_file, 'r') as f:
            data = json.load(f)
        regions = data.get("regions", [])
        if len(regions) != 4:
            logger.error(f"[ERROR] Expected 4 regions, found {len(regions)}.")
            return False
        game_id_counter = 1
        for region_info in regions:
            region_name = region_info["region_name"]
            teams = region_info["teams"]
            if len(teams) != 16:
                logger.error(f"[ERROR] Region '{region_name}' must have 16 seeds, found {len(teams)}.")
                return False
            # Create a mapping of seed to team name.
            seed_to_team = {team['seed']: team['team_name'] for team in teams}
            for pair in FIRST_ROUND_PAIRINGS:
                team1 = seed_to_team.get(pair[0])
                team2 = seed_to_team.get(pair[1])
                if not team1 or not team2:
                    logger.error(f"[ERROR] Missing team for seeds {pair} in region {region_name}.")
                    return False
                round_name = f"Round of 64 - {region_name}"
                new_game = TournamentResult(
                    game_id=game_id_counter,
                    round_name=round_name,
                    team1=team1,
                    team2=team2,
                    winner=None
                )
                session.add(new_game)
                game_id_counter += 1
        session.commit()
        logger.info("Bracket imported from tournament_bracket.json successfully.")
        return True
    except Exception as e:
        logger.error(f"Error importing bracket: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def validate_picks_against_bracket():
    """
    Validates that all user picks reference teams in the official bracket.
    Returns True if all picks are valid, False otherwise.
    """
    from db import TournamentResult, UserPick
    session = SessionLocal()
    try:
        bracket_teams = set()
        for game in session.query(TournamentResult).all():
            bracket_teams.add(game.team1)
            bracket_teams.add(game.team2)
        invalid_picks = []
        for pick in session.query(UserPick).all():
            if pick.team_name not in bracket_teams:
                invalid_picks.append((pick.user_id, pick.team_name))
        if invalid_picks:
            logger.error("The following picks reference teams not in the official bracket:")
            for uid, team in invalid_picks:
                logger.error(f" - user_id={uid}, team='{team}'")
            return False
        logger.info("All picks match bracket teams.")
        return True
    finally:
        session.close()


def get_default_round():
    """
    Determine the default round to display for the web view using the recursive visible-rounds logic.
    """
    _, visible_rounds = get_round_game_status()
    # Default to the lowest visible round.
    if visible_rounds:
        return list(visible_rounds.keys())[0]
    return ROUND_ORDER[0]

def update_dependent_for_pairing(session, region, base_round, pairing_index):
    """
    Recursively update the dependent game for a given pairing in a region.
    
    For the given base_round (e.g. "Round of 64") and pairing (identified by pairing_index),
    determine the next round. If the pairing in base_round is complete, then update (or create)
    the dependent game in the next round with the winners; then recursively repeat for subsequent rounds.
    
    If the pairing is incomplete, clear (but do not delete) the dependent game if it exists,
    and recursively clear dependent games in future rounds.
    """
    from constants import ROUND_ORDER
    current_index = ROUND_ORDER.index(base_round)
    next_round = ROUND_ORDER[current_index + 1] if current_index + 1 < len(ROUND_ORDER) else None
    if not next_round:
        return

    current_round_name = f"{base_round} - {region}"
    next_round_name = f"{next_round} - {region}"
    # Retrieve the games for the current round (for this region)
    region_games = session.query(TournamentResult).filter(
        TournamentResult.round_name == current_round_name
    ).order_by(TournamentResult.game_id).all()

    # Identify the pairing games in current round.
    pairing_games = region_games[pairing_index*2 : pairing_index*2 + 2]
    next_region_games = session.query(TournamentResult).filter(
        TournamentResult.round_name == next_round_name
    ).order_by(TournamentResult.game_id).all()

    if len(pairing_games) < 2 or not all(g.winner and g.winner.strip() for g in pairing_games):
        # The pairing is now incomplete; if a dependent game exists in the next round, clear its result.
        if pairing_index < len(next_region_games):
            dep_game = next_region_games[pairing_index]
            dep_game.winner = None
            session.commit()
            # Recursively clear subsequent dependent game(s)
            update_dependent_for_pairing(session, region, next_round, pairing_index)
        return
    else:
        # The pairing is complete: determine the expected matchup.
        expected_pairing = (pairing_games[0].winner.strip(), pairing_games[1].winner.strip())
        if pairing_index < len(next_region_games):
            dep_game = next_region_games[pairing_index]
            # Update the teams if they differ from the expected pairing.
            if (dep_game.team1.strip() != expected_pairing[0] or
                dep_game.team2.strip() != expected_pairing[1]):
                dep_game.team1 = expected_pairing[0]
                dep_game.team2 = expected_pairing[1]
            # Clear its result so that the user must select the winner.
            dep_game.winner = None
        else:
            # No dependent game exists yet; create one.
            last_game = session.query(TournamentResult).order_by(TournamentResult.game_id.desc()).first()
            new_id = last_game.game_id + 1 if last_game else 1
            new_game = TournamentResult(
                game_id=new_id,
                round_name=next_round_name,
                team1=expected_pairing[0],
                team2=expected_pairing[1],
                winner=None
            )
            session.add(new_game)
        session.commit()
        # Recurse for the next round.
        update_dependent_for_pairing(session, region, next_round, pairing_index)


def update_dependent_games_for_round(session, base_round):
    """
    For a given base_round (region-based), update dependent games for each region.
    Reads regions from tournament_bracket.json and, for each region,
    creates or updates the dependent game (in the next round) for every pairing that is complete.
    """
    import json
    with open(TOURNAMENT_BRACKET_JSON, 'r') as f:
        data = json.load(f)
    regions = [r["region_name"] for r in data.get("regions", [])]
    current_index = ROUND_ORDER.index(base_round)
    next_round = ROUND_ORDER[current_index + 1] if current_index + 1 < len(ROUND_ORDER) else None
    if not next_round:
        return
    for region in regions:
        current_round_name = f"{base_round} - {region}"
        next_round_name = f"{next_round} - {region}"
        region_games = session.query(TournamentResult).filter(
            TournamentResult.round_name == current_round_name
        ).order_by(TournamentResult.game_id).all()
        for pairing_index in range(0, len(region_games) // 2):
            pairing_games = region_games[pairing_index*2 : pairing_index*2 + 2]
            if len(pairing_games) < 2 or not all(g.winner and g.winner.strip() for g in pairing_games):
                continue
            expected_pairing = (pairing_games[0].winner.strip(), pairing_games[1].winner.strip())
            next_region_games = session.query(TournamentResult).filter(
                TournamentResult.round_name == next_round_name
            ).order_by(TournamentResult.game_id).all()
            if pairing_index < len(next_region_games):
                dep_game = next_region_games[pairing_index]
                if (dep_game.team1.strip() != expected_pairing[0] or
                    dep_game.team2.strip() != expected_pairing[1]):
                    dep_game.team1 = expected_pairing[0]
                    dep_game.team2 = expected_pairing[1]
                dep_game.winner = None
            else:
                last_game = session.query(TournamentResult).order_by(TournamentResult.game_id.desc()).first()
                new_id = last_game.game_id + 1 if last_game else 1
                new_game = TournamentResult(
                    game_id=new_id,
                    round_name=next_round_name,
                    team1=expected_pairing[0],
                    team2=expected_pairing[1],
                    winner=None
                )
                session.add(new_game)
    session.commit()

@app.route('/')
def index():
    """
    Main route renders the tournament bracket for the selected round.
    For region-based rounds, games are grouped by region.
    For interregional rounds (Final Four and Championship), games are grouped by game label (e.g., "Game 1", "Game 2").
    """
    session = SessionLocal()
    try:
        current_round, visible_rounds = get_round_game_status()
        if not current_round:
            current_round = ROUND_ORDER[0]
        available_base_rounds = list(visible_rounds.keys())
        selected_round = request.args.get('round', current_round)
        if selected_round not in available_base_rounds:
            selected_round = current_round

        if selected_round not in ["Final Four", "Championship"]:
            # Region-based rounds.
            results = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{selected_round} -%")
            ).all()
            region_data = defaultdict(list)
            with open(TOURNAMENT_BRACKET_JSON, 'r') as f:
                bracket_data = json.load(f)
            team_seeds = {team['team_name']: team['seed'] for region in bracket_data['regions'] for team in region['teams']}
            for game in results:
                region = game.round_name.split('-', 1)[1].strip() if '-' in game.round_name else "No Region"
                region_data[region].append(game)
            if selected_round == "Round of 64":
                try:
                    for region, games in region_data.items():
                        region_data[region] = sorted(
                            games,
                            key=lambda g: FIRST_ROUND_PAIRINGS.index((
                                min(team_seeds.get(g.team1.strip(), 999), team_seeds.get(g.team2.strip(), 999)),
                                max(team_seeds.get(g.team1.strip(), 999), team_seeds.get(g.team2.strip(), 999))
                            ))
                        )
                except ValueError as ve:
                    logger.error(f"Mismatch in pairing order: {ve}")
                    sys.exit(1)
            else:
                for region in region_data:
                    region_data[region].sort(key=lambda g: g.game_id)
            display_data = dict(region_data)
        else:
            # For Final Four and Championship, group by game label.
            results = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{selected_round}%")
            ).all()
            game_data = defaultdict(list)
            for game in results:
                if '-' in game.round_name:
                    label = game.round_name.split('-', 1)[1].strip()
                else:
                    label = selected_round
                game_data[label].append(game)
            for label in game_data:
                game_data[label].sort(key=lambda g: g.game_id)
            display_data = dict(game_data)

        return render_template("index.html", region_data=display_data,
                               selected_round=selected_round,
                               available_base_rounds=available_base_rounds)
    finally:
        session.close()


@app.route('/update_game', methods=['POST'])
def update_game():
    """
    Update a game result.
    
    When a game result is updated or cleared, only the dependent next-round game for that pairing
    is recalculated/cleared. This effect ripples recursively. However, if the change causes the
    global completeness of the round to change from complete to incomplete, later rounds remain intact
    (and will be hidden by the UI) until the round becomes complete again.
    """
    import json
    data = request.get_json()
    game_id = data.get('game_id')
    new_winner = data.get('winner', '').strip() or None
    session = SessionLocal()
    try:
        game = session.query(TournamentResult).filter_by(game_id=game_id).first()
        if not game:
            logger.info(f"Game {game_id} not found.")
            return jsonify({"status": "failure", "error": "Game not found"}), 404
        if new_winner is not None and new_winner not in [game.team1.strip(), game.team2.strip()]:
            logger.info(f"Invalid winner '{new_winner}' for game {game_id}: {game.team1} vs {game.team2}")
            return jsonify({"status": "failure", "error": "Invalid winner"}), 400

        # Extract base round and region (or game label).
        if '-' in game.round_name:
            base_round = game.round_name.split('-', 1)[0].strip()  # e.g., "Round of 64"
            region = game.round_name.split('-', 1)[1].strip()       # e.g., "South"
        else:
            base_round = game.round_name.strip()
            region = None

        # Check the global completeness of the current round before update.
        current_round_pattern = f"{base_round} -%"
        current_games_before = session.query(TournamentResult).filter(
            TournamentResult.round_name.like(current_round_pattern)
        ).all()
        old_global_complete = all(g.winner and g.winner.strip() for g in current_games_before)

        # (1) Update the game.
        game.winner = new_winner
        session.commit()
        logger.info(f"Updated game {game_id}: set winner to '{new_winner}'")

        # (2) Process the dependent next-round game only for the specific pairing.
        if base_round in ["Round of 64", "Round of 32", "Sweet 16", "Elite 8"]:
            # Retrieve games for the current region and determine pairing index.
            region_games = session.query(TournamentResult).filter(
                TournamentResult.round_name == f"{base_round} - {region}"
            ).order_by(TournamentResult.game_id).all()
            game_ids = [g.game_id for g in region_games]
            try:
                game_index = game_ids.index(game.game_id)
            except ValueError:
                logger.info(f"Game {game_id} not found in region games.")
                return jsonify({"status": "failure", "error": "Game not in expected region"}), 500
            pairing_index = game_index // 2

            # Check new global completeness after the update.
            current_games_after = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(current_round_pattern)
            ).all()
            new_global_complete = all(g.winner and g.winner.strip() for g in current_games_after)

            # If the current round remains globally complete, update dependent game for this pairing.
            if new_global_complete:
                update_dependent_for_pairing(session, region, base_round, pairing_index)
            else:
                # If the global state changes from complete to incomplete, do not update later rounds.
                logger.info("Global state changed: round is now incomplete; dependent rounds will be hidden.")
        elif base_round == "Final Four":
            # (Final Four interregional logic remains unchanged.)
            next_round = "Championship"
            final_four_games = session.query(TournamentResult).filter(
                TournamentResult.round_name.like("Final Four -%")
            ).order_by(TournamentResult.game_id).all()
            all_final_four_complete = (len(final_four_games) == 2 and
                                       all(g.winner and g.winner.strip() for g in final_four_games))
            championship_game = session.query(TournamentResult).filter_by(round_name="Championship").first()
            if not all_final_four_complete:
                if championship_game:
                    logger.info("Clearing Championship game since Final Four is incomplete.")
                    championship_game.winner = None
            else:
                expected_pairing = (final_four_games[0].winner.strip(), final_four_games[1].winner.strip())
                if championship_game:
                    if (championship_game.team1.strip() != expected_pairing[0] or
                        championship_game.team2.strip() != expected_pairing[1]):
                        logger.info(f"Updating Championship game {championship_game.game_id} teams to {expected_pairing} and clearing winner.")
                        championship_game.team1 = expected_pairing[0]
                        championship_game.team2 = expected_pairing[1]
                    championship_game.winner = None
                else:
                    last_game = session.query(TournamentResult).order_by(TournamentResult.game_id.desc()).first()
                    new_id = last_game.game_id + 1 if last_game else 1
                    new_game = TournamentResult(
                        game_id=new_id,
                        round_name="Championship",
                        team1=expected_pairing[0],
                        team2=expected_pairing[1],
                        winner=None
                    )
                    session.add(new_game)
            session.commit()

        # (3) Determine refresh flag: only if the global completeness state of the current round changed.
        refresh = False
        if old_global_complete != new_global_complete:
            refresh = True
            if new_global_complete:
                logger.info("Global state changed: current round went from incomplete to complete.")
            else:
                logger.info("Global state changed: current round went from complete to incomplete.")
        else:
            logger.info("Global state of current round did not change.")

        # Optionally, recalc the current round if your UI depends on it.
        new_current_round, _ = get_round_game_status()
        return jsonify({"status": "success", "refresh": refresh, "current_round": new_current_round})
    except Exception as e:
        session.rollback()
        logger.error(f"Error in update_game: {e}")
        return jsonify({"status": "failure", "error": str(e)}), 500
    finally:
        session.close()

if __name__ == '__main__':
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        logger.info("Database does not exist. Initializing database and importing data...")
        init_db()
        if not import_bracket_from_json(TOURNAMENT_BRACKET_JSON):
            sys.exit(1)
        try:
            picks_data = fetch_picks_from_sheets()
            update_local_db_with_picks(picks_data)
            logger.info("Imported picks from Google Sheets.")
        except Exception as e:
            logger.error(f"Failed to import picks from Google Sheets: {e}")
    else:
        logger.info("Database exists. Retaining existing matchup data.")
        if not import_bracket_from_json(TOURNAMENT_BRACKET_JSON):
            logger.error("Bracket import failed.")
    
    if not validate_picks_against_bracket():
        sys.exit(1)
    
    calculate_scoring()
    app.run(debug=False)
