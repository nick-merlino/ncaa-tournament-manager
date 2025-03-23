"""
config.py

Configuration settings for the NCAA Tournament Picks Application.

This file defines:
  - OAuth2 scopes and paths for Google API credentials.
  - Spreadsheet details for importing user picks.
  - The database connection URL.
  - Logging configuration for the application.
"""

import os
import logging
import sys

# ------------------------------------------------------------------------
# Google Sheets OAuth2 Configuration
# ------------------------------------------------------------------------
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

# Paths to Google OAuth2 credentials and token files.
GOOGLE_CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

# ------------------------------------------------------------------------
# Google Sheets Data Configuration
# ------------------------------------------------------------------------
# ID of the Google Sheet and the data range to retrieve picks.
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1NFsleeR7kMSQHwmOhAjxSh9zvUhlbZDOwRQ81r3yc50")
RANGE_NAME = os.environ.get("RANGE_NAME", "Form Responses 1!A1:Z")

# ------------------------------------------------------------------------
# Database Configuration
# ------------------------------------------------------------------------
# SQLite is used by default; can be overridden by setting the DATABASE_URL environment variable.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///ncaa_picks.db")

# ------------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------------
# Configure logging to output messages to stdout.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("NCAA-Picks")
