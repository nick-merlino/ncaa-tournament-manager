# NCAA Tournament Bracket and Picks Application

This project is an NCAA Tournament bracket and picks application that allows users to select winners in a bracket through a web interface, track user picks and calculate scores based on tournament results, and generate a comprehensive PDF report summarizing user performance and tournament progress.

## History
This idea was born at MITRE in the mid 90s by Andy Merlino, Steve Janiak, David Palmer, Stanley Boykin and Daryl Morey.  The concept was to create a NCAA basketball tournament game that would follow the 64 games, keep everyone engaged and make it as easy and even for the non-sports fan to participate.  Thus, the idea of having everyone select just one seed at each level, one number 1, one number 2 and all the way to one number 16 and let the tournament committee do all the hard work.  In the begining, this was tracked on a whiteboard in a conference room in K building.  With more people interested in joining, Andy Merlino created a MS Access database to accept the tournament initial pairings, allow for manual participant entries and generate the results.  With the number of participants growing and errononeous manual entries, we started to accept google forms tournament entries to reduce typo entry errors.  In 2025, Nick Merlino took the whole concept, used AI utilties to create a base solution in Python.  He then continued to tweak the reports with more visuals and add new data entry features to make it easy for the administrator.

## Project Capabilities

- **Web Interface:** Users can select winners for each game. The interface automatically saves selections, updates matchups for subsequent rounds, and ensures proper game ordering.
- **Persistent Data:** Tournament results are stored in a SQLite database to maintain consistency across sessions.
- **Scoring & Rankings:** User picks are scored based on correct selections across tournament rounds, with scores updated automatically.
- **PDF Report Generation:** Generates a detailed report including:
  - A current round overview with player picks and scores grouped by performance.
  - A modern line chart displaying player points.
  - Bar charts for the 10 most popular and 10 least popular teams still remaining.
  - A table of games with the biggest upsets (by seed differential).
  - A table displaying the potential for each player to earn more points in their best-case future.
- **Google Sheets Integration:** Enables importing user picks directly from a Google Sheet.

## File Structure

- **index.html:** Web template for displaying tournament matchups.
- **tournament_bracket.json:** JSON file containing the initial tournament bracket (teams and seeds).  
**Important:** The order of the regions in the "regions" array is used to determine the matchups in the final rounds. The first two regions (e.g., South and East) are paired for Final Four Game 1, and the last two regions (e.g., West and Midwest) are paired for Final Four Game 2. Each region must contain exactly 16 teams with correct seed values for the tournament progression to work properly.
- **scoring.py:** Module for calculating user scores and determining current round status.
- **report.py:** Generates detailed PDF reports with player scores and visualizations.
- **main.py:** Main Flask application handling web routes, game updates, and round progression.
- **google_integration.py:** Manages OAuth2 authentication and data retrieval from Google Sheets.
- **db.py:** Defines database models and initialization logic using SQLAlchemy.
- **config.py:** Contains configuration settings (logging, database URL, Google API credentials, etc.).
- **requirements.txt:** Lists the required Python dependencies.
- **constants.py:** Shared constants such as round order and pairing information.

## Installation and Run Instructions

1. **Clone the Repository:**

   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```

2. **Create a Virtual Environment:**

   ```bash
   python -m venv venv
   ```

3. **Activate the Virtual Environment:**

   - On Linux/Mac:
     ```bash
     source venv/bin/activate
     ```

   - On Windows:
     ```bash
     venv\Scripts\activate
     ```

4. **Install Dependencies:**

   ```bash
   pip install --upgrade pip && pip install -r requirements.txt
   ```

5. **Configure the Application:**

   - Update `config.py` as needed for your environment.
   - Ensure `tournament_bracket.json` contains the correct bracket data.
   - Provide valid Google API credentials in `credentials.json` for Google Sheets integration.

6. **Initialize the Database and Import Data:**

   The database is automatically initialized on the first run, importing the bracket and Google Sheets picks if available.

7. **Run the Application:**

   ```bash
   python main.py
   ```

8. **Access the Web Interface:**

   Open your browser and navigate to [http://127.0.0.1:5000](http://127.0.0.1:5000) to view the tournament bracket and make selections.

## Additional Notes

- **Persistent Data:** Tournament results are stored in a SQLite database (`ncaa_picks.db`) and persist across sessions.
- **Round Progression:** After each round, new matchups are automatically generated.
- **PDF Report:** Use the "Generate PDF Report" button on the web interface to download a comprehensive report.