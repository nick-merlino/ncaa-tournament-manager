"""
report.py

Generates a comprehensive PDF report for the NCAA Tournament application.
The report includes:
  - A current round overview with player picks and score breakdown.
  - Visuals including:
      * 10 Most Popular Teams Chart
      * 10 Least Popular Teams Chart
      * Player Points Line Chart
      * Upsets Table
      * Best Case Scenario Final Scores Table

Each visual is generated by its own function.
"""

import datetime
import json
from io import BytesIO

import pandas as pd
import plotly.graph_objects as go
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak,
    HRFlowable, KeepTogether, Table, TableStyle
)
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from sqlalchemy.orm import joinedload

from config import logger
from db import SessionLocal, User, UserPick, UserScore, TournamentResult
from scoring import get_round_game_status, calculate_best_case_scores, calculate_worst_case_scores
from constants import ROUND_ORDER, ROUND_WEIGHTS, FIRST_ROUND_PAIRINGS


def fig_to_image(fig):
    """
    Converts a Plotly figure to a PNG image in memory.
    """
    try:
        return fig.to_image(format="png")
    except Exception as e:
        logger.error(f"Error converting figure to image: {e}")
        return None


def add_page_number(canvas, doc):
    """
    Adds a page number to the PDF canvas at the bottom center.
    """
    page_num = canvas.getPageNumber()
    text = f"Page {page_num}"
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(LETTER[0] / 2, 20, text)


def determine_team_status(team, current_round, round_games):
    """
    Determines the status of a team for the current round.
    
    Returns:
      - 'out': if the team lost in any completed round.
      - 'not_played': if the team has not been eliminated and the current round is incomplete.
      
    Explanation:
      Even if a team won in previous rounds, if the current round (the first incomplete round)
      hasn't been played yet, the status is 'not_played' (matching the original behavior).
    """
    current_index = ROUND_ORDER.index(current_round)
    # Check rounds that are fully completed (all rounds before the current round)
    for i in range(current_index):
        rnd = ROUND_ORDER[i]
        for game in round_games.get(rnd, []):
            # If the team participated and did NOT win, it's eliminated.
            if game.get("winner") and game["winner"].strip() != team.strip() \
               and team.strip() in (game["team1"].strip(), game["team2"].strip()):
                return "out"
    # If not eliminated, then even if the team won previous rounds,
    # the current round is not yet played, so status should be "not_played".
    return "not_played"



def generate_user_overview(story, styles, df, user_points_df, sorted_users, visible_rounds, current_round):
    """
    Generates the current round overview with player picks and scores.
    """
   
    # Import and calculate the maximum theoretical score.
    from scoring import calculate_maximum_possible_score
    max_score = calculate_maximum_possible_score()
    
    story.append(Paragraph(f"Current Round in Progress: {current_round}", styles['Title']))
    story.append(Paragraph(
        '<para align="center"><font size="8" color="grey">Team key: seed(points)-Team Name</font></para>',
        styles['Normal']))
    story.append(Paragraph(
        f'<para align="center"><font size="8" color="grey">Maximum Theoretical Score: {max_score}</font></para>',
        styles['Normal']))
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
        header_line = f"{uname} - <b>Points:</b> {user_pts:.0f}"
        player_flowables = [Paragraph(header_line, styles['Heading3'])]
        
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
        
        still_in_list = [display for seed, display in sorted(still_in_picks, key=lambda x: x[0])]
        not_played_list = [display for seed, display in sorted(not_played_picks, key=lambda x: x[0])]
        out_list = [display for seed, display in sorted(out_picks, key=lambda x: x[0])]
        
        def format_category(category, items):
            return f"<b>{category} ({len(items)}):</b> " + ", ".join(items) if items else f"<b>{category}:</b> None"
        
        player_flowables.append(Paragraph(format_category("Won This Round", still_in_list), styles['Normal']))
        player_flowables.append(Paragraph(format_category("Not Played Yet", not_played_list), styles['Normal']))
        player_flowables.append(Paragraph(format_category("Out", out_list), styles['Normal']))
        
        story.append(KeepTogether(player_flowables))
        story.append(Spacer(1, 12))
        previous_points = user_pts


