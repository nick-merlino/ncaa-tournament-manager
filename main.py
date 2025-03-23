"""
main.py

Main Flask application for the NCAA Tournament Bracket and Picks application.

Key Features:
  - Web interface for displaying tournament matchups with correctly ordered games.
  - Persistence of matchup selections and automatic partial clearing of affected subsequent round games.
  - Integration with Google Sheets for importing user picks.
  - PDF report generation based on tournament progress and user scores.
  
Note: Round weights, round order, and first-round pairing info are imported from constants.py.
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
from constants import ROUND_ORDER, FIRST_ROUND_PAIRINGS  # Shared constants for round order and pairing info

app = Flask(__name__)

# File containing the tournament bracket data.
TOURNAMENT_BRACKET_JSON = "tournament_bracket.json"

def get_available_base_rounds():
    """
    Retrieves the list of distinct base rounds from the TournamentResult table,
    ordered according to the predefined ROUND_ORDER.
    """
    session = SessionLocal()
    try:
        rounds = session.query(TournamentResult.round_name).distinct().all()
        base_rounds = set()
        for r in rounds:
            # Extract only the base round name (before any '-')
            base_round = r[0].split('-')[0].strip()
            base_rounds.add(base_round)
        # Return rounds ordered by the global ROUND_ORDER constant.
        return sorted(list(base_rounds), key=lambda x: ROUND_ORDER.index(x))
    finally:
        session.close()

def get_default_round():
    """
    Determines the default round to display based on the current tournament progress.
    The default round is the first round that still has any game without a winner.
    If all rounds are complete, it returns the last round with games.
    """
    session = SessionLocal()
    try:
        for base_round in ROUND_ORDER:
            games = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{base_round}%")
            ).all()
            if games and any(g.winner is None for g in games):
                return base_round
        # If every round is complete, return the last round that has any games.
        for base_round in reversed(ROUND_ORDER):
            games = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{base_round}%")
            ).all()
            if games:
                return base_round
        return None
    finally:
        session.close()

def create_next_round_games(session, current_results):
    """
    Generates the games for the next round based on the winners of the current round.
    
    This function groups the current round games by region (extracted from the round_name),
    then, for each region where an even number of winners exists, creates new TournamentResult
    entries for the next round by pairing adjacent winners in the order of their game_id.
    
    NOTE: With the new behavior, next-round generation is no longer auto-triggered
    on round completion. Instead, affected games are updated only when a previous round result changes.
    
    Args:
        session: Active SQLAlchemy session.
        current_results: List of TournamentResult objects from the current round.
    """
    # (This function is retained for manual or admin use if full regeneration is needed.)
    pass

def import_bracket_from_json(json_file):
    """
    Imports the tournament bracket from a JSON file if no matchup data exists in the database.
    
    Expects the JSON to have 4 regions, each with 16 teams.
    
    Args:
        json_file (str): Path to the tournament bracket JSON file.
    
    Returns:
        bool: True if import was successful or skipped; False otherwise.
    """
    from db import TournamentResult
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
            # Use the predefined pairing order from FIRST_ROUND_PAIRINGS
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
    Validates that all user picks reference teams that exist in the tournament bracket.
    
    Returns:
        bool: True if all picks are valid, False if any invalid picks are found.
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

@app.route('/')
def index():
    """
    Main route that renders the tournament bracket for the selected round.
    
    If the selected round is "Round of 64", games are ordered using FIRST_ROUND_PAIRINGS.
    For subsequent rounds, games are ordered by game_id.
    """
    session = SessionLocal()
    try:
        available_base_rounds = get_available_base_rounds()
        default_round = get_default_round()
        selected_round = request.args.get('round', default_round)
        if not available_base_rounds:
            return render_template("index.html", region_data={}, selected_round="None", available_base_rounds=[])
        if selected_round not in available_base_rounds:
            selected_round = default_round or ROUND_ORDER[0]
        
        # Fetch all games for the selected round.
        results = session.query(TournamentResult).filter(
            TournamentResult.round_name.like(f"{selected_round}%")
        ).all()
        region_data = defaultdict(list)
        # Load team seeds from the bracket file for ordering purposes.
        with open(TOURNAMENT_BRACKET_JSON, 'r') as f:
            bracket_data = json.load(f)
        team_seeds = {team['team_name']: team['seed'] for region in bracket_data['regions'] for team in region['teams']}
        
        for game in results:
            region = game.round_name.split('-', 1)[1].strip() if '-' in game.round_name else "No Region"
            region_data[region].append(game)
        
        # Order games: for Round of 64 use the explicit pairing order; otherwise, sort by game_id.
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
        
        return render_template("index.html", region_data=dict(region_data),
                               selected_round=selected_round, available_base_rounds=available_base_rounds)
    finally:
        session.close()

@app.route('/update_game', methods=['POST'])
def update_game():
    """
    Update the winner for a game. If the winner is set to a valid team,
    the game is updated. If the winner is cleared (set to null), then any
    future round games (which depend on this result) are deleted.
    
    When all games in the current round (across all regions) are complete,
    next-round games are created (or updated) and the refresh flag is set
    so that the client reloads the page. Otherwise, if a result is cleared,
    future rounds are invalidated.
    """
    import json
    data = request.get_json()
    game_id = data.get('game_id')
    # Normalize winner: trim whitespace; if empty string, set to None.
    winner = data.get('winner', '').strip() or None
    session = SessionLocal()
    try:
        game = session.query(TournamentResult).filter_by(game_id=game_id).first()
        if not game:
            logger.info(f"Game {game_id} not found.")
            return jsonify({"status": "failure", "error": "Game not found"}), 404

        # Allow clearing the winner (i.e. winner==None)
        if winner is not None and winner not in [game.team1.strip(), game.team2.strip()]:
            logger.info(f"Invalid winner '{winner}' for game {game_id}: {game.team1} vs {game.team2}")
            return jsonify({"status": "failure", "error": "Invalid winner"}), 400

        # Update the game with the new winner (could be None)
        game.winner = winner
        session.commit()
        logger.info(f"Updated game {game_id}: set winner to '{winner}'")
        
        # Determine the base round (e.g. "Round of 64")
        current_round = game.round_name.split('-', 1)[0].strip()
        current_index = ROUND_ORDER.index(current_round)
        refresh = False  # Default: do not force a client reload
        
        # If the winner was cleared (set to None), invalidate future rounds.
        if winner is None:
            logger.info(f"Winner cleared for game {game_id}. Invalidating all rounds after '{current_round}'.")
            for r in ROUND_ORDER[current_index+1:]:
                deleted = session.query(TournamentResult).filter(
                    TournamentResult.round_name.like(f"{r} -%")
                ).delete(synchronize_session=False)
                if deleted:
                    logger.info(f"Deleted {deleted} games from round '{r}'.")
            session.commit()
            refresh = True  # Invalidate future rounds: refresh view.
        else:
            # Winner was set; check if all games in current round (global across regions) are complete.
            round_games = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{current_round} -%")
            ).all()
            total_games = len(round_games)
            incomplete_games = [g for g in round_games if not (g.winner and g.winner.strip())]
            logger.info(f"Round '{current_round}': {total_games} total games, {len(incomplete_games)} incomplete.")
            for g in incomplete_games:
                logger.info(f"Game {g.game_id}: {g.team1} vs {g.team2}, current winner: '{g.winner}'")
            
            if total_games > 0 and not incomplete_games:
                # All games in the current round are complete.
                if current_index + 1 < len(ROUND_ORDER):
                    next_round = ROUND_ORDER[current_index + 1]
                    logger.info(f"All games in '{current_round}' complete. Creating/updating '{next_round}' games.")
                    # Load regions from the bracket JSON.
                    with open("tournament_bracket.json") as f:
                        bracket = json.load(f)
                    regions = [r["region_name"] for r in bracket["regions"]]
                    for region in regions:
                        # Query current round games for this region.
                        region_games = session.query(TournamentResult).filter(
                            TournamentResult.round_name == f"{current_round} - {region}"
                        ).order_by(TournamentResult.game_id).all()
                        if not region_games:
                            logger.info(f"No games for region '{region}' in round '{current_round}'; skipping.")
                            continue
                        winners = [g.winner.strip() for g in sorted(region_games, key=lambda g: g.game_id)]
                        # Build expected pairings.
                        pairings = [(winners[i], winners[i+1]) for i in range(0, len(winners), 2) if i+1 < len(winners)]
                        logger.info(f"Region '{region}' expected pairings for '{next_round}': {pairings}")
                        # Query any existing next-round games for this region.
                        next_games = session.query(TournamentResult).filter(
                            TournamentResult.round_name == f"{next_round} - {region}"
                        ).order_by(TournamentResult.game_id).all()
                        if not next_games:
                            last_game = session.query(TournamentResult).order_by(TournamentResult.game_id.desc()).first()
                            new_id = last_game.game_id if last_game else 0
                            for pairing in pairings:
                                new_id += 1
                                new_game = TournamentResult(
                                    game_id=new_id,
                                    round_name=f"{next_round} - {region}",
                                    team1=pairing[0],
                                    team2=pairing[1],
                                    winner=None  # Start with no winner.
                                )
                                session.add(new_game)
                                logger.info(f"Created game {new_id} for {next_round} - {region}: {pairing[0]} vs {pairing[1]}")
                        else:
                            # Update existing next-round games: if pairing has changed, update and clear winner.
                            for idx, pairing in enumerate(pairings):
                                if idx < len(next_games):
                                    next_game = next_games[idx]
                                    if (next_game.team1.strip() != pairing[0] or next_game.team2.strip() != pairing[1]):
                                        logger.info(f"Updating game {next_game.game_id} for {next_round} - {region}: was {next_game.team1} vs {next_game.team2}, updating to {pairing[0]} vs {pairing[1]} (resetting winner).")
                                        next_game.team1 = pairing[0]
                                        next_game.team2 = pairing[1]
                                    # Always clear the winner even if the pairing is unchanged.
                                    next_game.winner = None
                        session.commit()
                    refresh = True  # Refresh view because next-round games have been (re)calculated.
            else:
                # Not all games in current round are complete; delete any future rounds.
                for r in ROUND_ORDER[current_index+1:]:
                    deleted = session.query(TournamentResult).filter(
                        TournamentResult.round_name.like(f"{r} -%")
                    ).delete(synchronize_session=False)
                    if deleted:
                        logger.info(f"Deleted {deleted} games from round '{r}' due to incomplete current round.")
                session.commit()
                refresh = False
        
        return jsonify({"status": "success", "refresh": refresh})
    except Exception as e:
        session.rollback()
        logger.error(f"Error in update_game: {e}")
        return jsonify({"status": "failure", "error": str(e)}), 500
    finally:
        session.close()

@app.route('/generate_pdf')
def generate_pdf_route():
    """
    Route to trigger PDF report generation.
    
    Calculates user scores, generates a PDF report, and then redirects the user
    to the generated PDF file.
    """
    calculate_scoring()
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_filename = f"NCAA_Report_{timestamp}.pdf"
    pdf_path = os.path.join(app.static_folder, pdf_filename)
    generate_report(pdf_path)
    return redirect(url_for('static', filename=pdf_filename))

if __name__ == '__main__':
    # On first run, initialize the database and import bracket/picks data.
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
    
    # Validate that user picks reference valid bracket teams.
    if not validate_picks_against_bracket():
        sys.exit(1)
    
    calculate_scoring()
    app.run(debug=False)
