# app.py
import os
from flask import Flask, render_template, request, redirect, url_for
from config import logger
from db import SessionLocal, TournamentResult, init_db
from google_integration import fetch_picks_from_sheets, update_local_db_with_picks
from scoring import import_tournament_results_from_json, calculate_scoring
from report import generate_report
from sqlalchemy.orm import joinedload

app = Flask(__name__)

@app.before_first_request
def setup_app():
    """
    Called once, before the first request.
    1) Ensure database/tables are created.
    2) Optionally auto-fetch picks from Google Sheets, if you want to keep in sync at startup.
    """
    logger.info("Initializing database...")
    init_db()
    # Example: auto-fetch picks on startup
    # picks_data = fetch_picks_from_sheets()
    # update_local_db_with_picks(picks_data)
    logger.info("App setup complete.")

@app.route('/')
def index():
    """
    Main route: Display bracket in progress.
    """
    session = SessionLocal()
    try:
        # Example: fetch all current results from DB
        # We group them by round or region to display a bracket-like layout
        results = session.query(TournamentResult).all()
        
        # For a typical NCAA bracket, you'd have 'regions' and 'rounds' in some order.
        # This example just lumps everything into a dictionary by round_name.
        from collections import defaultdict
        bracket_data = defaultdict(list)
        for game in results:
            bracket_data[game.round_name].append(game)
        
        # bracket_data might look like:
        # {
        #   "Round of 64": [TournamentResult(...), ...],
        #   "Round of 32": [...],
        #   ...
        # }
        
        return render_template("index.html", bracket_data=bracket_data)
    finally:
        session.close()

@app.route('/update_game', methods=['POST'])
def update_game():
    """
    Form submission endpoint that updates which team won a particular matchup.
    """
    game_id = request.form.get("game_id")
    selected_winner = request.form.get("winner")
    
    if not game_id or not selected_winner:
        return redirect(url_for('index'))
    
    session = SessionLocal()
    try:
        game = session.query(TournamentResult).filter_by(game_id=game_id).first()
        if game:
            game.winner = selected_winner
            session.commit()
            logger.info(f"Updated game {game_id}, winner={selected_winner}")
        else:
            logger.warning(f"No game found with ID {game_id}")
    finally:
        session.close()
    return redirect(url_for('index'))

@app.route('/generate_pdf')
def generate_pdf():
    """
    Trigger generating the PDF report (existing logic).
    """
    pdf_filename = f"NCAA_Report_{os.getpid()}.pdf"
    generate_report(pdf_filename)
    return f"PDF report generated: {pdf_filename}"

if __name__ == '__main__':
    app.run(debug=True)