def generate_most_popular_chart(story, styles, df, visible_rounds):
    """
    Generates a bar chart for the 10 most popular teams still remaining,
    with x-axis labels in the format "(seed-number) Team Name".
    """
    session = SessionLocal()
    try:
        # Get all teams from the Round of 64.
        first_round_games = session.query(TournamentResult).filter(
            TournamentResult.round_name.like("Round of 64%")
        ).all()
        bracket_teams = {game.team1.strip() for game in first_round_games}.union(
                        {game.team2.strip() for game in first_round_games})
        # Determine remaining teams using elimination logic.
        remaining = set(bracket_teams)
        for round_name in ROUND_ORDER:
            if round_name in visible_rounds:
                games = visible_rounds[round_name]
                if all(g.get('winner') and g['winner'].strip() for g in games):
                    for g in games:
                        if g['winner'].strip() == g['team1'].strip():
                            loser = g['team2'].strip()
                        else:
                            loser = g['team1'].strip()
                        remaining.discard(loser)
                else:
                    break

        # Build a DataFrame with team names.
        teams_df = pd.DataFrame({'team_name': list(bracket_teams)})
        # Filter to teams still remaining.
        teams_df = teams_df[teams_df['team_name'].isin(remaining)]
        # Get pick counts per team.
        pick_counts = df.groupby('team_name')['username'].nunique().reset_index().rename(
            columns={'username': 'pick_count'}
        )
        teams_df = teams_df.merge(pick_counts, on='team_name', how='left')
        teams_df['pick_count'] = teams_df['pick_count'].fillna(0).astype(int)
        # Load team seeds from tournament_bracket.json.
        with open("tournament_bracket.json", "r") as f:
            bracket_info = json.load(f)
        team_seeds = {team["team_name"].strip(): team["seed"]
                      for region in bracket_info.get("regions", [])
                      for team in region.get("teams", [])}
        # Add x-axis label in the format "(seed) Team Name".
        teams_df["x_label"] = teams_df["team_name"].apply(lambda tn: f"({team_seeds.get(tn, 'N/A')}) {tn}")
        top_remaining = teams_df.sort_values(by=['pick_count', 'team_name'], ascending=[False, True]).head(10)
        fig_top = go.Figure(
            data=[go.Bar(x=top_remaining["x_label"], y=top_remaining['pick_count'])],
            layout=dict(template="plotly_white")
        )
        fig_top.update_layout(xaxis_title="Team", yaxis_title="Number of Picks", title="")
        top_img = fig_to_image(fig_top)
        top_title = Paragraph('<para align="center"><b>10 Most Popular Teams Still Remaining</b></para>', styles['Heading2'])
        group = [top_title]
        if top_img:
            group.append(Image(BytesIO(top_img), width=400, height=300))
        story.append(KeepTogether(group))
        story.append(Spacer(1, 12))
    except Exception as e:
        logger.error(f"Error generating most popular chart: {e}")
    finally:
        session.close()

def generate_least_popular_chart(story, styles, df, visible_rounds):
    """
    Generates a bar chart for the 10 least popular teams still remaining,
    with x-axis labels in the format "(seed-number) Team Name".
    """
    session = SessionLocal()
    try:
        first_round_games = session.query(TournamentResult).filter(
            TournamentResult.round_name.like("Round of 64%")
        ).all()
        bracket_teams = {game.team1.strip() for game in first_round_games}.union(
                        {game.team2.strip() for game in first_round_games})
        remaining = set(bracket_teams)
        for round_name in ROUND_ORDER:
            if round_name in visible_rounds:
                games = visible_rounds[round_name]
                if all(g.get('winner') and g['winner'].strip() for g in games):
                    for g in games:
                        if g['winner'].strip() == g['team1'].strip():
                            loser = g['team2'].strip()
                        else:
                            loser = g['team1'].strip()
                        remaining.discard(loser)
                else:
                    break

        teams_df = pd.DataFrame({'team_name': list(bracket_teams)})
        teams_df = teams_df[teams_df['team_name'].isin(remaining)]
        pick_counts = df.groupby('team_name')['username'].nunique().reset_index().rename(
            columns={'username': 'pick_count'}
        )
        teams_df = teams_df.merge(pick_counts, on='team_name', how='left')
        teams_df['pick_count'] = teams_df['pick_count'].fillna(0).astype(int)
        # Load team seeds from tournament_bracket.json.
        with open("tournament_bracket.json", "r") as f:
            bracket_info = json.load(f)
        team_seeds = {team["team_name"].strip(): team["seed"]
                      for region in bracket_info.get("regions", [])
                      for team in region.get("teams", [])}
        # Create x-axis label.
        teams_df["x_label"] = teams_df["team_name"].apply(lambda tn: f"({team_seeds.get(tn, 'N/A')}) {tn}")
        least_remaining = teams_df.sort_values(by=['pick_count', 'team_name'], ascending=[True, True]).head(10)
        fig_least = go.Figure(
            data=[go.Bar(x=least_remaining["x_label"], y=least_remaining['pick_count'])],
            layout=dict(template="plotly_white")
        )
        fig_least.update_layout(xaxis_title="Team", yaxis_title="Number of Picks", title="")
        least_img = fig_to_image(fig_least)
        least_title = Paragraph('<para align="center"><b>10 Least Popular Teams Still Remaining</b></para>', styles['Heading2'])
        group = [least_title]
        if least_img:
            group.append(Image(BytesIO(least_img), width=400, height=300))
        story.append(KeepTogether(group))
        story.append(Spacer(1, 12))
    except Exception as e:
        logger.error(f"Error generating least popular chart: {e}")
    finally:
        session.close()

