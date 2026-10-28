"""
run_analytics.py
----------------
Run all Part D aggregation pipelines and print results (CLI helper).

    python run_analytics.py
"""

import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

from analytics_pipelines import (
    occupancy_per_show_pipeline,
    peak_time_slots_pipeline,
    revenue_per_movie_pipeline,
    top_movies_pipeline,
)

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "cinebook")


def json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(type(obj))


def main() -> None:
    if not MONGODB_URI or "{{" in MONGODB_URI:
        print("ERROR: Set MONGODB_URI in .env")
        sys.exit(1)

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    try:
        client.admin.command("ping")
    except ConnectionFailure as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    db = client[MONGODB_DB]

    jobs = [
        ("Revenue per movie", db["bookings"], revenue_per_movie_pipeline()),
        ("Occupancy per show", db["shows"], occupancy_per_show_pipeline()),
        ("Top movies", db["bookings"], top_movies_pipeline(5)),
        ("Peak time slots", db["bookings"], peak_time_slots_pipeline()),
    ]

    for title, coll, pipeline in jobs:
        print("=" * 60)
        print(title)
        print("=" * 60)
        rows = list(coll.aggregate(pipeline))
        print(json.dumps(rows, indent=2, default=json_default))
        print()


if __name__ == "__main__":
    main()
