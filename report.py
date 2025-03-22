# report.py

import datetime
from io import BytesIO

import pandas as pd
import plotly.graph_objects as go
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                PageBreak, Table, TableStyle)
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader

from config import logger
from db import SessionLocal, User, UserPick, UserScore
from scoring import get_round_game_status

def generate_report(pdf_filename=None):
    """
    Build a PDF showing picks, points, who is in/out/not-played in the current round.
    Uses a modern Plotly theme for the bar chart. 
    """
    if not pdf_filename:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"NCAA_Report_{timestamp}.pdf"

    doc = SimpleDocTemplate(pdf_filename, pagesize=LETTER)
    story = []
    styles = getSampleStyleSheet()

    # Title
    story.append(Paragraph("NCAA Tournament Picks Report", styles['Title']))
    story.append(Spacer(1, 12))

    session = SessionLocal()
    try:
        from sqlalchemy.orm import joinedload
        all_users = session.query(User).options(joinedload(User.picks)).all()
        user_scores = {us.user_id: us for us in session.query(UserScore).all()}

        data_rows = []
        for user in all_users:
            points = user_scores[user.user_id].points if user.user_id in user_scores else 0.0
            for pick in user.picks:
                data_rows.append({
                    "username": user.full_name,
                    "seed_label": pick.seed_label,
                    "team_name": pick.team_name,
                    "points": points
                })

        df = pd.DataFrame(data_rows)
        if not df.empty:
            # Table of picks
            table_data = [["User", "Seed", "Team", "Points"]]
            for _, row in df.iterrows():
                table_data.append([
                    row["username"],
                    row["seed_label"],
                    row["team_name"],
                    f"{row['points']:.1f}"
                ])

            picks_table = Table(table_data, hAlign='LEFT')
            picks_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ]))
            story.append(picks_table)
        else:
            story.append(Paragraph("No picks to display.", styles['Normal']))

        story.append(PageBreak())

        # Visualization: Bar Chart of Points (use 'plotly_white' for modern look)
        if not df.empty:
            user_points = (df[['username', 'points']].drop_duplicates()
                           .groupby('username')['points']
                           .max()
                           .reset_index())

            fig_bar = go.Figure(data=[go.Bar(x=user_points['username'], y=user_points['points'])])
            fig_bar.update_layout(
                template="plotly_white",
                title="Total Points by User",
                xaxis_title="User",
                yaxis_title="Points"
            )
            bar_img = fig_to_image(fig_bar)
            if bar_img:
                story.append(Paragraph("Points Distribution", styles['Heading2']))
                story.append(Image(ImageReader(BytesIO(bar_img)), width=400, height=300))
                story.append(Spacer(1, 12))
            story.append(PageBreak())

        # Show user picks for the current round
        current_round, round_games = get_round_game_status()
        story.append(Paragraph(f"Current Round in Progress: {current_round}", styles['Heading2']))
        story.append(Spacer(1, 12))

        for user in all_users:
            story.append(Paragraph(f"User: {user.full_name}", styles['Heading3']))
            picks_for_user = df[df['username'] == user.full_name]
            still_in, out, not_played = [], [], []

            for _, row in picks_for_user.iterrows():
                team = row['team_name']
                status = determine_team_status(team, current_round, round_games)
                if status == 'in':
                    still_in.append(team)
                elif status == 'out':
                    out.append(team)
                else:
                    not_played.append(team)

            story.extend(make_status_table("Still In", still_in, styles))
            story.extend(make_status_table("Out", out, styles))
            story.extend(make_status_table("Not Yet Played", not_played, styles))
            story.append(PageBreak())

        doc.build(story)
        logger.info(f"PDF report saved as {pdf_filename}")
    except Exception as e:
        logger.error(f"Error generating PDF report: {e}")
    finally:
        session.close()

def make_status_table(title, items, styles):
    if not items:
        return [Paragraph(f"{title}: None", styles['Normal']), Spacer(1, 6)]
    data = [[title, "Team"]] + [[title if i == 0 else "", t] for i, t in enumerate(items)]
    tbl = Table(data, hAlign='LEFT')
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold'),
    ]))
    return [tbl, Spacer(1, 12)]

def determine_team_status(team, current_round, round_games):
    """
    Return 'in', 'out', or 'not_played' based on bracket data.
    """
    for rd_name, games in round_games.items():
        for g in games:
            if team in [g['team1'], g['team2']]:
                if g['winner']:
                    if g['winner'] == team:
                        return 'in'
                    else:
                        return 'out'
                else:
                    if rd_name == current_round:
                        return 'not_played'
                    return 'not_played'
    return 'out'  # fallback

def fig_to_image(fig):
    """
    Convert a Plotly figure to PNG bytes using kaleido.
    Returns None if there's an error, logs error clearly.
    """
    from config import logger
    try:
        return fig.to_image(format="png")
    except Exception as e:
        logger.error(
            "Error converting Plotly figure to image with kaleido. "
            f"Ensure kaleido is installed and up to date. Details: {e}"
        )
        return None
