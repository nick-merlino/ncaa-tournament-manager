# config.py

import os
import logging
import sys

# ------------------------------------------------------------------------
# Environment / Configuration
# ------------------------------------------------------------------------

# OAuth2 Scopes for reading Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

# Path to your Google OAuth2 client secrets
GOOGLE_CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

# ID of the Google Sheet to read picks (participants, seeds, etc.)
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "YOUR_SPREADSHEET_ID")
# Range that includes: timestamp, participant name, email, seed1..seedN
RANGE_NAME = os.environ.get("RANGE_NAME", "Sheet1!A1:Z")

# Database URL (SQLite by default)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///ncaa_picks.db")

# ------------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("NCAA-Picks")
