# NCAA Tournament Bracket and Picks Application

This project is an NCAA Tournament bracket and picks application that allows users to select winners in a bracket through a web interface, tracks user picks and calculates scores based on tournament results, and generates a PDF report summarizing user performance and tournament progress.

## History
This idea was born at MITRE in the mid 90s by Andy Merlino, Steve Janiak, David Palmer, Stanley Boykin and Daryl Morey.  The concept was to create a NCAA basketball tournament game that would follow the 64 games, keep everyone engaged and make it as easy and even for the non-sports fan to participate.  Thus, the idea of having everyone select just one seed at each level, one number 1, one number 2 and all the way to one number 16 and let the tournament committee do all the hard work.  In the begining, this was tracked on a whiteboard in a conference room in K building.  With more people interested in joining, Andy Merlino created a MS Access database to accept the tournament initial pairings, allow for manual participant entries and generate the results.  With the number of participants growing and errononeous manual entries, we started to accept google forms tournament entries to reduce typo entry errors.  In 2025, Nick Merlino took the whole concept, used AI utilties to create a base solution in Python.  He then continued to tweak the reports with more visuals and add new data entry features to make it easy for the administrator.

## Project Capabilities

- Web Interface for Matchup Results: Users can select winners for each game in the bracket. The application automatically saves selections, updates the matchups for subsequent rounds, and ensures the correct game ordering.
- Persistent Tournament Data: Tournament results are stored in a SQLite database so that user selections persist across multiple runs.
- Scoring and Rankings: User picks are scored based on correct selections across various tournament rounds. Score calculations are updated automatically.
- PDF Report Generation: A detailed PDF report is generated, which includes:
 - A current round overview with player picks and scores grouped by score levels.
 - A modern line chart showing player points.
 - Bar charts for the 10 most and 10 least popular teams still remaining.
 - A table of games with the biggest upsets (by seed differential).
 - A region breakdown chart.
- Google Sheets Integration: User picks can be imported from a Google Sheet.

## File Structure

- index.html: Web interface template for displaying tournament matchups.
- tournament_bracket.json: JSON file containing the initial tournament bracket (teams and seeds).
- scoring.py: Contains logic to calculate user scores and determine current round status.
- report.py: Generates a detailed PDF report with player scores and visualizations.
- main.py: Main Flask application that handles web routes, game updates, and round progression.
- google_integration.py: Manages OAuth2 authentication and data retrieval from Google Sheets.
- db.py: Database models and initialization logic using SQLAlchemy.
- config.py: Configuration settings for the application (logging, database URL, Google API credentials, etc.).
- requirements.txt: Lists the Python dependencies required for the project.
- constants.py: Shared constants such as round order and pairing information for reusability across modules.

## Installation and Run Instructions

1. Clone the Repository:

```bash
git clone <repository-url>  cd <repository-directory>
```

2. Create a Virtual Environment:

```bash
python -m venv venv
```

3. Activate the Virtual Environment:

 - On Linux/Mac:
```bash
source venv/bin/activate
```
 - On Windows:
```bash
venv\Scripts\activate
```

4. Install Dependencies:
```bash
 pip install --upgrade pip && pip install -r requirements.txt
 ```

5. Configure the Application:

 - Update config.py if necessary with your specific configurations.
 - Ensure tournament_bracket.json contains the correct tournament bracket data.
 - Provide valid Google API credentials in credentials.json for Google Sheets integration.

6. Initialize the Database and Import Data:

 The database will be automatically initialized on first run, importing the tournament bracket and Google Sheets picks if available.

7. Run the Application:
```bash
python main.py 
```

8. Access the Web Interface:

 Open your browser and navigate to http://127.0.0.1:5000 to view the tournament bracket and make selections.

## Additional Notes

- Persistent Data: Tournament matchup results are saved in a SQLite database (ncaa_picks.db) and persist between runs.
- Round Progression: After completing a round's matchups, the application automatically generates the next round's games.
- PDF Report: Use the "Generate PDF Report" button on the web interface to download a comprehensive report of tournament progress and user scores.

## License

This project is provided for educational and demonstration purposes.
