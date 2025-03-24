"""
report.py

This module generates a PDF report for the NCAA Tournament application.
It includes:
  - A current round section showing player picks and scores,
    with players grouped by score levels (with a horizontal separator between groups).
  - Several visual sections:
      * A modern line chart showing "Player Points" with player names as x-axis labels.
      * An upsets table that lists all games with seed differentials (all upsets).
      * Bar charts for the 10 most popular and 10 least popular teams still remaining.
Each visual is grouped with its title so that they remain on the same page.
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
from scoring import get_round_game_status
from constants import ROUND_ORDER, ROUND_WEIGHTS  # Shared constants


def generate_report(pdf_filename=None):
    """
    Generates a PDF report for the tournament.

    The report includes:
      - Current round information with player picks and score breakdown.
      - Visual sections including charts and tables.
    Each visual is kept together with its title on the same page.
    
    Args:
        pdf_filename (str, optional): Output PDF filename. If not provided, a timestamp-based name is used.
    """
    if not pdf_filename:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"NCAA_Report_{timestamp}.pdf"

    # Use narrow margins: 0.5 inch (36 points)
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

        # Build DataFrame: each row corresponds to one user pick with associated score.
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

        # Sort users descending by points then alphabetically.
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
        # Process each user's picks
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
            
            # Collect picks as tuples (seed_int, team_display)
            still_in_picks = []
            not_played_picks = []
            out_picks = []
            for _, row in df[df['username'] == uname].iterrows():
                team = row['team_name']
                seed_label = row['seed_label']
                try:
                    seed_int = int(seed_label.replace("Seed", "").strip())
                except ValueError:
                    seed_int = 999
                status = determine_team_status(team, current_round, visible_rounds)
                # Calculate team points based on wins in each round (only add once per round).
                team_points = 0
                for r in ROUND_ORDER:
                    if r in visible_rounds:
                        for game in visible_rounds[r]:
                            if game.get('winner') and game['winner'].strip() == team.strip():
                                team_points += ROUND_WEIGHTS.get(r, 0)
                                break
                team_display = f"{seed_int}({team_points}) {team}"
                if status == 'in':
                    still_in_picks.append((seed_int, team_display))
                elif status == 'out':
                    out_picks.append((seed_int, team_display))
                else:
                    not_played_picks.append((seed_int, team_display))
            
            # Sort each list numerically by the seed value
            still_in_list = [display for seed, display in sorted(still_in_picks, key=lambda x: x[0])]
            not_played_list = [display for seed, display in sorted(not_played_picks, key=lambda x: x[0])]
            out_list = [display for seed, display in sorted(out_picks, key=lambda x: x[0])]
            
            def format_category(category, items):
                if not items:
                    return f"<b>{category}:</b> None"
                return f"<b>{category} ({len(items)}):</b> " + ", ".join(items)
            
            player_flowables.append(Paragraph(format_category("Won This Round", still_in_list), styles['Normal']))
            player_flowables.append(Paragraph(format_category("Not Played Yet", not_played_list), styles['Normal']))
            player_flowables.append(Paragraph(format_category("Out", out_list), styles['Normal']))
            
            story.append(KeepTogether(player_flowables))
            story.append(Spacer(1, 12))
            previous_points = user_pts
        
        story.append(PageBreak())

        # ---------------- Visuals Section ----------------
        visuals = []

        # -- 10 Most Popular Teams Still Remaining --
        team_picks = df.groupby('team_name')['username'].nunique().reset_index()
        team_picks.columns = ['team_name', 'pick_count']
        first_round_games = session.query(TournamentResult).filter(TournamentResult.round_name.like("Round of 64%")).all()
        all_teams = set()
        for game in first_round_games:
            all_teams.add(game.team1)
            all_teams.add(game.team2)
        decided_games = session.query(TournamentResult).filter(TournamentResult.winner.isnot(None)).all()
        losers = set()
        for game in decided_games:
            if game.winner.strip() == game.team1.strip():
                losers.add(game.team2)
            elif game.winner.strip() == game.team2.strip():
                losers.add(game.team1)
        still_remaining = all_teams - losers
        remaining_df = team_picks[team_picks['team_name'].isin(still_remaining)]
        # Limit to top 10 teams.
        top_remaining = remaining_df.sort_values(by='pick_count', ascending=False).head(10)
        fig_top_remaining = go.Figure(
            data=[go.Bar(x=top_remaining['team_name'], y=top_remaining['pick_count'])],
            layout=dict(template="plotly_white")
        )
        fig_top_remaining.update_layout(title="", xaxis_title="Team", yaxis_title="Number of Picks")
        top_remaining_img = fig_to_image(fig_top_remaining)
        top_remaining_title = Paragraph('<para align="center"><b>10 Most Popular Teams Still Remaining</b></para>', styles['Heading2'])
        popular_group = [top_remaining_title]
        if top_remaining_img:
            popular_group.append(Image(BytesIO(top_remaining_img), width=400, height=300))
        visuals.append(KeepTogether(popular_group))
        visuals.append(Spacer(1, 12))

        # -- 10 Least Popular Teams Still Remaining --
        least_remaining = remaining_df.sort_values(by='pick_count', ascending=True).head(10)
        fig_least_remaining = go.Figure(
            data=[go.Bar(x=least_remaining['team_name'], y=least_remaining['pick_count'])],
            layout=dict(template="plotly_white")
        )
        fig_least_remaining.update_layout(title="", xaxis_title="Team", yaxis_title="Number of Picks")
        least_remaining_img = fig_to_image(fig_least_remaining)
        least_remaining_title = Paragraph('<para align="center"><b>10 Least Popular Teams Still Remaining</b></para>', styles['Heading2'])
        least_group = [least_remaining_title]
        if least_remaining_img:
            least_group.append(Image(BytesIO(least_remaining_img), width=400, height=300))
        visuals.append(KeepTogether(least_group))
        visuals.append(Spacer(1, 12))

        # -- Player Points Line Chart --
        if not df.empty:
            # Use player names as x-axis labels.
            user_points_sorted = user_points_df.sort_values(by='points', ascending=False)
            x_vals = user_points_sorted['username'].tolist()
            fig_line = go.Figure(
                data=[go.Scatter(x=x_vals, y=user_points_sorted['points'], mode="lines+markers")],
                layout=dict(template="plotly_white")
            )
            fig_line.update_layout(
                title="",
                xaxis_title="Player",
                yaxis_title="Points",
                xaxis_tickangle=-45,
                width=800,  # Fixed width
                margin=dict(l=40, r=40, t=40, b=150),
                xaxis=dict(tickfont=dict(size=10))
            )
            line_img = fig_to_image(fig_line)
        else:
            line_img = None

        player_points_title = Paragraph('<para align="center"><b>Player Points</b></para>', styles['Heading2'])
        pp_group = [player_points_title]
        if line_img:
            pp_group.append(Image(BytesIO(line_img), width=500, height=300))
        visuals.append(KeepTogether(pp_group))
        visuals.append(Spacer(1, 12))

        # -- Upsets Table (All Upsets) --
        with open("tournament_bracket.json", 'r') as f:
            bracket_info = json.load(f)
        team_seeds = {team['team_name']: team['seed'] for region in bracket_info['regions'] for team in region['teams']}
        upsets = []
        decided = session.query(TournamentResult).filter(TournamentResult.winner.isnot(None)).all()
        for game in decided:
            if game.winner:
                team1_seed = team_seeds.get(game.team1, 999)
                team2_seed = team_seeds.get(game.team2, 999)
                if game.winner.strip() == game.team1.strip():
                    winner_seed = team1_seed
                    loser_seed = team2_seed
                else:
                    winner_seed = team2_seed
                    loser_seed = team1_seed
                if winner_seed > loser_seed:
                    diff = winner_seed - loser_seed
                    upsets.append({
                        'round': game.round_name,
                        'winner': f"({winner_seed}) {game.winner}",
                        'loser': f"({loser_seed}) " + (game.team1 if game.winner.strip() == game.team2.strip() else game.team2),
                        'differential': diff
                    })
        upset_table = None
        if upsets:
            upset_data = [['Round', 'Winner', 'Loser', 'Seed Differential']]
            for up in sorted(upsets, key=lambda x: x['differential'], reverse=True):
                upset_data.append([up['round'], up['winner'], up['loser'], up['differential']])
            upset_table = Table(upset_data)
            upset_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
        upset_title = Paragraph('<para align="center"><b>Games with Biggest Upsets</b></para>', styles['Heading2'])
        upset_group = [upset_title]
        if upset_table:
            upset_group.append(upset_table)
        visuals.append(KeepTogether(upset_group))
        visuals.append(Spacer(1, 12))

        # Add all visual groups to the story.
        for group in visuals:
            story.append(group)
        
        # Build the PDF with page numbers.
        doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
        logger.info(f"PDF report saved as {pdf_filename}")
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
    finally:
        session.close()


def determine_team_status(team, current_round, round_games):
    """
    Determines the status of a team based on tournament progress.
    
    Returns:
      - 'in': if the team has won in current or previous rounds.
      - 'out': if the team has lost in any round.
      - 'not_played': if the team is scheduled but no result is recorded.
    
    Args:
        team (str): The team name.
        current_round (str): The base name of the current round (e.g., "Round of 64").
        round_games (dict): Dictionary mapping round names to lists of game dictionaries.
    
    Returns:
        str: 'in', 'out', or 'not_played'
    """
    from constants import ROUND_ORDER
    current_index = ROUND_ORDER.index(current_round)
    # Check previous rounds for elimination.
    for i in range(current_index):
        r = ROUND_ORDER[i]
        prev_games = []
        for rd_name, gs in round_games.items():
            if rd_name.startswith(r):
                prev_games.extend(gs)
        for g in prev_games:
            if team in [g['team1'], g['team2']]:
                if g.get('winner') and g['winner'].strip() != team.strip():
                    return 'out'
    # Check current round games.
    current_games = []
    for rd_name, gs in round_games.items():
        if rd_name.startswith(current_round):
            current_games.extend(gs)
    for g in current_games:
        if team in [g['team1'], g['team2']]:
            if g.get('winner'):
                if g['winner'].strip() == team.strip():
                    return 'in'
                else:
                    return 'out'
            else:
                return 'not_played'
    return 'not_played'


def fig_to_image(fig):
    """
    Converts a Plotly figure to a PNG image in memory.
    
    Args:
        fig (plotly.graph_objects.Figure): The figure to convert.
    
    Returns:
        bytes: The PNG image as bytes, or None if an error occurs.
    """
    from config import logger
    try:
        return fig.to_image(format="png")
    except Exception as e:
        logger.error("Error converting Plotly figure to PNG: %s", e)
        return None


def add_page_number(canvas, doc):
    """
    Draws the page number at the bottom center of each page.
    
    Args:
        canvas: The canvas to draw on.
        doc: The document object.
    """
    page_num = canvas.getPageNumber()
    text = f"Page {page_num}"
    canvas.drawCentredString(LETTER[0] / 2.0, 20, text)
