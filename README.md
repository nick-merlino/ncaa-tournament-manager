# Create virtual environment
python -m venv venv

# Activate venv
source venv/bin/activate

# Install requirements
pip install --upgrade pip
pip install -r requirements.txt

# Run the script
python main.py


TODO

Change "Total Points by User" to "Player Points". Instead of listing every player's points as a bar chart, use a modern line chart that displays all user scores in a similar way. 

Make it so in the PDF report the following are bold
Points:
Still In:
Out:
Not Played Yet:

Don't say "User: Name" but just say "Name"

Make sure that a player's data is on the same page, and not broken up on multiple pages

Put graphs on the same page when possible to reduce the amount of space used

Also, add a separator between player groups according to score. For example if 3 players are tied for first they'd be in the first group. Then there would be a horizontal line, followed by the next group who is in second place

Also add these under the current graphs
- 10 most popular (according to google sheet picks) teams still remaining
- 10 least popular (according to google sheet picks) teams still remaining
- games with biggest upsets (seed differential)

Give me complete files for any changed files

Also make the web interface where I select the winners per game have the games listed in the right order. For example round 1 from top to bottom is 1vs16, 8vs9, 5vs12, 4vs13, 6vs11, 3vs14, 7vs10, 2vs15. Round 2 is the winner of the first two matchups then follow that trend for the rest of that round and the future rounds

Also make sure to save off the values I enter in the matchup results web interface. I want those results to persist across multiple runs.

When I entered in all of the round 1 matchup results I expected it to show the round 2 matchups for me to complete, but nothing changed. Fix that. Also the PDF report after I entered all of the round 1 results says there is no round in progress. It should know that it's now round 2 (since round 1 is fully filled out) and figure out which matchups are happening based on the tournament bracket and results. Therefore, it should say it's on the second round and it should calculate the fields such as "not played yet", "still in", "still out", etc accordingly. Also it should update the graphs to reflect that as well.