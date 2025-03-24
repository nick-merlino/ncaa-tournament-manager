"""
report.py

This module generates a PDF report for the NCAA Tournament application.
It includes:
  - A current round section showing player picks and scores,
    with players grouped by score levels.
  - Several visual sections including charts and tables.

The current round (and visible rounds) is determined recursively using
get_round_game_status(), so that a round is only visible if all games in every
previous round (across regions) are complete.
"""

import datetime
from io import BytesIO
import json
import pandas as pd
import plotly.graph_objects as go

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak,
    HRFlowable, KeepTogether, Table, TableStyle
)
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from config import logger
from db import SessionLocal, User, UserPick, UserScore, TournamentResult
from scoring import get_round_game_status  # recursive round-check function
from constants import ROUND_ORDER, ROUND_WEIGHTS  # Shared constants

def generate_report(pdf_filename=None):
    """
    Generates a PDF report. The report title and grouping are based on the current round,
    which is determined recursively so that later rounds (and their scoring) are visible only
    if all previous rounds (across regions) are complete.
    """
    if not pdf_filename:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"NCAA_Report_{timestamp}.pdf"

    doc = SimpleDocTemplate(pdf_filename, pagesize=LETTER,
                            leftMargin=36, rightMargin=36,
                            topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()
    session = SessionLocal()

    try:
        from sqlalchemy.orm import joinedload
        all_users = session.query(User).options(joinedload(User.picks)).all()
        user_scores = {us.user_id: us for us in session.query(UserScore).all()}

        # Build a DataFrame: each row corresponds to one user pick with associated score.
        data_rows = []
        for user in all_users:
            score_obj = user_scores.get(user.user_id)
            user_points = score_obj.points if score_obj else 0.0
            for pick in user.picks:
                data_rows.append({
                    "username": user.full_name,
                    "seed_label": pick.seed_label,
                    "team_name": pick.team_name,
                    "points": user_points
                })
        df = pd.DataFrame(data_rows)

        # Determine current round and visible rounds recursively.
        current_round, visible_rounds = get_round_game_status()
        if not current_round:
            current_round = ROUND_ORDER[0]
        story.append(Paragraph(f"Current Round in Progress: {current_round}", styles['Title']))
        story.append(Paragraph('<para align="center"><font size="8" color="grey">Team key: seed(points)-Team Name</font></para>', styles['Normal']))
        story.append(Spacer(1, 12))

        # Sort users by points descending then alphabetically.
        if not df.empty:
            user_points_df = (df[['username', 'points']]
                              .drop_duplicates()
                              .groupby('username')['points']
                              .max().reset_index())
            user_points_df = user_points_df.sort_values(by=['points', 'username'],
                                                         ascending=[False, True])
            sorted_users = user_points_df['username'].tolist()
        else:
            sorted_users = sorted([u.full_name for u in all_users])

        previous_points = None
        for uname in sorted_users:
            if not df.empty:
                user_pts = user_points_df.loc[user_points_df['username'] == uname, 'points'].values[0]
            else:
                user_pts = 0.0
            if previous_points is not None and user_pts != previous_points:
                story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
                story.append(Spacer(1, 6))
            header_line = f"{uname} - <b>Points:</b> {user_pts:.0f}"
            player_flowables = [Paragraph(header_line, styles['Heading3'])]
            still_in_list, not_played_list, out_list = [], [], []
            for _, row in df[df['username'] == uname].iterrows():
                team = row['team_name']
                seed_label = row['seed_label']
                try:
                    seed_int = int(seed_label.replace("Seed", "").strip())
                except ValueError:
                    seed_int = 999
                status = determine_team_status(team, current_round, visible_rounds.get(current_round, []))
                team_points = 0
                # Calculate points for rounds present in visible_rounds.
                for r in ROUND_ORDER:
                    if r in visible_rounds:
                        for game in visible_rounds[r]:
                            if game.get('winner') and game['winner'].strip() == team.strip():
                                team_points += ROUND_WEIGHTS.get(r, 0)
                                break
                team_display = f"{seed_int}({team_points}) {team}"
                if status == 'in':
                    still_in_list.append(team_display)
                elif status == 'out':
                    out_list.append(team_display)
                else:
                    not_played_list.append(team_display)
            def format_category(category, items):
                if not items:
                    return f"<b>{category}:</b> None"
                return f"<b>{category} ({len(items)}):</b> " + ", ".join(items)
            player_flowables.append(Paragraph(format_category("Still In", still_in_list), styles['Normal']))
            player_flowables.append(Paragraph(format_category("Not Played Yet", not_played_list), styles['Normal']))
            player_flowables.append(Paragraph(format_category("Out", out_list), styles['Normal']))
            story.append(KeepTogether(player_flowables))
            story.append(Spacer(1, 12))
            previous_points = user_pts

        story.append(PageBreak())

        # Visuals section (example: Player Points line chart)
        visuals = []
        if not df.empty:
            user_points_sorted = user_points_df.sort_values(by='points', ascending=False)
            x_vals = list(range(1, len(user_points_sorted) + 1))
            fig_line = go.Figure(
                data=[go.Scatter(x=x_vals, y=user_points_sorted['points'], mode="lines+markers")],
                layout=dict(template="plotly_white")
            )
            fig_line.update_layout(
                title="",
                xaxis=dict(title="", showticklabels=False),
                yaxis_title="Points"
            )
            line_img = fig_to_image(fig_line)
        else:
            line_img = None

        player_points_title = Paragraph('<para align="center"><b>Player Points</b></para>', styles['Heading2'])
        pp_group = [player_points_title]
        if line_img:
            pp_group.append(Image(BytesIO(line_img), width=350, height=250))
        visuals.append(KeepTogether(pp_group))
        visuals.append(Spacer(1, 12))

        # (Additional visual sections could be added here.)

        for group in visuals:
            story.append(group)

        # Build the PDF with page numbers.
        doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
        logger.info(f"PDF report saved as {pdf_filename}")
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
    finally:
        session.close()

def determine_team_status(team, current_round, current_games):
    """
    Determines the status of a team based on tournament progress.
    
    Returns:
      - 'in': if the team appears as a winner in current_games.
      - 'not_played': otherwise.
    """
    for g in current_games:
        if team == g.get('winner', '').strip():
            return 'in'
    return 'not_played'

def fig_to_image(fig):
    from config import logger
    try:
        return fig.to_image(format="png")
    except Exception as e:
        logger.error("Error converting Plotly figure to PNG: %s", e)
        return None

def add_page_number(canvas, doc):
    page_num = canvas.getPageNumber()
    text = f"Page {page_num}"
    canvas.drawCentredString(LETTER[0] / 2.0, 20, text)
