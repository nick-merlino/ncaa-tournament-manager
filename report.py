# report.py

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

def generate_report(pdf_filename=None):
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

        # Build a DataFrame: columns = [username, seed_label, team_name, points]
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

        # Determine current round from bracket results (base round only)
        current_round, round_games = get_round_game_status()
        if not current_round:
            current_round = "Round of 64"
        else:
            if '-' in current_round:
                current_round = current_round.split('-', 1)[0].strip()

        # Sort users descending by points then alphabetically by name.
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

        # --- Current Round Section ---
        story.append(Paragraph(f"Current Round in Progress: {current_round}", styles['Title']))
        story.append(Spacer(1, 12))
        previous_points = None
        for uname in sorted_users:
            if not df.empty:
                user_pts = user_points_df.loc[user_points_df['username'] == uname, 'points'].values[0]
            else:
                user_pts = 0.0
            if previous_points is not None and user_pts != previous_points:
                story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
                story.append(Spacer(1, 6))
            # New format: "Name - # points"
            header_line = f"{uname} - {user_pts:.0f} points"
            player_flowables = []
            player_flowables.append(Paragraph(header_line, styles['Heading3']))
            picks_for_user = df[df['username'] == uname]
            won_this_round, not_played, out = [], [], []
            for _, row in picks_for_user.iterrows():
                team = row['team_name']
                seed_label = row['seed_label']
                try:
                    seed_int = int(seed_label.replace("Seed", "").strip())
                except ValueError:
                    seed_int = 999
                status = determine_team_status(team, current_round, round_games)
                if status == 'in':
                    won_this_round.append((seed_int, team))
                elif status == 'out':
                    out.append((seed_int, team))
                else:
                    not_played.append((seed_int, team))
            won_this_round.sort(key=lambda x: x[0])
            not_played.sort(key=lambda x: x[0])
            out.sort(key=lambda x: x[0])
            def format_line(category, items):
                if not items:
                    return f"<b>{category}:</b> None"
                part_str = " ".join([f"({sd}) {tm}" for (sd, tm) in items])
                return f"<b>{category}:</b> {part_str}"
            # New ordering: "Won", then "Not Yet Played", then "Out"
            player_flowables.append(Paragraph(format_line("Won", won_this_round), styles['Normal']))
            player_flowables.append(Paragraph(format_line("Not Yet Played", not_played), styles['Normal']))
            player_flowables.append(Paragraph(format_line("Out", out), styles['Normal']))
            story.append(KeepTogether(player_flowables))
            story.append(Spacer(1, 12))
            previous_points = user_pts
        story.append(PageBreak())

        # --- Graphs Section ---
        if not df.empty:
            # Combine Player Score Graph (Line Chart) and Upset Table on one page.
            user_points_sorted = user_points_df.sort_values(by='points', ascending=False)
            x_vals = list(range(1, len(user_points_sorted) + 1))
            fig_line = go.Figure(
                data=[go.Scatter(x=x_vals, y=user_points_sorted['points'], mode="lines+markers")],
                layout=dict(template="plotly_white")
            )
            fig_line.update_layout(
                title="Total Points by User",
                xaxis=dict(title="", showticklabels=False),
                yaxis_title="Points"
            )
            line_img = fig_to_image(fig_line)
            # Upset Table
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
                            'winner': game.winner,
                            'loser': game.team1 if game.winner.strip() == game.team2.strip() else game.team2,
                            'differential': diff
                        })
            top_upsets = sorted(upsets, key=lambda x: x['differential'], reverse=True)[:10]
            upset_table = None
            if top_upsets:
                upset_data = [['Round', 'Winner', 'Loser', 'Seed Differential']]
                for up in top_upsets:
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
            combined_graphs = []
            if line_img:
                combined_graphs.append(Image(BytesIO(line_img), width=350, height=250))
            if upset_table:
                # Add a title above the upset table.
                combined_graphs.append(Paragraph("Top 10 Biggest Upsets", styles['Heading2']))
                combined_graphs.append(upset_table)
            if combined_graphs:
                story.append(KeepTogether(combined_graphs))
                story.append(Spacer(1, 12))
            story.append(PageBreak())

            # Combine "10 Most Popular Teams Still Remaining" and "10 Least Popular Teams Still Remaining" on one page.
            team_counts = df['team_name'].value_counts().reset_index()
            team_counts.columns = ['team_name', 'count']
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
            team_picks = df.groupby('team_name')['username'].nunique().reset_index()
            team_picks.columns = ['team_name', 'pick_count']
            remaining_df = team_picks[team_picks['team_name'].isin(still_remaining)]
            top10_remaining = remaining_df.sort_values(by='pick_count', ascending=False).head(10)
            fig_top_remaining = go.Figure(
                data=[go.Bar(x=top10_remaining['team_name'], y=top10_remaining['pick_count'])],
                layout=dict(template="plotly_white")
            )
            fig_top_remaining.update_layout(title="10 Most Popular Teams Still Remaining",
                                            xaxis_title="", yaxis_title="Number of Picks")
            top_remaining_img = fig_to_image(fig_top_remaining)
            least10_remaining = remaining_df.sort_values(by='pick_count', ascending=True).head(10)
            fig_least_remaining = go.Figure(
                data=[go.Bar(x=least10_remaining['team_name'], y=least10_remaining['pick_count'])],
                layout=dict(template="plotly_white")
            )
            fig_least_remaining.update_layout(title="10 Least Popular Teams Still Remaining",
                                              xaxis_title="", yaxis_title="Number of Picks")
            least_remaining_img = fig_to_image(fig_least_remaining)
            remaining_flowables = []
            if top_remaining_img:
                remaining_flowables.append(Image(BytesIO(top_remaining_img), width=350, height=250))
            if least_remaining_img:
                remaining_flowables.append(Image(BytesIO(least_remaining_img), width=350, height=250))
            if remaining_flowables:
                story.append(KeepTogether(remaining_flowables))
                story.append(Spacer(1, 12))
            story.append(PageBreak())
        doc.build(story)
        logger.info(f"PDF report saved as {pdf_filename}")
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
    finally:
        session.close()

def determine_team_status(team, current_round, round_games):
    """
    Return 'in', 'out', or 'not_played' for a team.
    First, check previous rounds for elimination.
    Then, examine current round games (i.e. those whose round_name starts with current_round):
      - If a game exists and the team is the winner, return 'in'
      - If the team is in a game and loses, return 'out'
      - If the team is in a game but no winner is selected, return 'not_played'
    If the team does not appear in any current round game, return 'not_played'.
    """
    ROUND_ORDER = ["Round of 64", "Round of 32", "Sweet 16", "Elite Eight", "Final Four", "Championship"]
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
                if g['winner'] and g['winner'].strip() != team.strip():
                    return 'out'
    # Check current round games.
    current_games = []
    for rd_name, gs in round_games.items():
        if rd_name.startswith(current_round):
            current_games.extend(gs)
    for g in current_games:
        if team in [g['team1'], g['team2']]:
            if g['winner']:
                if g['winner'].strip() == team.strip():
                    return 'in'
                else:
                    return 'out'
            else:
                return 'not_played'
    return 'not_played'

def fig_to_image(fig):
    from config import logger
    try:
        return fig.to_image(format="png")
    except Exception as e:
        logger.error("Error converting Plotly figure to PNG: %s", e)
        return None
