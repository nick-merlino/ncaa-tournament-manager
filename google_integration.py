"""
google_integration.py

Handles Google Sheets integration for the NCAA Picks application.
Responsible for authenticating with Google using OAuth2,
fetching picks data from a specified Google Sheets document,
and updating the local database with those picks.
"""

import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import SCOPES, GOOGLE_CREDENTIALS_FILE, TOKEN_FILE, SPREADSHEET_ID, RANGE_NAME, logger
from db import SessionLocal, User, UserPick

class GoogleSheetsError(Exception):
    """Custom exception for errors during Google Sheets integration."""
    pass

def google_sheets_authenticate():
    """
    Authenticates with Google Sheets using OAuth2.

    Checks for existing credentials in TOKEN_FILE; if not available or invalid,
    initiates the OAuth flow.

    Returns:
        service: Authenticated Google Sheets service instance.

    Raises:
        GoogleSheetsError: If credentials are missing or authentication fails.
    """
    # Ensure the credentials file exists and appears valid.
    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        message = f"\n[ERROR] '{GOOGLE_CREDENTIALS_FILE}' not found. Download valid credentials.json from Google Cloud Console."
        logger.error(message)
        raise GoogleSheetsError(message)
    if os.path.getsize(GOOGLE_CREDENTIALS_FILE) < 50:
        message = f"\n[ERROR] '{GOOGLE_CREDENTIALS_FILE}' is empty or invalid."
        logger.error(message)
        raise GoogleSheetsError(message)

    creds = None
    try:
        # Attempt to load credentials from the token file if available.
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        # If credentials are missing or invalid, refresh or initiate OAuth flow.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
                creds = flow.run_console()
            # Save the newly obtained credentials for future use.
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
    except Exception as e:
        message = f"Error during OAuth2 flow: {e}"
        logger.error(message)
        raise GoogleSheetsError(message)

    try:
        # Build and return the authenticated Google Sheets service.
        service = build('sheets', 'v4', credentials=creds, cache_discovery=False)
        return service
    except Exception as e:
        message = f"Error building Google Sheets service: {e}"
        logger.error(message)
        raise GoogleSheetsError(message)

def fetch_picks_from_sheets():
    """
    Fetches user picks data from the configured Google Sheets document.

    Expects the sheet to have a header row with at least 4 columns:
    [timestamp, participant_full_name, email, seed1, ...].

    Returns:
        picks_data (list of dict): Each dict contains:
            - full_name: Participant's full name.
            - seed_label: Label for the seed (e.g., "Seed 1").
            - team_name: The team selected by the participant.

    Raises:
        GoogleSheetsError: If fetching data fails or the sheet format is unexpected.
    """
    service = google_sheets_authenticate()
    try:
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
        values = result.get('values', [])
    except Exception as e:
        message = f"Error fetching data from Google Sheets (ID={SPREADSHEET_ID}, Range={RANGE_NAME}): {e}"
        logger.error(message)
        raise GoogleSheetsError(message)
    
    if not values or len(values) < 2:
        message = f"Google Sheet appears empty or missing data (ID: {SPREADSHEET_ID}, Range: {RANGE_NAME})."
        logger.error(message)
        raise GoogleSheetsError(message)
    
    header = values[0]
    if len(header) < 4:
        message = "Google Sheet header format unexpected. Expected at least: [timestamp, participant_full_name, email, seed1, ...]"
        logger.error(message)
        raise GoogleSheetsError(message)
    
    picks_data = []
    # Process each row after the header.
    for idx, row in enumerate(values[1:], start=2):
        if len(row) < 4:
            logger.warning(f"Skipping row {idx}: not enough columns.")
            continue
        full_name = row[1].strip()
        if not full_name:
            logger.warning(f"Skipping row {idx}: participant name is blank.")
            continue
        seeds = row[3:]
        for i, team_name in enumerate(seeds, start=1):
            if not team_name:
                continue
            picks_data.append({
                "full_name": full_name,
                "seed_label": f"Seed {i}",
                "team_name": team_name.strip()
            })
    if not picks_data:
        message = "No valid picks extracted from the Google Sheet."
        logger.error(message)
        raise GoogleSheetsError(message)
    
    logger.info(f"Fetched {len(picks_data)} picks from the sheet.")
    return picks_data

def update_local_db_with_picks(picks_data):
    """
    Updates the local database with user picks fetched from Google Sheets.

    For each record:
      - If the user does not exist, creates a new user.
      - Updates or creates the corresponding UserPick record.

    Args:
        picks_data (list of dict): User picks data from Google Sheets.
    """
    session = SessionLocal()
    try:
        for record in picks_data:
            full_name = record['full_name']
            seed_label = record['seed_label']
            team_name = record['team_name']
            # Check if the user exists; create if not.
            user = session.query(User).filter_by(full_name=full_name).first()
            if not user:
                user = User(full_name=full_name)
                session.add(user)
                session.commit()  # Commit to generate user_id.
            # Update the pick if it exists; otherwise, create a new pick.
            pick = session.query(UserPick).filter_by(user_id=user.user_id, seed_label=seed_label).first()
            if pick:
                pick.team_name = team_name
            else:
                new_pick = UserPick(user_id=user.user_id, seed_label=seed_label, team_name=team_name)
                session.add(new_pick)
        session.commit()
    except Exception as e:
        logger.error(f"Error updating local database with picks: {e}")
        session.rollback()
    finally:
        session.close()
