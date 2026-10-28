"""
query.py
--------
Create random bookings against existing shows.

Rules
- Each booking reserves between 1 and 5 seats (random).
- Only AVAILABLE seats are chosen.
- Uses the same atomic all-or-nothing update as POST /api/bookings.
- Skips a show if it does not have enough free seats.

How to run (from assignments/, venv active)
    python query.py
    python query.py --count 20
    python query.py --count 10 --min-seats 2 --max-seats 5
"""

import argparse
import os
import random
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "cinebook")


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def connect():
    """Open Atlas connection and return the cinebook database."""
    if not MONGODB_URI or "{{" in MONGODB_URI:
        print("ERROR: Set MONGODB_URI in .env first (copy from example.env).")
        sys.exit(1)

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    try:
        client.admin.command("ping")
    except ConnectionFailure as exc:
        print(f"ERROR: Could not reach MongoDB Atlas: {exc}")
        sys.exit(1)
    return client[MONGODB_DB]


def random_mobile() -> str:
    """6–10 digit phone (matches app validation)."""
    length = random.randint(6, 10)
    # First digit 6-9 so it looks like a mobile prefix
    first = str(random.randint(6, 9))
    rest = "".join(str(random.randint(0, 9)) for _ in range(length - 1))
    return first + rest


def compute_amount(show: dict[str, Any], seat_labels: list[str]) -> int:
    """Sum ticket prices for the chosen seats."""
    price_by_seat = {s["seat"]: show["prices"][s["category"]] for s in show["seats"]}
    return sum(price_by_seat[label] for label in seat_labels)


def reserve_seats_atomically(db, show_id: str, seats: list[str]) -> bool:
    """
    All-or-nothing seat lock (same pattern as app.py).
    Returns True only if every seat was still AVAILABLE and is now BOOKED.
    """
    all_available = [
        {"seats": {"$elemMatch": {"seat": seat, "status": "AVAILABLE"}}}
        for seat in seats
    ]
    result = db["shows"].update_one(
        {"_id": show_id, "$and": all_available},
        {"$set": {"seats.$[s].status": "BOOKED"}},
        array_filters=[{"s.seat": {"$in": seats}, "s.status": "AVAILABLE"}],
    )
    return result.matched_count == 1 and result.modified_count == 1


def pick_random_available_seats(show: dict[str, Any], min_seats: int, max_seats: int) -> list[str]:
    """
    Choose 1–5 (or configured range) AVAILABLE seats at random from one show.
    Returns [] if not enough free seats.
    """
    available = [s["seat"] for s in show["seats"] if s["status"] == "AVAILABLE"]
    if len(available) < min_seats:
        return []

    upper = min(max_seats, len(available))
    n = random.randint(min_seats, upper)
    return sorted(random.sample(available, n))


def create_random_booking(db, show: dict[str, Any], seats: list[str]) -> dict[str, Any] | None:
    """
    Atomically reserve seats, then insert a bookings document.
    Returns the booking doc, or None if the seat lock lost a race.
    """
    if not reserve_seats_atomically(db, show["_id"], seats):
        return None

    now = datetime.now(timezone.utc)
    booking = {
        "_id": f"BKG-{uuid.uuid4().hex[:8]}",
        "show_id": show["_id"],
        "mobile_number": random_mobile(),
        "seats": seats,
        "amount": compute_amount(show, seats),
        "status": "BOOKED",
        "status_history": [{"status": "BOOKED", "at": now}],
        "payment_status": "PAID",
        "created_at": now,
        "movie_id": show["movie_id"],
        "city": show["city"],
        "source": "query.py",  # so you can tell seed traffic apart in Compass
    }
    db["bookings"].insert_one(booking)
    return booking


# ------------------------------------------------------------
# Main loop
# ------------------------------------------------------------

def run(count: int, min_seats: int, max_seats: int) -> None:
    """Attempt `count` random bookings across all shows."""
    if min_seats < 1 or max_seats > 5 or min_seats > max_seats:
        print("ERROR: seat range must be within 1–5 and min <= max")
        sys.exit(1)

    db = connect()
    shows = list(db["shows"].find({}))
    if not shows:
        print("ERROR: No shows found. Run seeds.py first.")
        sys.exit(1)

    print(f"Loaded {len(shows)} shows. Creating up to {count} bookings "
          f"({min_seats}-{max_seats} seats each)...\n")

    created = 0
    skipped = 0

    for i in range(1, count + 1):
        # Fresh read so seat availability stays accurate as we book
        show_meta = random.choice(shows)
        show = db["shows"].find_one({"_id": show_meta["_id"]})
        if not show:
            skipped += 1
            continue

        seats = pick_random_available_seats(show, min_seats, max_seats)
        if not seats:
            print(f"[{i:02d}] SKIP  {show['_id']} — not enough AVAILABLE seats")
            skipped += 1
            continue

        booking = create_random_booking(db, show, seats)
        if not booking:
            # Rare: another client booked one of these seats between read and update
            print(f"[{i:02d}] RACE  {show['_id']} seats {seats} — lost atomic lock")
            skipped += 1
            continue

        created += 1
        print(
            f"[{i:02d}] OK    {booking['_id']}  show={show['_id']}  "
            f"city={show['city']}  seats={seats}  "
            f"amount=₹{booking['amount']}  mobile={booking['mobile_number']}"
        )

    print(f"\nDone. created={created}  skipped={skipped}")
    total_bookings = db["bookings"].count_documents({})
    print(f"bookings collection now has {total_bookings} document(s).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Random CineBook bookings (1–5 seats each)")
    parser.add_argument("--count", type=int, default=15, help="How many booking attempts (default: 15)")
    parser.add_argument("--min-seats", type=int, default=1, help="Min seats per booking (default: 1)")
    parser.add_argument("--max-seats", type=int, default=5, help="Max seats per booking (default: 5)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(count=args.count, min_seats=args.min_seats, max_seats=args.max_seats)
