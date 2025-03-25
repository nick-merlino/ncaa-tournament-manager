"""
constants.py

Shared constants for the NCAA Tournament Picks application.

This module defines:
  - The sequential order of tournament rounds.
  - The pairings for first round matchups.
  - The scoring weights for each round.
"""

# Define the tournament rounds in their sequential order.
ROUND_ORDER = [
    "Round of 64",
    "Round of 32",
    "Sweet 16",
    "Elite 8",
    "Final Four",
    "Championship"
]

# Define the pairings for the first round matchups.
# Each tuple represents the matchup seeds: (lower seed, higher seed)
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

# Define the scoring weight assigned to each round.
ROUND_WEIGHTS = {
    "Round of 64": 1,
    "Round of 32": 1,
    "Sweet 16": 1,
    "Elite 8": 1,
    "Final Four": 1,
    "Championship": 1
}
