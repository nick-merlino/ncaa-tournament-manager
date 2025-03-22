# main.py

import os
import sys
import argparse
from flask import Flask, render_template, request, redirect, url_for
from config import logger
from db import init_db, SessionLocal, TournamentResult
from google_integration import fetch_picks_from_sheets, update_local_db_with_picks, GoogleSheetsError
from scoring import calculate_scoring
from report import generate_report

app = Flask(__name__)

@app.route('/')
def index():
    """
    Main bracket route: if we reach here, Google Sheets linking has already succeeded.
    Display bracket from DB, or placeholders if empty.
    """
    session = SessionLocal()
    try:
        results = session.query(TournamentResult).all()
        if not results:
            logger.info("No bracket data in DB; creating a placeholder 64-team bracket.")
            create_full_bracket_if_empty(session)
            session.commit()
            results = session.query(TournamentResult).all()

        from collections import defaultdict
        bracket_data = defaultdict(list)
        for game in results:
            bracket_data[game.round_name].append(game)

        return render_template("index.html", bracket_data=bracket_data, sheet_error=None)
    finally:
        session.close()

@app.route('/update_game', methods=['POST'])
def update_game():
    """
    Update the winner for a single game.
    """
    game_id = request.form.get("game_id")
    selected_winner = request.form.get("winner")
    if not game_id or not selected_winner:
        logger.warning("No valid game_id or winner selected.")
        return redirect(url_for('index'))

    session = SessionLocal()
    try:
        game = session.query(TournamentResult).filter_by(game_id=game_id).first()
        if game:
            game.winner = selected_winner
            session.commit()
            logger.info(f"Updated game {game_id} winner to '{selected_winner}'")
        else:
            logger.warning(f"No game found with ID={game_id}")
    finally:
        session.close()
    return redirect(url_for('index'))

@app.route('/generate_pdf')
def generate_pdf_route():
    """
    Web route to generate the PDF. But we've already linked Google to get here,
    so just recalc scoring and generate the PDF.
    """
    calculate_scoring(round_weights=None)
    pdf_filename = f"NCAA_Report_{os.getpid()}.pdf"
    generate_report(pdf_filename)
    return f"<h4>PDF report generated successfully:</h4><p>{pdf_filename}</p>"

def run_web_server():
    """
    Start the Flask server. We only call this if Google Sheets linking succeeded.
    """
    app.run(debug=True)

def main():
    parser = argparse.ArgumentParser(
        description="NCAA Picks Tool: forces Google API linking before bracket use."
    )
    parser.add_argument(
        '--web', action='store_true',
        help="Start the bracket web interface (requires successful Sheets linking)."
    )
    parser.add_argument(
        '--report', action='store_true',
        help="Generate a PDF report (also requires successful Sheets linking)."
    )

    args = parser.parse_args()

    # Always init DB
    init_db()

    if not (args.web or args.report):
        parser.print_help()
        sys.exit(0)

    # Attempt to sync picks from Google Sheets. If it fails, we print instructions & exit.
    try:
        picks_data = fetch_picks_from_sheets()
        update_local_db_with_picks(picks_data)
    except GoogleSheetsError as e:
        print(
            "[ERROR] Google Sheets linking failed.\n"
            f"Details: {e}\n\n"
            "Please fix 'credentials.json' (and 'SPREADSHEET_ID' if needed), then rerun.\n"
        )
        sys.exit(1)

    # If we got here, Google linking + picks fetch is fine. Next steps:
    # 1) Recalc scoring
    calculate_scoring(round_weights=None)

    # 2) Depending on the flag, either run the bracket web interface or generate the PDF
    if args.web:
        run_web_server()
    elif args.report:
        pdf_filename = f"NCAA_Report_{os.getpid()}.pdf"
        generate_report(pdf_filename)
        logger.info(f"PDF report generated: {pdf_filename}")
        print(f"PDF report generated successfully: {pdf_filename}")

def create_full_bracket_if_empty(session):
    """
    Creates a placeholder 64-team bracket if none in DB:
    4 regions, each with 15 games for Round of 64 -> Elite 8, plus 2 Final Four, 1 Championship
    """
    REGIONS = ["South", "East", "West", "Midwest"]
    ROUNDS_PER_REGION = [
        ("Round of 64", 8),
        ("Round of 32", 4),
        ("Sweet 16", 2),
        ("Elite 8", 1),
    ]

    game_id_counter = 1

    for region in REGIONS:
        for (round_name, num_games) in ROUNDS_PER_REGION:
            for _ in range(num_games):
                placeholder_game = TournamentResult(
                    game_id=game_id_counter,
                    round_name=f"{round_name} - {region}",
                    team1="",
                    team2="",
                    winner=None
                )
                session.add(placeholder_game)
                game_id_counter += 1

    # 2 Final Four placeholders
    for _ in range(2):
        session.add(
            TournamentResult(
                game_id=game_id_counter,
                round_name="Final Four",
                team1="",
                team2="",
                winner=None
            )
        )
        game_id_counter += 1

    # 1 Championship placeholder
    session.add(
        TournamentResult(
            game_id=game_id_counter,
            round_name="Championship",
            team1="",
            team2="",
            winner=None
        )
    )

if __name__ == "__main__":
    main()
