"""
create_indexes.py
-----------------
Part B / D — create ESR-friendly indexes for browse + analytics queries.

How to run (venv active)
    python create_indexes.py

Indexes created
  shows
    { city: 1, movie_id: 1, start_time: 1 }   — browse shows (Equality, Sort/Range)
  bookings
    { status: 1, movie_id: 1 }                — revenue / top-movies $match+$group
    { status: 1, show_id: 1 }                 — join path / per-show booking lookups
    { status: 1, created_at: -1 }             — recent paid bookings list
    { user-facing } { mobile_number: 1, created_at: -1 }
  movies
    { cities: 1 }                             — filter movies by city
    { language: 1 }
    { genres: 1 }
"""

import os
import sys

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import ConnectionFailure

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "cinebook")


def main() -> None:
    if not MONGODB_URI or "{{" in MONGODB_URI:
        print("ERROR: Set MONGODB_URI in .env first")
        sys.exit(1)

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    try:
        client.admin.command("ping")
    except ConnectionFailure as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    db = client[MONGODB_DB]

    specs = [
        ("shows", [("city", ASCENDING), ("movie_id", ASCENDING), ("start_time", ASCENDING)], "idx_shows_city_movie_time"),
        ("bookings", [("status", ASCENDING), ("movie_id", ASCENDING)], "idx_bookings_status_movie"),
        ("bookings", [("status", ASCENDING), ("show_id", ASCENDING)], "idx_bookings_status_show"),
        ("bookings", [("status", ASCENDING), ("created_at", DESCENDING)], "idx_bookings_status_created"),
        ("bookings", [("mobile_number", ASCENDING), ("created_at", DESCENDING)], "idx_bookings_mobile_created"),
        ("movies", [("cities", ASCENDING)], "idx_movies_cities"),
        ("movies", [("language", ASCENDING)], "idx_movies_language"),
        ("movies", [("genres", ASCENDING)], "idx_movies_genres"),
    ]

    print(f"Creating indexes on '{MONGODB_DB}' ...\n")
    for coll, keys, name in specs:
        created = db[coll].create_index(keys, name=name)
        print(f"  {coll:10s}  {created}")

    print("\nDone. Verify in Compass → Indexes, or:")
    print("  db.shows.getIndexes()")
    print("  db.bookings.getIndexes()")


if __name__ == "__main__":
    main()
