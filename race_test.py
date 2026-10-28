"""
race_test.py
------------
Prove Part C: two concurrent bookings for the SAME seat — only one wins.

How to run (venv active, server optional — this hits Mongo directly):
    python race_test.py

What you should see
- Exactly one success (seat reserved + booking inserted)
- Exactly one failure (seat already taken)
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "cinebook")

SHOW_ID = "SHW-000502"   # Delhi Dune show
SEAT = "D5"              # both "users" fight for this seat


async def try_book(label: str, mobile: str) -> str:
    """
    Same atomic pattern as app.reserve_seats_atomically + booking insert.
    Runs as a coroutine so we can fire two at once with asyncio.gather.
    """
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client[MONGODB_DB]

    # --- atomic conditional update (all-or-nothing even for 1 seat) ---
    result = await db["shows"].update_one(
        {
            "_id": SHOW_ID,
            "seats": {"$elemMatch": {"seat": SEAT, "status": "AVAILABLE"}},
        },
        {"$set": {"seats.$[s].status": "BOOKED"}},
        array_filters=[{"s.seat": SEAT, "s.status": "AVAILABLE"}],
    )

    if result.matched_count != 1 or result.modified_count != 1:
        client.close()
        return f"{label}: REJECTED (seat not available)"

    now = datetime.now(timezone.utc)
    booking_id = f"BKG-race-{uuid.uuid4().hex[:6]}"
    await db["bookings"].insert_one(
        {
            "_id": booking_id,
            "show_id": SHOW_ID,
            "mobile_number": mobile,
            "seats": [SEAT],
            "amount": 300,
            "status": "BOOKED",
            "status_history": [{"status": "BOOKED", "at": now}],
            "payment_status": "PAID",
            "created_at": now,
            "movie_id": "MOV-DUNE2",
            "city": "Delhi",
            "note": "created by race_test.py",
        }
    )
    client.close()
    return f"{label}: SUCCESS booking {booking_id}"


async def reset_seat() -> None:
    """Put SEAT back to AVAILABLE so the race test is repeatable."""
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client[MONGODB_DB]
    await db["shows"].update_one(
        {"_id": SHOW_ID},
        {"$set": {"seats.$[s].status": "AVAILABLE"}},
        array_filters=[{"s.seat": SEAT}],
    )
    client.close()
    print(f"Reset {SHOW_ID} seat {SEAT} -> AVAILABLE")


async def main() -> None:
    if not MONGODB_URI or "{{" in MONGODB_URI:
        raise SystemExit("Set MONGODB_URI in .env first")

    await reset_seat()
    print(f"Racing two users for {SHOW_ID} / {SEAT} ...\n")

    # Fire both updates concurrently — MongoDB document-level atomicity decides the winner
    results = await asyncio.gather(
        try_book("User-A", "111111"),
        try_book("User-B", "222222"),
    )
    for line in results:
        print(line)

    wins = sum(1 for r in results if r.startswith("User") and "SUCCESS" in r)
    print(f"\nWins: {wins} (expected 1)")


if __name__ == "__main__":
    asyncio.run(main())