def generate_player_points_chart(story, styles, user_points_df):
    """
    Generates a line chart showing player points.
    """
    try:
        if not user_points_df.empty:
            user_points_sorted = user_points_df.sort_values(by='points', ascending=False)
            x_vals = user_points_sorted['username'].tolist()
            fig_line = go.Figure(
                data=[go.Scatter(x=x_vals, y=user_points_sorted['points'], mode="lines+markers")],
                layout=dict(template="plotly_white")
            )
            fig_line.update_layout(
                xaxis_title="Player",
                yaxis_title="Points",
                title="",
                xaxis_tickangle=-45,
                width=800,
                margin=dict(l=40, r=40, t=40, b=150),
                xaxis=dict(tickfont=dict(size=10))
            )
            line_img = fig_to_image(fig_line)
            line_title = Paragraph('<para align="center"><b>Player Points</b></para>', styles['Heading2'])
            group = [line_title]
            if line_img:
                group.append(Image(BytesIO(line_img), width=500, height=300))
            story.append(KeepTogether(group))
            story.append(Spacer(1, 12))
    except Exception as e:
        logger.error(f"Error generating player points chart: {e}")


def generate_upsets_table(story, styles):
    """
    Generates a table of games with the biggest upsets (based on seed differential).
    """
    try:
        with open("tournament_bracket.json", 'r') as f:
            bracket_info = json.load(f)
        team_seeds = {}
        for region in bracket_info.get("regions", []):
            for team in region.get("teams", []):
                if "team_name" in team and team["team_name"]:
                    team_seeds[team["team_name"].strip()] = team.get("seed")
                else:
                    logger.warning(f"Missing team_name in region {region.get('region_name')}: {team}")
        upsets = []
        session = SessionLocal()
        try:
            decided = session.query(TournamentResult).filter(TournamentResult.winner.isnot(None)).all()
            for game in decided:
                if game.winner:
                    team1_seed = team_seeds.get(game.team1.strip(), 999)
                    team2_seed = team_seeds.get(game.team2.strip(), 999)
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
        finally:
            session.close()
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
        group = [upset_title]
        if upset_table:
            group.append(upset_table)
        story.append(KeepTogether(group))
        story.append(Spacer(1, 12))
    except Exception as e:
        logger.error(f"Error generating upsets table: {e}")


