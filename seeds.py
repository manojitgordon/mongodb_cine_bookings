"""
seeds.py
--------
Part B helper: load sample CineBook documents into MongoDB Atlas.

What this script does
1. Reads MONGODB_URI / MONGODB_DB from a .env file (copy from example.env).
2. Connects to Atlas with PyMongo.
3. Clears (optional) and inserts seed data for:
   - movies
   - theaters  (screens + seat layout embedded)
   - shows     (each show embeds a full seats[] array)
4. Prints a short summary so you can verify the load in Compass.

How to run (from the assignments folder, with venv active)
    python seeds.py

Notes
- start_time is stored as a real datetime (BSON Date), not a string.
- Field names match the assignment so APIs / CDC line up later.
- No separate users collection: a booking only needs a mobile_number
  stored on the booking document itself (created later by POST /bookings).
- Bookings are NOT seeded here; they are created by the booking API later.
"""

# ------------------------------------------------------------
# Standard-library imports
# ------------------------------------------------------------
import os                          # read environment variables
import sys                         # exit with a clear error code if setup is wrong
from datetime import datetime, timezone  # build real Date values for show start times
from itertools import product      # generate seat labels like A1, A2, ... E10
from typing import Any             # type hints for nested document dicts

# ------------------------------------------------------------
# Third-party imports
# ------------------------------------------------------------
from dotenv import load_dotenv     # loads key=value pairs from a .env file
from pymongo import MongoClient    # sync MongoDB driver (simple for one-off scripts)
from pymongo.errors import ConnectionFailure, OperationFailure


# ============================================================
# 1) CONFIGURATION
# ============================================================

# load_dotenv() looks for a file named ".env" in the current working directory.
# Copy example.env -> .env and fill in your Atlas credentials first.
load_dotenv()

# Atlas SRV connection string, e.g. mongodb+srv://user:pass@cluster.mongodb.net
MONGODB_URI = os.getenv("MONGODB_URI")

# Database name (assignment default: cinebook)
MONGODB_DB = os.getenv("MONGODB_DB", "cinebook")

# If True, drop existing documents in these collections before inserting.
# Safe for a learning / assignment cluster; turn off if you want to keep data.
RESET_COLLECTIONS = True


# ============================================================
# 2) HELPERS - seat layout generation
# ============================================================

def build_seats_for_screen(rows: list[str], seats_per_row: int, categories: dict[str, str]) -> list[dict[str, Any]]:
    """
    Build the seats[] array embedded on a show document.

    Why seats live on the show (not the theater):
    - Availability is per screening, not per theater forever.
    - Putting all seats in ONE show document lets MongoDB update them
      atomically later (race-free booking in Part C).

    Args:
        rows:          e.g. ["A", "B", "C", "D", "E"]
        seats_per_row: e.g. 10  -> seats A1..A10, B1..B10, ...
        categories:    row-range -> category map, e.g. {"A-B": "PREMIUM", "C-E": "REGULAR"}

    Returns:
        List of seat docs: {"seat": "A1", "category": "PREMIUM", "status": "AVAILABLE"}
    """
    # Expand "A-B" -> {"A": "PREMIUM", "B": "PREMIUM", ...}
    row_to_category: dict[str, str] = {}
    for row_range, category in categories.items():
        # row_range is like "A-B" or "C-E"
        start_row, end_row = row_range.split("-")
        # Walk every row letter from start through end (inclusive)
        for code in range(ord(start_row), ord(end_row) + 1):
            row_to_category[chr(code)] = category

    seats: list[dict[str, Any]] = []
    # product(rows, 1..N) -> (A,1), (A,2), ... (E,10)
    for row, number in product(rows, range(1, seats_per_row + 1)):
        seat_label = f"{row}{number}"          # e.g. "A1"
        category = row_to_category.get(row, "REGULAR")
        seats.append(
            {
                "seat": seat_label,
                "category": category,
                # AVAILABLE → HELD (on payout) → BOOKED (on payment) | back to AVAILABLE if hold expires
                "status": "AVAILABLE",
            }
        )
    return seats


# ============================================================
# 3) SEED DOCUMENTS (assignment field names)
# ============================================================

# --- movies (referenced by shows via movie_id) ---
# Natural _id like MOV-DUNE2 is easy to read in Compass and in API URLs.
MOVIES: list[dict[str, Any]] = [
    {
        "_id": "MOV-DUNE2",
        "title": "Dune: Part Two",
        "language": "English",
        "genres": ["Sci-Fi", "Adventure"],
        "duration_min": 166,
        "certification": "UA",
        "release_date": "2024-03-01",
        "cities": ["Delhi", "Mumbai", "Bengaluru"],
    },
    {
        "_id": "MOV-OPPEN",
        "title": "Oppenheimer",
        "language": "English",
        "genres": ["Drama", "History"],
        "duration_min": 180,
        "certification": "UA",
        "release_date": "2023-07-21",
        "cities": ["Delhi", "Mumbai"],
    },
    {
        "_id": "MOV-RRR",
        "title": "RRR",
        "language": "Telugu",
        "genres": ["Action", "Drama"],
        "duration_min": 182,
        "certification": "UA",
        "release_date": "2022-03-25",
        "cities": ["Bengaluru", "Mumbai", "Delhi"],
    },
    {
        "_id": "MOV-JAWAN",
        "title": "Jawan",
        "language": "Hindi",
        "genres": ["Action", "Thriller"],
        "duration_min": 169,
        "certification": "UA",
        "release_date": "2023-09-07",
        "cities": ["Delhi", "Mumbai", "Bengaluru"],
    },
]

