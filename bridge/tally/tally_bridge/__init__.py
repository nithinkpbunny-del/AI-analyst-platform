"""Tally bridge: a small helper that runs on the customer's Tally PC (BUILD_SPEC 8.8, M16).

It reads from TallyPrime's local XML server with **export requests only** and pushes the rows to the platform over
HTTPS. It never sends an import, alter, create or delete request to Tally, and it accepts no commands from the
platform except "please read again now". See docs/SETUP_TALLY.md.
"""

__version__ = "1.0.0"
PROTOCOL_VERSION = 1
