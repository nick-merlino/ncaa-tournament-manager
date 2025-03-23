"""
constants.py

This module contains shared constants used across the NCAA Tournament Picks application.
These constants include the tournament round order and the first round pairings.
"""

# Define the order of tournament rounds.
ROUND_ORDER = [
    "Round of 64",
    "Round of 32",
    "Sweet 16",
    "Elite 8",
    "Final Four",
    "Championship"
]

# Define the pairings for the first round.
# Each tuple represents a matchup: (lower seed, higher seed)
FIRST_ROUND_PAIRINGS = [
    (1, 16),
    (8, 9),
    (5, 12),
    (4, 13),
    (6, 11),
    (3, 14),
    (7, 10),
    (2, 15)
]

# Round weights configuration
ROUND_WEIGHTS = {
    "Round of 64": 1,
    "Round of 32": 1,
    "Sweet 16": 1,
    "Elite 8": 1,
    "Final Four": 1,
    "Championship": 1
}
