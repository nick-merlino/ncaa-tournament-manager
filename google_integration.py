# google_integration.py

import os
import sys
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import SCOPES, GOOGLE_CREDENTIALS_FILE, TOKEN_FILE, SPREADSHEET_ID, RANGE_NAME, logger
from db import SessionLocal, User, UserPick

class GoogleSheetsError(Exception):
    """Custom exception for Google Sheets errors."""
    pass

def google_sheets_authenticate():
    """
    Handle OAuth2 flow and return a Google Sheets service object.
    If credentials.json is missing or invalid, we print instructions and exit.
    No 'skip' prompt – we don't open the bracket until Google linking is fixed.
    """
    # 1) Check credentials.json
    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        print(
            "\n[ERROR] Google OAuth2 credentials file (credentials.json) is missing.\n"
            "To fix:\n"
            "  1) Go to https://console.cloud.google.com/\n"
            "  2) Create or select a project, enable the 'Google Sheets API'\n"
            "  3) Under 'APIs & Services' -> 'Credentials', create an 'OAuth client ID'\n"
            "  4) Download the JSON and rename it to 'credentials.json'\n"
            "  5) Place it in this project folder.\n"
        )
        sys.exit(1)

    if os.path.getsize(GOOGLE_CREDENTIALS_FILE) < 50:  # Arbitrary check for a minimal file
        print(
            "\n[ERROR] 'credentials.json' is present but appears empty/invalid.\n"
            "Ensure it's the correct JSON from Google Cloud Console.\n"
        )
        sys.exit(1)

    creds = None
    try:
        # 2) Attempt to load existing token or run the OAuth flow
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
                print("Opening browser for Google OAuth2 login...\n")
                creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
    except Exception as e:
        message = f"Error during Google OAuth2 flow: {e}"
        logger.error(message)
        print("\n[ERROR] Could not complete OAuth flow.\n"
              "Check that you're signed in to Google with the correct account,\n"
              "and that your credentials.json is valid for the Sheets API.\n")
        sys.exit(1)

    # 3) Build the Sheets service
    try:
        service = build('sheets', 'v4', credentials=creds)
        return service
    except Exception as e:
        message = f"Error building Google Sheets service: {e}"
        logger.error(message)
        print("\n[ERROR] Could not build the Sheets service with provided credentials.\n")
        sys.exit(1)

def fetch_picks_from_sheets():
    """
    Fetch participants + picks from the Google Sheet. 
    If the sheet is empty/malformed, raise GoogleSheetsError.
    """
    service = google_sheets_authenticate()
    try:
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
        values = result.get('values', [])
    except Exception as e:
        message = (
            f"Error fetching data from Google Sheets (ID={SPREADSHEET_ID}, Range={RANGE_NAME}): {e}\n"
            "Check if the Spreadsheet ID is correct and if you have read access."
        )
        logger.error(message)
        raise GoogleSheetsError(message)

    if not values or len(values) < 2:
        # We expect at least a header row plus some data
        message = (
            "Google Sheet appears empty or missing expected data.\n"
            f"Sheet ID: {SPREADSHEET_ID}, Range: {RANGE_NAME}"
        )
        logger.error(message)
        raise GoogleSheetsError(message)

    header = values[0]
    if len(header) < 4:
        message = (
            "Google Sheet header format unexpected. Must have at least:\n"
            "[timestamp, participant_name, email, seed1, seed2, ...]"
        )
        logger.error(message)
        raise GoogleSheetsError(message)

    picks_data = []
    for idx, row in enumerate(values[1:], start=2):
        if len(row) < 4:
            logger.warning(f"Skipping row {idx}: not enough columns (found {len(row)}).")
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
        message = "No valid picks extracted from the Google Sheet. Possibly all seeds are empty."
        logger.error(message)
        raise GoogleSheetsError(message)

    logger.info(f"Fetched {len(picks_data)} picks from the sheet.")
    return picks_data

def update_local_db_with_picks(picks_data):
    """
    Insert or update user picks in the local DB.
    """
    session = SessionLocal()
    try:
        for record in picks_data:
            full_name = record['full_name']
            seed_label = record['seed_label']
            team_name = record['team_name']

            user = session.query(User).filter_by(full_name=full_name).first()
            if not user:
                user = User(full_name=full_name)
                session.add(user)
                session.commit()

            pick_exists = session.query(UserPick).filter_by(
                user_id=user.user_id,
                seed_label=seed_label
            ).first()

            if pick_exists:
                pick_exists.team_name = team_name
            else:
                new_pick = UserPick(
                    user_id=user.user_id,
                    seed_label=seed_label,
                    team_name=team_name
                )
                session.add(new_pick)
        session.commit()
        logger.info("Local DB updated with picks from Google Sheets.")
    except Exception as e:
        logger.error(f"Error updating DB with picks: {e}")
        session.rollback()
    finally:
        session.close()