# --- theaters (screens + layout EMBEDDED) ---
# Screens rarely change independently of the theater, so embedding is a good fit.
THEATERS: list[dict[str, Any]] = [
    {
        "_id": "THR-DEL-01",
        "name": "Downtown Cineplex",
        "city": "Delhi",
        "screens": [
            {
                "screen_id": "SCR-1",
                "name": "Audi 1",
                "rows": ["A", "B", "C", "D", "E"],
                "seats_per_row": 10,
                "categories": {"A-B": "PREMIUM", "C-E": "REGULAR"},
            },
            {
                "screen_id": "SCR-2",
                "name": "Audi 2",
                "rows": ["A", "B", "C", "D"],
                "seats_per_row": 8,
                "categories": {"A-B": "PREMIUM", "C-D": "REGULAR"},
            },
        ],
    },
    {
        "_id": "THR-MUM-01",
        "name": "Marine Drive Multiplex",
        "city": "Mumbai",
        "screens": [
            {
                "screen_id": "SCR-1",
                "name": "Screen 1",
                "rows": ["A", "B", "C", "D", "E"],
                "seats_per_row": 10,
                "categories": {"A-B": "PREMIUM", "C-E": "REGULAR"},
            }
        ],
    },
    {
        "_id": "THR-BLR-01",
        "name": "Indiranagar Screens",
        "city": "Bengaluru",
        "screens": [
            {
                "screen_id": "SCR-1",
                "name": "Audi A",
                "rows": ["A", "B", "C", "D", "E"],
                "seats_per_row": 10,
                "categories": {"A-B": "PREMIUM", "C-E": "REGULAR"},
            }
        ],
    },
]

# --- bookings (NOT seeded here; shape documented for later API work) ---
# There is NO users collection. Identity for a booking is just mobile_number.
# Example booking document the API will create later:
#
# {
#   "_id": "BKG-8f2c1a",
#   "show_id": "SHW-000512",
#   "mobile_number": "9876543210",   # required; only customer field we store
#   "seats": ["A1", "A5"],
#   "amount": 900,
#   "status": "BOOKED",               # current = most recent status_history entry
#   "status_history": [               # max 2 snapshots
#     {"status": "AWAITING_PAYMENT", "at": <BSON Date>},  # seats HELD
#     {"status": "BOOKED", "at": <BSON Date>}             # or EXPIRED / then CANCELLED
#   ],
#   "payment_status": "PAID",         # PENDING while awaiting, UNPAID if expired, REFUNDED if cancelled
#   "created_at": <BSON Date>
# }


