# main.py

import os
import sys
import json
from collections import defaultdict
from flask import Flask, render_template, request, jsonify, redirect, url_for
from config import logger, DATABASE_URL
from db import init_db, SessionLocal, TournamentResult, UserPick
from google_integration import fetch_picks_from_sheets, update_local_db_with_picks, GoogleSheetsError
from scoring import calculate_scoring, get_round_game_status, get_unlocked_rounds
from report import generate_report

app = Flask(__name__)

TOURNAMENT_BRACKET_JSON = "tournament_bracket.json"
ROUND_ORDER = ["Round of 64", "Round of 32", "Sweet 16", "Elite 8", "Final Four", "Championship"]
first_round_pairings = [(1, 16), (8, 9), (5, 12), (4, 13), (6, 11), (3, 14), (7, 10), (2, 15)]

def get_available_base_rounds():
    session = SessionLocal()
    try:
        all_rounds = session.query(TournamentResult.round_name).distinct().all()
        base_rounds = set()
        for r in all_rounds:
            base_round = r[0].split('-')[0].strip()
            base_rounds.add(base_round)
        return sorted(list(base_rounds), key=lambda x: ROUND_ORDER.index(x))
    finally:
        session.close()

def get_default_round():
    session = SessionLocal()
    try:
        for base_round in ROUND_ORDER:
            games = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{base_round}%")
            ).all()
            if games and any(g.winner is None for g in games):
                return base_round
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
    from collections import defaultdict
    # Group winners by region from current round games.
    region_winners = defaultdict(list)
    for game in current_results:
        if game.winner:
            region = game.round_name.split('-', 1)[1].strip() if '-' in game.round_name else "Unknown"
            region_winners[region].append(game)
    current_round_name = current_results[0].round_name.split('-', 1)[0].strip()
    current_round_index = ROUND_ORDER.index(current_round_name)
    if current_round_index + 1 >= len(ROUND_ORDER):
        return
    next_round_name_base = ROUND_ORDER[current_round_index + 1]
    # Delete any existing next round games for these regions.
    for region in region_winners.keys():
        existing_next = session.query(TournamentResult).filter(
            TournamentResult.round_name == f"{next_round_name_base} - {region}"
        ).all()
        for game in existing_next:
            session.delete(game)
    session.commit()
    # Get current maximum game_id and assign new game_ids sequentially.
    max_game = session.query(TournamentResult).order_by(TournamentResult.game_id.desc()).first()
    next_game_id = max_game.game_id + 1 if max_game else 1
    for region, games in region_winners.items():
        if len(games) % 2 != 0:
            continue
        games.sort(key=lambda g: g.game_id)
        for i in range(0, len(games), 2):
            if i + 1 < len(games):
                winner1 = games[i].winner
                winner2 = games[i+1].winner
                if winner1 and winner2:
                    new_game = TournamentResult(
                        game_id=next_game_id,
                        round_name=f"{next_round_name_base} - {region}",
                        team1=winner1,
                        team2=winner2,
                        winner=None
                    )
                    session.add(new_game)
                    next_game_id += 1
    session.commit()

def import_bracket_from_json(json_file):
    from db import TournamentResult
    from config import logger
    session = SessionLocal()
    try:
        if session.query(TournamentResult).count() > 0:
            logger.info("Matchup data exists. Skipping bracket import.")
            return True
        with open(json_file, 'r') as f:
            data = json.load(f)
        regions = data.get("regions", [])
        if len(regions) != 4:
            print(f"[ERROR] Expected 4 regions, found {len(regions)}.")
            return False
        game_id_counter = 1
        for region_info in regions:
            region_name = region_info["region_name"]
            teams = region_info["teams"]
            if len(teams) != 16:
                print(f"[ERROR] Region '{region_name}' must have 16 seeds, found {len(teams)}.")
                return False
            pairing_order = [(1,16), (8,9), (5,12), (4,13), (6,11), (3,14), (7,10), (2,15)]
            seed_to_team = {team['seed']: team['team_name'] for team in teams}
            for pair in pairing_order:
                team1 = seed_to_team.get(pair[0])
                team2 = seed_to_team.get(pair[1])
                if not team1 or not team2:
                    print(f"[ERROR] Missing team for seeds {pair} in region {region_name}.")
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
    from db import TournamentResult, UserPick
    session = SessionLocal()
    try:
        bracket_teams = set()
        for g in session.query(TournamentResult).all():
            bracket_teams.add(g.team1)
            bracket_teams.add(g.team2)
        invalid_picks = []
        picks = session.query(UserPick).all()
        for p in picks:
            if p.team_name not in bracket_teams:
                invalid_picks.append((p.user_id, p.team_name))
        if invalid_picks:
            print("\n[ERROR] The following picks reference teams not in the official bracket:")
            for (uid, team) in invalid_picks:
                print(f" - user_id={uid}, team='{team}'")
            return False
        print("[INFO] All picks match bracket teams.")
        return True
    finally:
        session.close()

