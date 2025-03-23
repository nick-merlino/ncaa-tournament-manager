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

        # Define round_weights (currently unweighted: all wins are 1 point)
        round_weights = {
            "Round of 64": 1,
            "Round of 32": 1,
            "Sweet 16": 1,
            "Elite 8": 1,
            "Final Four": 1,
            "Championship": 1
        }

        # Build winners_by_round mapping (if needed later)
        winners_by_round = {}
        for round_key, games in round_games.items():
            base_round = round_key.split('-', 1)[0].strip() if '-' in round_key else round_key.strip()
            for g in games:
                if g['winner']:
                    winners_by_round.setdefault(base_round, set()).add(g['winner'].strip())

        # --- Current Round Section ---
        story.append(Paragraph(f"Current Round in Progress: {current_round}", styles['Title']))
        # Centered, small, grey subtitle for team key (no extra space after)
        story.append(Paragraph('<para align="center"><font size="8" color="grey">Team key: seed(points)-Team Name</font></para>', styles['Normal']))
        story.append(Spacer(1, 12))

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

        previous_points = None
        for uname in sorted_users:
            if not df.empty:
                user_pts = user_points_df.loc[user_points_df['username'] == uname, 'points'].values[0]
            else:
                user_pts = 0.0
            # Separator between different score groups if scores differ
            if previous_points is not None and user_pts != previous_points:
                story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
                story.append(Spacer(1, 6))
            # Header: show name and bold Points label
            header_line = f"{uname} - <b>Points:</b> {user_pts:.0f}"
            player_flowables = [Paragraph(header_line, styles['Heading3'])]
            picks_for_user = df[df['username'] == uname]
            won_list, not_played_list, out_list = [], [], []
            for _, row in picks_for_user.iterrows():
                team = row['team_name']
                seed_label = row['seed_label']
                try:
                    seed_int = int(seed_label.replace("Seed", "").strip())
                except ValueError:
                    seed_int = 999
                status = determine_team_status(team, current_round, round_games)
                # Calculate points for this team for the player:
                # For each base round, add the round's weight only once if team won any game in that round.
                team_points = 0
                for r in ["Round of 64", "Round of 32", "Sweet 16", "Elite 8", "Final Four", "Championship"]:
                    for round_key, games in round_games.items():
                        if round_key.startswith(r):
                            if any(g['winner'] and g['winner'].strip() == team.strip() for g in games):
                                team_points += round_weights.get(r, 0)
                                break
                team_display = f"{seed_int}({team_points}) {team}"
                if status == 'in':
                    won_list.append(team_display)
                elif status == 'out':
                    out_list.append(team_display)
                else:
                    not_played_list.append(team_display)
            def format_category(category, items):
                if not items:
                    return f"<b>{category}:</b> None"
                return f"<b>{category} ({len(items)}):</b> " + ", ".join(items)
            player_flowables.append(Paragraph(format_category("Won", won_list), styles['Normal']))
            player_flowables.append(Paragraph(format_category("Not Played Yet", not_played_list), styles['Normal']))
            player_flowables.append(Paragraph(format_category("Out", out_list), styles['Normal']))
            story.append(KeepTogether(player_flowables))
            story.append(Spacer(1, 12))
            previous_points = user_pts
        story.append(PageBreak())

        # --- Graphs Section (Existing Visuals) ---
        if not df.empty:
            # Group 1: Player Points and Upsets visuals on one page.
            # Player Points chart:
            user_points_sorted = user_points_df.sort_values(by='points', ascending=False)
            x_vals = list(range(1, len(user_points_sorted) + 1))
            fig_line = go.Figure(
                data=[go.Scatter(x=x_vals, y=user_points_sorted['points'], mode="lines+markers")],
                layout=dict(template="plotly_white")
            )
            # Remove title from the image so it is not baked in.
            fig_line.update_layout(
                title="",
                xaxis=dict(title="", showticklabels=False),
                yaxis_title="Points"
            )
            line_img = fig_to_image(fig_line)
            player_points_title = Paragraph('<para align="center"><b>Player Points</b></para>', styles['Heading2'])
            
            # Upset Table – list all upsets and include team seed with team name.
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
            
            group1 = []
            group1.append(player_points_title)
            if line_img:
                group1.append(Image(BytesIO(line_img), width=350, height=250))
            group1.append(upset_title)
            if upset_table:
                group1.append(upset_table)
            if group1:
                story.append(KeepTogether(group1))
                story.append(Spacer(1, 12))
            # Group 2: Most and Least Popular Teams visuals on one page.
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
            top_remaining = remaining_df.sort_values(by='pick_count', ascending=False)
            fig_top_remaining = go.Figure(
                data=[go.Bar(x=top_remaining['team_name'], y=top_remaining['pick_count'])],
                layout=dict(template="plotly_white")
            )
            fig_top_remaining.update_layout(title="", xaxis_title="", yaxis_title="Number of Picks")
            top_remaining_img = fig_to_image(fig_top_remaining)
            top_remaining_title = Paragraph('<para align="center"><b>10 Most Popular Teams Still Remaining</b></para>', styles['Heading2'])
            
            least_remaining = remaining_df.sort_values(by='pick_count', ascending=True)
            fig_least_remaining = go.Figure(
                data=[go.Bar(x=least_remaining['team_name'], y=least_remaining['pick_count'])],
                layout=dict(template="plotly_white")
            )
            fig_least_remaining.update_layout(title="", xaxis_title="", yaxis_title="Number of Picks")
            least_remaining_img = fig_to_image(fig_least_remaining)
            least_remaining_title = Paragraph('<para align="center"><b>10 Least Popular Teams Still Remaining</b></para>', styles['Heading2'])
            
            group2 = []
            group2.append(top_remaining_title)
            if top_remaining_img:
                group2.append(Image(BytesIO(top_remaining_img), width=350, height=250))
            group2.append(least_remaining_title)
            if least_remaining_img:
                group2.append(Image(BytesIO(least_remaining_img), width=350, height=250))
            if group2:
                story.append(KeepTogether(group2))
                story.append(Spacer(1, 12))
            # --- Additional Visualizations Section (Only Region Breakdown Chart) ---
            with open("tournament_bracket.json", 'r') as f:
                bracket_data = json.load(f)
            region_mapping = {}
            for region in bracket_data.get("regions", []):
                region_name = region["region_name"]
                teams = [team["team_name"] for team in region["teams"]]
                region_mapping[region_name] = teams
            region_status = {}
            for region, teams in region_mapping.items():
                counts = {"in": 0, "not_played": 0, "out": 0}
                for team in teams:
                    status = determine_team_status(team, current_round, round_games)
                    counts[status] += 1
                region_status[region] = counts
            regions = list(region_status.keys())
            in_counts = [region_status[r]["in"] for r in regions]
            not_played_counts = [region_status[r]["not_played"] for r in regions]
            out_counts = [region_status[r]["out"] for r in regions]
            fig_region = go.Figure(data=[
                go.Bar(name='In', x=regions, y=in_counts),
                go.Bar(name='Not Played Yet', x=regions, y=not_played_counts),
                go.Bar(name='Out', x=regions, y=out_counts)
            ])
            fig_region.update_layout(barmode='stack', title="", xaxis_title="Region", yaxis_title="Number of Teams")
            region_img = fig_to_image(fig_region)
            region_title = Paragraph('<para align="center"><b>Region Breakdown Chart</b></para>', styles['Heading2'])
            
            group_additional = []
            group_additional.append(region_title)
            if region_img:
                group_additional.append(Image(BytesIO(region_img), width=350, height=250))
            if group_additional:
                story.append(KeepTogether(group_additional))
                story.append(Spacer(1, 12))
            # No extra page break here.
        # Build the PDF and add page numbers.
        doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
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
    ROUND_ORDER = ["Round of 64", "Round of 32", "Sweet 16", "Elite 8", "Final Four", "Championship"]
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

def add_page_number(canvas, doc):
    """
    Draws the page number at the bottom center of each page.
    """
    page_num = canvas.getPageNumber()
    text = f"Page {page_num}"
    canvas.drawCentredString(LETTER[0] / 2.0, 20, text)