def build_shows() -> list[dict[str, Any]]:
    """
    Build ~12 show documents across cities.

    Each show:
    - references movie_id + theater_id + screen_id
    - denormalizes city (helps browse-by-city without joining theaters)
    - embeds seats[] copied from that screen's layout
    - stores start_time as datetime (UTC) so range queries / sorts work
    """
    # Look up theater by _id so we can read its screen layout
    theater_by_id = {t["_id"]: t for t in THEATERS}

    # Declarative list of shows: (show_id, movie_id, theater_id, screen_id, start_time_iso, prices)
    # Prices are per category; booking amount = sum of selected seat categories.
    show_specs = [
        ("SHW-000501", "MOV-DUNE2", "THR-DEL-01", "SCR-1", "2026-07-20T18:30:00Z", {"PREMIUM": 450, "REGULAR": 300}),
        ("SHW-000502", "MOV-DUNE2", "THR-DEL-01", "SCR-2", "2026-07-20T21:00:00Z", {"PREMIUM": 400, "REGULAR": 280}),
        ("SHW-000503", "MOV-DUNE2", "THR-MUM-01", "SCR-1", "2026-07-21T19:00:00Z", {"PREMIUM": 500, "REGULAR": 350}),
        ("SHW-000504", "MOV-OPPEN", "THR-DEL-01", "SCR-1", "2026-07-20T15:00:00Z", {"PREMIUM": 450, "REGULAR": 300}),
        ("SHW-000505", "MOV-OPPEN", "THR-MUM-01", "SCR-1", "2026-07-22T18:00:00Z", {"PREMIUM": 500, "REGULAR": 350}),
        ("SHW-000506", "MOV-RRR", "THR-BLR-01", "SCR-1", "2026-07-20T20:00:00Z", {"PREMIUM": 420, "REGULAR": 280}),
        ("SHW-000507", "MOV-RRR", "THR-MUM-01", "SCR-1", "2026-07-21T16:30:00Z", {"PREMIUM": 480, "REGULAR": 320}),
        ("SHW-000508", "MOV-JAWAN", "THR-DEL-01", "SCR-1", "2026-07-21T18:30:00Z", {"PREMIUM": 450, "REGULAR": 300}),
        ("SHW-000509", "MOV-JAWAN", "THR-BLR-01", "SCR-1", "2026-07-22T21:00:00Z", {"PREMIUM": 420, "REGULAR": 280}),
        ("SHW-000510", "MOV-DUNE2", "THR-BLR-01", "SCR-1", "2026-07-23T18:30:00Z", {"PREMIUM": 420, "REGULAR": 280}),
        ("SHW-000511", "MOV-OPPEN", "THR-DEL-01", "SCR-2", "2026-07-23T12:00:00Z", {"PREMIUM": 400, "REGULAR": 280}),
        ("SHW-000512", "MOV-JAWAN", "THR-MUM-01", "SCR-1", "2026-07-23T20:30:00Z", {"PREMIUM": 500, "REGULAR": 350}),
    ]

    shows: list[dict[str, Any]] = []
    for show_id, movie_id, theater_id, screen_id, start_iso, prices in show_specs:
        theater = theater_by_id[theater_id]

        # Find the matching screen inside the embedded screens[] array
        screen = next(s for s in theater["screens"] if s["screen_id"] == screen_id)

        # Parse ISO string into a timezone-aware datetime -> stored as BSON Date
        start_time = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(timezone.utc)

        shows.append(
            {
                "_id": show_id,
                "movie_id": movie_id,
                "theater_id": theater_id,
                "screen_id": screen_id,
                "city": theater["city"],  # denormalized for city+movie+time queries
                "start_time": start_time,
                "prices": prices,
                # Fresh AVAILABLE seats copied from the screen layout
                "seats": build_seats_for_screen(
                    rows=screen["rows"],
                    seats_per_row=screen["seats_per_row"],
                    categories=screen["categories"],
                ),
            }
        )
    return shows


# ============================================================
# 4) DATABASE WRITE LOGIC
# ============================================================

def get_database():
    """
    Connect to Atlas and return the target database handle.

    Raises SystemExit with a readable message if URI is missing or unreachable.
    """
    if not MONGODB_URI or "{{" in MONGODB_URI:
        print(
            "ERROR: MONGODB_URI is missing or still has placeholders.\n"
            "Copy example.env to .env and set your Atlas connection string."
        )
        sys.exit(1)

    # MongoClient opens a connection pool; ping proves credentials + network work.
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    try:
        client.admin.command("ping")
    except ConnectionFailure as exc:
        print(f"ERROR: Could not reach MongoDB Atlas: {exc}")
        sys.exit(1)

    return client[MONGODB_DB]


def replace_collection(db, name: str, documents: list[dict[str, Any]]) -> int:
    """
    Optionally clear a collection, then insert_many the seed documents.

    Returns the number of documents inserted.
    """
    collection = db[name]

    if RESET_COLLECTIONS:
        # delete_many({}) removes all docs but keeps the collection + indexes.
        deleted = collection.delete_many({})
        print(f"  cleared {name}: deleted {deleted.deleted_count} existing document(s)")

    if not documents:
        return 0

    try:
        result = collection.insert_many(documents, ordered=True)
    except OperationFailure as exc:
        # Often means schema validation rejected a document - useful later in Part B.
        print(f"ERROR inserting into {name}: {exc}")
        sys.exit(1)

    return len(result.inserted_ids)


def seed() -> None:
    """Main entry: write all seed collections and print counts."""
    print(f"Connecting to database '{MONGODB_DB}' ...")
    db = get_database()
    print("Connected.\n")

    shows = build_shows()

    print("Seeding collections:")
    counts = {
        "movies": replace_collection(db, "movies", MOVIES),
        "theaters": replace_collection(db, "theaters", THEATERS),
        "shows": replace_collection(db, "shows", shows),
    }

    # If an older run created a users collection, remove it so the model stays clean.
    if "users" in db.list_collection_names():
        db.drop_collection("users")
        print("  dropped leftover 'users' collection (not used — mobile lives on bookings)")

    print("\nSeed complete:")
    for name, count in counts.items():
        print(f"  {name:10s} -> {count} document(s)")

    # Quick sanity check: one show should have 50 seats (5 rows x 10) for SCR-1 layouts
    sample = db["shows"].find_one({"_id": "SHW-000512"})
    if sample:
        print(f"\nSample show SHW-000512 has {len(sample['seats'])} seats "
              f"(city={sample['city']}, start_time={sample['start_time'].isoformat()})")

    print("\nNext: open MongoDB Compass and browse the 'cinebook' database.")


# ============================================================
# 5) SCRIPT ENTRY POINT
# ============================================================

if __name__ == "__main__":
    # Only runs when you execute: python seeds.py
    # Importing this file elsewhere will NOT auto-seed.
    seed()