def generate_potential_score_table(story, styles, user_points_df, sorted_users):
    """
    Generates a table showing for each user:
    Rank, Player, Current Score, Guaranteed Points, Potential Points,
    Worst Case Score, and Best Case Score.
    
    Guaranteed Points are computed as (Worst Case Score - Current Score).
    Potential Points are computed as (Best Case Score - Current Score).
    
    The table is sorted by Best Case Score (descending), then Current Score, then name.
    """
    try:
        # Compute best-case and worst-case final scores.
        best_case_scores = calculate_best_case_scores()
        worst_case_scores = calculate_worst_case_scores()

        # Build a dictionary of current scores.
        current_scores = {}
        if not user_points_df.empty:
            for _, row in user_points_df.iterrows():
                current_scores[row['username']] = row['points']
        else:
            for uname in sorted_users:
                current_scores[uname] = 0

        # Calculate bonus components.
        potential_points = {
            uname: best_case_scores.get(uname, current_scores.get(uname, 0)) - current_scores.get(uname, 0)
            for uname in current_scores
        }
        guaranteed_points = {
            uname: worst_case_scores.get(uname, current_scores.get(uname, 0)) - current_scores.get(uname, 0)
            for uname in current_scores
        }

        # Sort players by Best Case Score (descending), then current score, then name.
        sorted_players = sorted(
            current_scores.keys(),
            key=lambda x: (
                -best_case_scores.get(x, 0),
                -current_scores.get(x, 0),
                -guaranteed_points.get(x, 0),
                -potential_points.get(x, 0),
                x
            )
        )

        # Create a ranked list (ties share the same rank).
        ranked_list = []
        prev_best = None
        current_rank = 0
        for idx, uname in enumerate(sorted_players, start=1):
            bc = best_case_scores.get(uname, current_scores.get(uname, 0))
            wc = worst_case_scores.get(uname, current_scores.get(uname, 0))
            curr = current_scores.get(uname, 0)
            pot = potential_points.get(uname, 0)
            guar = guaranteed_points.get(uname, 0)
            if bc != prev_best:
                current_rank = idx
            ranked_list.append((current_rank, uname, curr, guar, pot, wc, bc))
            prev_best = bc

        # Build table data.
        header = ['Rank', 'Player', 'Current Score', 'Guaranteed', 'Potential', 'Worst Case Score', 'Best Case Score']
        table_data = [header]
        for rank, uname, curr, guar, pot, wc, bc in ranked_list:
            table_data.append([str(rank), uname, f"{curr:.0f}", f"{guar:.0f}", f"{pot:.0f}", f"{wc:.0f}", f"{bc:.0f}"])
        table_data.append(header)

        # (Optional) Apply span commands similar to your other tables.
        span_commands = []
        row_idx = 1
        while row_idx < len(table_data):
            current_value = table_data[row_idx][0]
            start_idx = row_idx
            end_idx = row_idx
            while end_idx + 1 < len(table_data) and table_data[end_idx + 1][0] == current_value:
                end_idx += 1
            if end_idx > start_idx:
                span_commands.append(("SPAN", (0, start_idx), (0, end_idx)))
            row_idx = end_idx + 1

        # Set up table styling.
        potential_table = Table(table_data, hAlign='CENTER')
        base_style = [
            # All rows
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            # First row (header)
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            # Middle rows
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            # Last row (header)
            ('BACKGROUND', (0, -1), (-1, -1), colors.grey),
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('TOPPADDING', (0, -1), (-1, -1), 12),
        ]
        for cmd in span_commands:
            base_style.append(cmd)
        potential_table.setStyle(TableStyle(base_style))

        # Add title and subtitle.
        potential_title = Paragraph('<para align="center"><b>Potential Scenarios Final Scores</b></para>', styles['Heading2'])
        potential_subtitle_1 = Paragraph(
            '<para align="center"><font size="8" color="grey">'
            'Scores if future results are as bad as and as good as possible per person'
            '</font></para>',
            styles['Normal']
        )
        potential_subtitle_2 = Paragraph(
            '<para align="center"><font size="8" color="grey">'
            'This considers that your teams will sometimes play each other and that means both of your teams can\'t win sometimes'
            '</font></para>',
            styles['Normal']
        )
        group = [potential_title, potential_subtitle_1, potential_subtitle_2, Spacer(1, 12), potential_table]
        story.append(PageBreak())
        story.append(KeepTogether(group))
    except Exception as e:
        logger.error(f"Error generating potential scenario table: {e}")


def generate_report(pdf_filename=None):
    """
    Main function to generate the PDF report.
    It collects user data, builds a DataFrame of picks and scores,
    and then calls the individual visual-generating functions.
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
        # Fetch all users and their associated picks.
        all_users = session.query(User).options(joinedload(User.picks)).all()
        user_scores = {us.user_id: us for us in session.query(UserScore).all()}
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
        # Build the main DataFrame from the collected data.
        df = pd.DataFrame(data_rows)
        # Always define user_points_df.
        if not df.empty:
            # Replicate original grouping and sorting: one row per user with max score.
            user_points_df = (
                df[['username', 'points']]
                .drop_duplicates()
                .groupby('username')['points']
                .max().reset_index()
            )
            user_points_df = user_points_df.sort_values(by=['points', 'username'], ascending=[False, True])
            sorted_users = user_points_df['username'].tolist()
        else:
            # If no picks are found, create an empty DataFrame with the expected columns.
            user_points_df = pd.DataFrame(columns=['username', 'points'])
            sorted_users = sorted([u.full_name for u in all_users])
        
        current_round, visible_rounds = get_round_game_status()
        if not current_round:
            current_round = ROUND_ORDER[0]

        # Generate each section of the report.
        generate_user_overview(story, styles, df, user_points_df, sorted_users, visible_rounds, current_round)
        generate_most_popular_chart(story, styles, df, visible_rounds)
        generate_least_popular_chart(story, styles, df, visible_rounds)
        generate_player_points_chart(story, styles, user_points_df)
        generate_upsets_table(story, styles)
        generate_potential_score_table(story, styles, user_points_df, sorted_users)

        doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
        logger.info(f"PDF report saved as {pdf_filename}")
    except Exception as e:
        logger.error(f"Error generating PDF report: {e}")
    finally:
        session.close()
