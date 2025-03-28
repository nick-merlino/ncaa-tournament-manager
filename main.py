"""
main.py

Main Flask application for the NCAA Tournament Bracket and Picks application.
This updated version optimizes code reuse by factoring common functionality into helper functions,
localizes imports where appropriate, and adds extensive inline documentation.
It handles displaying tournament matchups, updating game results (with recursive dependent updates),
and generating PDF reports.
"""

import os
import sys
import json
from collections import defaultdict
from flask import Flask, render_template, request, jsonify, redirect, url_for

# Import core configuration and logging
from config import logger, DATABASE_URL
# Import database session and models
from db import init_db, SessionLocal, TournamentResult, UserPick
# Import modules for Google Sheets integration, scoring, and report generation
from google_integration import fetch_picks_from_sheets, update_local_db_with_picks, GoogleSheetsError
from scoring import calculate_scoring, get_round_game_status
from report import generate_report
from constants import ROUND_ORDER, FIRST_ROUND_PAIRINGS

# Initialize the Flask application
app = Flask(__name__)

# File path for the tournament bracket JSON file
TOURNAMENT_BRACKET_JSON = "tournament_bracket.json"


def import_bracket_from_json(json_file):
    """
    Imports the tournament bracket from a JSON file if no matchup data exists.
    Expects exactly 4 regions with 16 teams each.
    """
    session = SessionLocal()
    try:
        # If matchup data already exists, skip the import.
        if session.query(TournamentResult).count() > 0:
            logger.info("Matchup data already exists. Skipping bracket import.")
            return True

        with open(json_file, 'r') as f:
            data = json.load(f)
        regions = data.get("regions", [])
        if len(regions) != 4:
            logger.error(f"[ERROR] Expected 4 regions, found {len(regions)}.")
            return False

        game_id_counter = 1
        for region in regions:
            region_name = region.get("region_name", "Unknown Region")
            teams = region.get("teams", [])
            if len(teams) != 16:
                logger.error(f"[ERROR] Region '{region_name}' must have 16 seeds, found {len(teams)}.")
                return False
            # Create mapping from seed to team name
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
        logger.info("Bracket imported successfully from JSON.")
        return True
    except Exception as e:
        logger.error(f"Error importing bracket: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def validate_picks_against_bracket():
    """
    Validates that every user pick references a team that exists in the official bracket.
    Returns True if all picks are valid, otherwise False.
    """
    session = SessionLocal()
    try:
        bracket_teams = set(game.team1 for game in session.query(TournamentResult).all())
        bracket_teams.update(game.team2 for game in session.query(TournamentResult).all())
        invalid_picks = []
        for pick in session.query(UserPick).all():
            if pick.team_name not in bracket_teams:
                invalid_picks.append((pick.user_id, pick.team_name))
        if invalid_picks:
            logger.error("Invalid picks found referencing teams not in the official bracket:")
            for uid, team in invalid_picks:
                logger.error(f" - user_id={uid}, team='{team}'")
            return False
        logger.info("All user picks match teams in the official bracket.")
        return True
    finally:
        session.close()


def get_default_round():
    """
    Determines the default round to display based on visible rounds.
    Returns the lowest visible round if available; otherwise, defaults to the first round.
    """
    _, visible_rounds = get_round_game_status()
    return list(visible_rounds.keys())[0] if visible_rounds else ROUND_ORDER[0]


def update_dependent_for_pairing(session, region, base_round, pairing_index):
    """
    Recursively updates the dependent game for a given pairing within a region.
    
    For the provided base_round (e.g., "Round of 64") and pairing_index,
    it determines the corresponding game in the next round and updates it based on the winners.
    If the current pairing is incomplete, any dependent game is cleared.
    """
    current_index = ROUND_ORDER.index(base_round)
    if current_index + 1 >= len(ROUND_ORDER):
        return  # No subsequent round exists

    next_round = ROUND_ORDER[current_index + 1]
    current_round_name = f"{base_round} - {region}"
    next_round_name = f"{next_round} - {region}"

    region_games = session.query(TournamentResult).filter(
        TournamentResult.round_name == current_round_name
    ).order_by(TournamentResult.game_id).all()
    pairing_games = region_games[pairing_index * 2: pairing_index * 2 + 2]
    next_region_games = session.query(TournamentResult).filter(
        TournamentResult.round_name == next_round_name
    ).order_by(TournamentResult.game_id).all()

    if len(pairing_games) < 2 or not all(g.winner and g.winner.strip() for g in pairing_games):
        # If pairing is incomplete, clear dependent game if it exists and recursively clear further rounds.
        if pairing_index < len(next_region_games):
            dep_game = next_region_games[pairing_index]
            dep_game.winner = None
            session.commit()
            update_dependent_for_pairing(session, region, next_round, pairing_index)
        return
    else:
        expected_pairing = (pairing_games[0].winner.strip(), pairing_games[1].winner.strip())
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
        update_dependent_for_pairing(session, region, next_round, pairing_index)


def update_final_four(session):
    """
    Updates the Final Four games based on the winners of the Elite 8 round.
    
    Reads region names from the tournament bracket JSON, collects the Elite 8 winners,
    and creates or updates the Final Four games accordingly.
    """
    try:
        with open(TOURNAMENT_BRACKET_JSON, "r") as f:
            data = json.load(f)
        regions = [r.get("region_name", "Unknown") for r in data.get("regions", [])]

        elite8_winners = []
        for region in regions:
            game = session.query(TournamentResult).filter(
                TournamentResult.round_name == f"Elite 8 - {region}"
            ).order_by(TournamentResult.game_id).first()
            elite8_winners.append(game.winner.strip() if game and game.winner and game.winner.strip() else None)

        if not all(elite8_winners):
            for game in session.query(TournamentResult).filter(
                TournamentResult.round_name.like("Final Four -%")
            ).all():
                game.winner = None
            session.commit()
            return

        # Pair first two winners as Game 1 and the last two as Game 2
        game1_pair = (elite8_winners[0], elite8_winners[1])
        game2_pair = (elite8_winners[2], elite8_winners[3])

        ff_game1 = session.query(TournamentResult).filter_by(round_name="Final Four - Game 1").first()
        if ff_game1:
            if (ff_game1.team1.strip() != game1_pair[0] or
                ff_game1.team2.strip() != game1_pair[1]):
                ff_game1.team1 = game1_pair[0]
                ff_game1.team2 = game1_pair[1]
            ff_game1.winner = None
        else:
            last_game = session.query(TournamentResult).order_by(TournamentResult.game_id.desc()).first()
            new_id = last_game.game_id + 1 if last_game else 1
            ff_game1 = TournamentResult(
                game_id=new_id,
                round_name="Final Four - Game 1",
                team1=game1_pair[0],
                team2=game1_pair[1],
                winner=None
            )
            session.add(ff_game1)

        ff_game2 = session.query(TournamentResult).filter_by(round_name="Final Four - Game 2").first()
        if ff_game2:
            if (ff_game2.team1.strip() != game2_pair[0] or
                ff_game2.team2.strip() != game2_pair[1]):
                ff_game2.team1 = game2_pair[0]
                ff_game2.team2 = game2_pair[1]
            ff_game2.winner = None
        else:
            last_game = session.query(TournamentResult).order_by(TournamentResult.game_id.desc()).first()
            new_id = last_game.game_id + 1 if last_game else 1
            ff_game2 = TournamentResult(
                game_id=new_id,
                round_name="Final Four - Game 2",
                team1=game2_pair[0],
                team2=game2_pair[1],
                winner=None
            )
            session.add(ff_game2)
        session.commit()
    except Exception as e:
        logger.error(f"Error updating Final Four: {e}")


def update_championship(session):
    """
    Updates the Championship game based on the winners from the Final Four.
    If both Final Four games are complete, it updates (or creates) the Championship matchup;
    otherwise, it clears the Championship result.
    """
    ff_games = session.query(TournamentResult).filter(
        TournamentResult.round_name.like("Final Four -%")
    ).order_by(TournamentResult.game_id).all()
    if not (len(ff_games) == 2 and all(g.winner and g.winner.strip() for g in ff_games)):
        champ = session.query(TournamentResult).filter_by(round_name="Championship").first()
        if champ:
            champ.winner = None
            session.commit()
        return

    ff_winners = [g.winner.strip() for g in ff_games]
    champ = session.query(TournamentResult).filter_by(round_name="Championship").first()
    if champ:
        if champ.team1.strip() != ff_winners[0] or champ.team2.strip() != ff_winners[1]:
            champ.team1 = ff_winners[0]
            champ.team2 = ff_winners[1]
        champ.winner = None
    else:
        last_game = session.query(TournamentResult).order_by(TournamentResult.game_id.desc()).first()
        new_id = last_game.game_id + 1 if last_game else 1
        champ = TournamentResult(
            game_id=new_id,
            round_name="Championship",
            team1=ff_winners[0],
            team2=ff_winners[1],
            winner=None
        )
        session.add(champ)
    session.commit()


@app.route('/generate_pdf')
def generate_pdf_route():
    """
    Triggers PDF report generation.
    Recalculates user scores, generates the PDF report,
    and redirects the user to the generated file.
    """
    from datetime import datetime
    calculate_scoring()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_filename = f"Merlino_NCAA_March_Madness_{timestamp}.pdf"
    pdf_path = os.path.join(app.static_folder, pdf_filename)
    generate_report(pdf_path, pdf_filename)
    return redirect(url_for('static', filename=pdf_filename))


@app.route('/')
def index():
    """
    Renders the main tournament bracket view.
    For region-based rounds, games are grouped by region.
    For interregional rounds (Final Four and Championship), games are grouped by game label.
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
            results = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{selected_round} -%")
            ).all()
            region_data = defaultdict(list)
            with open(TOURNAMENT_BRACKET_JSON, 'r') as f:
                bracket_data = json.load(f)
            team_seeds = {team['team_name']: team['seed']
                          for region in bracket_data.get("regions", [])
                          for team in region.get("teams", [])}
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
                    logger.error(f"Error in pairing order: {ve}")
                    sys.exit(1)
            else:
                for region in region_data:
                    region_data[region].sort(key=lambda g: g.game_id)
            display_data = dict(region_data)
        else:
            results = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{selected_round}%")
            ).all()
            game_data = defaultdict(list)
            for game in results:
                label = game.round_name.split('-', 1)[1].strip() if '-' in game.round_name else selected_round
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
    API endpoint to update a game result.
    Expects a JSON payload with 'game_id' and 'winner'.
    After updating, it recursively adjusts dependent games based on round logic.
    Returns a JSON response indicating success and whether the UI should refresh.
    """
    data = request.get_json()
    game_id = data.get('game_id')
    new_winner = data.get('winner', '').strip() or None
    session = SessionLocal()
    try:
        game = session.query(TournamentResult).filter_by(game_id=game_id).first()
        if not game:
            logger.info(f"Game {game_id} not found.")
            return jsonify({"status": "failure", "error": "Game not found"}), 404
        if new_winner and new_winner not in [game.team1.strip(), game.team2.strip()]:
            logger.info(f"Invalid winner '{new_winner}' for game {game_id}: {game.team1} vs {game.team2}")
            return jsonify({"status": "failure", "error": "Invalid winner"}), 400

        # Extract base round and any additional details from the round name
        if '-' in game.round_name:
            base_round = game.round_name.split('-', 1)[0].strip()
            detail = game.round_name.split('-', 1)[1].strip()
        else:
            base_round = game.round_name.strip()
            detail = None

        # Check global completeness of the current round before update
        current_round_pattern = f"{base_round} -%"
        current_games_before = session.query(TournamentResult).filter(
            TournamentResult.round_name.like(current_round_pattern)
        ).all()
        old_global_complete = all(g.winner and g.winner.strip() for g in current_games_before)

        # Update the game result
        game.winner = new_winner
        session.commit()
        logger.info(f"Updated game {game_id}: winner set to '{new_winner}'")

        # Process dependent game updates based on round type
        if base_round in ["Round of 64", "Round of 32", "Sweet 16"]:
            region = detail
            region_games = session.query(TournamentResult).filter(
                TournamentResult.round_name == f"{base_round} - {region}"
            ).order_by(TournamentResult.game_id).all()
            game_ids = [g.game_id for g in region_games]
            try:
                game_index = game_ids.index(game.game_id)
            except ValueError:
                logger.info(f"Game {game_id} not found in expected region games.")
                return jsonify({"status": "failure", "error": "Game not in expected region"}), 500
            pairing_index = game_index // 2
            update_dependent_for_pairing(session, region, base_round, pairing_index)

        elif base_round == "Elite 8":
            elite8_games = session.query(TournamentResult).filter(
                TournamentResult.round_name.like("Elite 8 -%")
            ).all()
            if all(g.winner and g.winner.strip() for g in elite8_games):
                update_final_four(session)
            else:
                logger.info("Elite 8 incomplete; Final Four will be cleared.")

        elif base_round == "Final Four":
            ff_games = session.query(TournamentResult).filter(
                TournamentResult.round_name.like("Final Four -%")
            ).all()
            if all(g.winner and g.winner.strip() for g in ff_games):
                update_championship(session)
            else:
                championship_game = session.query(TournamentResult).filter_by(round_name="Championship").first()
                if championship_game:
                    championship_game.winner = None
                    session.commit()

        # Re-check global completeness after update to determine if UI refresh is needed
        current_games_after = session.query(TournamentResult).filter(
            TournamentResult.round_name.like(current_round_pattern)
        ).all()
        new_global_complete = all(g.winner and g.winner.strip() for g in current_games_after)
        refresh = (old_global_complete != new_global_complete)
        return jsonify({"status": "success", "refresh": refresh})
    except Exception as e:
        logger.error(f"Error updating game: {e}")
        session.rollback()
        return jsonify({"status": "failure", "error": str(e)}), 500
    finally:
        session.close()


if __name__ == '__main__':
    # Initialize the database and tables if not already created
    init_db()
    # Optionally import bracket data from the JSON file
    import_bracket_from_json(TOURNAMENT_BRACKET_JSON)
    try:
        picks_data = fetch_picks_from_sheets()
        update_local_db_with_picks(picks_data)
    except GoogleSheetsError as e:
        logger.error(f"Google Sheets integration error: {e}")
    # Start the Flask development server
    app.run(debug=False)