@app.route('/')
def index():
    session = SessionLocal()
    try:
        available_base_rounds = get_available_base_rounds()
        default_round = get_default_round()
        selected_round = request.args.get('round', default_round)
        if not available_base_rounds:
            return render_template("index.html", region_data={}, selected_round="None", available_base_rounds=[])
        if selected_round not in available_base_rounds:
            selected_round = default_round or ROUND_ORDER[0]
        results = session.query(TournamentResult).filter(
            TournamentResult.round_name.like(f"{selected_round}%")
        ).all()
        from collections import defaultdict
        region_data = defaultdict(list)
        with open(TOURNAMENT_BRACKET_JSON, 'r') as f:
            bracket_data = json.load(f)
        team_seeds = {team['team_name']: team['seed'] for region in bracket_data['regions'] for team in region['teams']}
        for game in results:
            region = game.round_name.split('-', 1)[1].strip() if '-' in game.round_name else "No Region"
            region_data[region].append(game)
        if selected_round == "Round of 64":
            try:
                region_data = {region: sorted(games, key=lambda g: first_round_pairings.index((
                    min(team_seeds.get(g.team1.strip(), 999), team_seeds.get(g.team2.strip(), 999)),
                    max(team_seeds.get(g.team1.strip(), 999), team_seeds.get(g.team2.strip(), 999))
                ))) for region, games in region_data.items()}
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
    data = request.get_json()
    game_id = data.get('game_id')
    winner = data.get('winner')
    if not winner or not winner.strip():
        winner = None
    session = SessionLocal()
    next_round = None
    try:
        game = session.query(TournamentResult).filter_by(game_id=game_id).first()
        if game and winner in [game.team1.strip(), game.team2.strip()]:
            game.winner = winner
            session.commit()
            current_round = game.round_name.split('-', 1)[0].strip()
            current_results = session.query(TournamentResult).filter(
                TournamentResult.round_name.like(f"{current_round}%")
            ).all()
            if all(g.winner is not None for g in current_results):
                create_next_round_games(session, current_results)
                next_round = get_default_round()
        return jsonify({"status": "success", "next_round": next_round})
    finally:
        session.close()

@app.route('/generate_pdf')
def generate_pdf_route():
    calculate_scoring()
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_filename = f"NCAA_Report_{timestamp}.pdf"
    pdf_path = os.path.join(app.static_folder, pdf_filename)
    generate_report(pdf_path)
    # Redirect directly to the PDF file so it opens in a new tab.
    return redirect(url_for('static', filename=pdf_filename))

if __name__ == '__main__':
    if not os.path.exists(DATABASE_URL.replace("sqlite:///", "")):
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
        logger.info("Database exists. Not clearing matchup data.")
        if not import_bracket_from_json(TOURNAMENT_BRACKET_JSON):
            logger.error("Bracket import failed.")
    def validate_round_pairings():
        try:
            with open(TOURNAMENT_BRACKET_JSON, 'r') as f:
                bracket_data = json.load(f)
            team_seeds = {team['team_name']: team['seed'] for region in bracket_data['regions'] for team in region['teams']}
        except Exception as e:
            logger.error(f"Error loading bracket JSON for validation: {e}")
            return False
        session = SessionLocal()
        try:
            games = session.query(TournamentResult).filter(TournamentResult.round_name.like("Round of 64%")).all()
            for game in games:
                seed1 = team_seeds.get(game.team1.strip(), 999)
                seed2 = team_seeds.get(game.team2.strip(), 999)
                pairing = (min(seed1, seed2), max(seed1, seed2))
                if pairing not in first_round_pairings:
                    logger.error(f"Mismatch in pairing for game_id {game.game_id}: pairing {pairing} not expected.")
                    return False
            return True
        finally:
            session.close()
    if not validate_round_pairings():
        logger.error("Bracket pairing validation failed. Exiting.")
        sys.exit(1)
    if not validate_picks_against_bracket():
        sys.exit(1)
    calculate_scoring()
    app.run(debug=True)
