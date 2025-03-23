# config.py

import os
import logging
import sys

# ------------------------------------------------------------------------
# Environment / Configuration
# ------------------------------------------------------------------------

# OAuth2 Scopes for reading Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

# Path to your Google OAuth2 client secrets and token
GOOGLE_CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

# ID and range for the Google Sheet
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1NFsleeR7kMSQHwmOhAjxSh9zvUhlbZDOwRQ81r3yc50")
RANGE_NAME = os.environ.get("RANGE_NAME", "Form Responses 1!A1:Z")

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
