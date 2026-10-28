"""
app.py
------
CineBook FastAPI backend + static UI.

Flow this file supports
1. Browse movies
2. Pick a show for a movie
3. View / select seats
4. Continue to payout → seats locked as HELD + booking AWAITING_PAYMENT for 5 minutes
5. Pay within 5 min → seats BOOKED + booking status BOOKED (payment PAID)
   Miss the window or leave payout → booking EXPIRED, seats free on next read

Hold expiry uses a MongoDB TTL index on the `holds` collection (no backend sweeper).
TTL cannot update nested seat fields, so seats are synced lazily when APIs run.

Run (from assignments/, venv active)
    uvicorn app:app --reload --port 8000

Then open http://127.0.0.1:8000/
"""

# ------------------------------------------------------------
# Imports
# ------------------------------------------------------------
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pydantic import BaseModel, Field, field_validator

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
# Load .env from this folder (same place as example.env / seeds.py)
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "cinebook")
STATIC_DIR = BASE_DIR / "static"

# How long unpaid seats stay locked after "Continue to payout"
HOLD_MINUTES = 5

# Shared DB handle filled during app lifespan (startup / shutdown)
db: Optional[AsyncIOMotorDatabase] = None
mongo_client: Optional[AsyncIOMotorClient] = None


# ============================================================
# Request / response models
# ============================================================

def _normalize_seats(seats: list[str]) -> list[str]:
    """Uppercase + unique while preserving order."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for seat in seats:
        label = seat.strip().upper()
        if label and label not in seen:
            seen.add(label)
            cleaned.append(label)
    if not cleaned:
        raise ValueError("at least one seat is required")
    return cleaned


class SeatHoldCreate(BaseModel):
    """Body for POST /api/holds — locks seats for 5 minutes before payment."""

    show_id: str
    seats: list[str] = Field(..., min_length=1)
    city: str = Field(..., min_length=1)

    @field_validator("city")
    @classmethod
    def validate_city(cls, value: str) -> str:
        city = value.strip()
        if not city:
            raise ValueError("city is required")
        return city

    @field_validator("seats")
    @classmethod
    def normalize_seats(cls, seats: list[str]) -> list[str]:
        return _normalize_seats(seats)


class BookingCreate(BaseModel):
    """Body for POST /api/bookings — payment confirmation after a hold."""

    show_id: str = Field(..., description="Show being booked, e.g. SHW-000512")
    seats: list[str] = Field(..., min_length=1, description="Seat labels, e.g. ['A1','A5']")
    city: str = Field(..., min_length=1, description="Selected city, e.g. Delhi")
    mobile_number: str = Field(..., description="Customer phone — digits only, 6 to 10 digits")
    # Required: seats must already be HELD under this token from POST /api/holds
    hold_id: str = Field(..., description="Hold token returned when seats were locked")

    @field_validator("city")
    @classmethod
    def validate_city(cls, value: str) -> str:
        city = value.strip()
        if not city:
            raise ValueError("city is required — please select a city before booking")
        return city

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile(cls, value: str) -> str:
        raw = value.strip()
        if not raw.isdigit():
            raise ValueError("phone number must contain digits only (no letters or symbols)")
        if len(raw) < 6 or len(raw) > 10:
            raise ValueError("phone number must be between 6 and 10 digits")
        return raw

    @field_validator("seats")
    @classmethod
    def normalize_seats(cls, seats: list[str]) -> list[str]:
        return _normalize_seats(seats)


# ============================================================
# Lifespan: connect Mongo, ensure collections + TTL index
# ============================================================

async def ensure_holds_ttl_index(database: AsyncIOMotorDatabase) -> None:
    """
    TTL on holds.expireAt — MongoDB deletes the hold document when expireAt is reached.
    expireAfterSeconds=0 means "delete as soon as expireAt is in the past".

    Note: MongoDB's TTL monitor runs about once a minute, so deletion can lag
    slightly past 5:00. Lazy seat sync (release_stale_held_seats) covers that gap.
    """
    await database["holds"].create_index(
        "expireAt",
        expireAfterSeconds=0,
        name="ttl_hold_expireAt",
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    Startup:
    - Connect to Atlas
    - Ensure bookings + holds collections
    - Create TTL index on holds.expireAt (5-minute unpaid holds)
    - Lazy-sync any HELD seats whose hold docs are already gone
    """
    global db, mongo_client

    if not MONGODB_URI or "{{" in MONGODB_URI:
        raise RuntimeError(
            "MONGODB_URI is missing or still has placeholders. "
            "Copy example.env to .env and set your Atlas URI."
        )

    mongo_client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    await mongo_client.admin.command("ping")
    db = mongo_client[MONGODB_DB]

    existing = await db.list_collection_names()
    for name in ("bookings", "holds"):
        if name not in existing:
            await db.create_collection(name)
            print(f"Created collection: {name}")
        else:
            print(f"Collection already exists: {name}")

    await ensure_holds_ttl_index(db)
    # Sync seats left HELD after TTL already deleted their hold docs
    await release_stale_held_seats(db)

    print(
        f"Connected to '{MONGODB_DB}' — holds use MongoDB TTL "
        f"({HOLD_MINUTES} min, no backend sweeper)"
    )
    yield

    if mongo_client is not None:
        mongo_client.close()
        print("MongoDB connection closed")


app = FastAPI(title="CineBook", version="1.0.0", lifespan=lifespan)

# Serve CSS / JS / images from ./static at URL path /static/...
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================
# Helpers
# ============================================================

def require_db() -> AsyncIOMotorDatabase:
    """Guard so routes never run without a live DB handle."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    return db


def serialize_value(value: Any) -> Any:
    """Recursively convert datetimes so nested status_history serializes cleanly."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}
    return value


def serialize_doc(doc: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Make a Mongo document JSON-safe for FastAPI responses."""
    if doc is None:
        return None
    return {key: serialize_value(value) for key, value in doc.items()}


def make_status_entry(status: str, at: Optional[datetime] = None) -> dict[str, Any]:
    """One snapshot in status_history: {status, at}."""
    return {"status": status, "at": at or datetime.now(timezone.utc)}


def append_status_history(history: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    """
    Append a status snapshot and keep ONLY the latest 2 entries.
    current status is always history[-1].status
    """
    updated = list(history or []) + [make_status_entry(status)]
    return updated[-2:]


def current_status_from_history(history: list[dict[str, Any]], fallback: str = "BOOKED") -> str:
    """Most recent status = last entry in status_history."""
    if history:
        return history[-1]["status"]
    return fallback


def as_utc(value: datetime) -> datetime:
    """Mongo may return naive UTC datetimes; comparisons need a timezone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def expire_awaiting_bookings(
    database: AsyncIOMotorDatabase,
    show_id: Optional[str] = None,
) -> None:
    """
    Unpaid bookings do not stay AWAITING_PAYMENT after the hold is gone.

    TTL deletes the hold document and cannot edit the booking, so this runs
    lazily: if the hold doc is missing or expireAt is past, status becomes EXPIRED.
    BOOKED bookings are never touched here.
    """
    now = datetime.now(timezone.utc)
    query: dict[str, Any] = {"status": "AWAITING_PAYMENT"}
    if show_id:
        query["show_id"] = show_id

    async for booking in database["bookings"].find(query):
        hold = await database["holds"].find_one({"_id": booking.get("hold_id")})
        if hold and hold.get("expireAt") and as_utc(hold["expireAt"]) > now:
            continue
        history = append_status_history(list(booking.get("status_history") or []), "EXPIRED")
        await database["bookings"].update_one(
            {"_id": booking["_id"], "status": "AWAITING_PAYMENT"},
            {
                "$set": {
                    "status": "EXPIRED",
                    "status_history": history,
                    "payment_status": "UNPAID",
                }
            },
        )


async def release_seats(database: AsyncIOMotorDatabase, show_id: str, seats: list[str]) -> None:
    """On cancel: flip this booking's seats back to AVAILABLE on the show document."""
    await database["shows"].update_one(
        {"_id": show_id},
        {
            "$set": {
                "seats.$[s].status": "AVAILABLE",
                "seats.$[s].hold_id": None,
                "seats.$[s].held_until": None,
            }
        },
        array_filters=[{"s.seat": {"$in": seats}, "s.status": "BOOKED"}],
    )


async def release_stale_held_seats(
    database: AsyncIOMotorDatabase,
    show_id: Optional[str] = None,
) -> int:
    """
    Lazy seat sync (replaces the backend sweeper).

    A hold is alive only while its document exists in `holds`.
    MongoDB TTL deletes that doc after expireAt. When that happens (or when
    held_until is already past), flip matching HELD seats back to AVAILABLE.
    Matching AWAITING_PAYMENT bookings become EXPIRED.

    Called on seat reads / hold / book — not on a timer.
    """
    now = datetime.now(timezone.utc)
    query: dict[str, Any] = {"seats.status": "HELD"}
    if show_id:
        query["_id"] = show_id

    modified = 0
    cursor = database["shows"].find(query, {"seats": 1})
    async for show in cursor:
        hold_ids = {
            s.get("hold_id")
            for s in show.get("seats", [])
            if s.get("status") == "HELD" and s.get("hold_id")
        }
        if not hold_ids:
            continue

        alive: set[str] = set()
        async for doc in database["holds"].find(
            {"_id": {"$in": list(hold_ids)}, "expireAt": {"$gt": now}},
            {"_id": 1},
        ):
            alive.add(doc["_id"])

        stale_ids = list(hold_ids - alive)
        # Free seats whose hold doc is gone (TTL deleted) ...
        if stale_ids:
            result = await database["shows"].update_one(
                {"_id": show["_id"]},
                {
                    "$set": {
                        "seats.$[s].status": "AVAILABLE",
                        "seats.$[s].hold_id": None,
                        "seats.$[s].held_until": None,
                    }
                },
                array_filters=[{"s.status": "HELD", "s.hold_id": {"$in": stale_ids}}],
            )
            modified += result.modified_count

        # ... and seats past held_until (covers TTL monitor lag of ~60s)
        result = await database["shows"].update_one(
            {"_id": show["_id"]},
            {
                "$set": {
                    "seats.$[s].status": "AVAILABLE",
                    "seats.$[s].hold_id": None,
                    "seats.$[s].held_until": None,
                }
            },
            array_filters=[{"s.status": "HELD", "s.held_until": {"$lte": now}}],
        )
        modified += result.modified_count

    await expire_awaiting_bookings(database, show_id)
    return modified


async def hold_seats_atomically(
    database: AsyncIOMotorDatabase,
    show_id: str,
    seats: list[str],
    hold_id: str,
    held_until: datetime,
) -> bool:
    """
    Lock seats for payment: AVAILABLE → HELD (all-or-nothing, single-document atomic).
    Caller also inserts a `holds` doc with expireAt for MongoDB TTL.
    """
    all_available = [
        {"seats": {"$elemMatch": {"seat": seat, "status": "AVAILABLE"}}}
        for seat in seats
    ]
    result = await database["shows"].update_one(
        {"_id": show_id, "$and": all_available},
        {
            "$set": {
                "seats.$[s].status": "HELD",
                "seats.$[s].hold_id": hold_id,
                "seats.$[s].held_until": held_until,
            }
        },
        array_filters=[{"s.seat": {"$in": seats}, "s.status": "AVAILABLE"}],
    )
    return result.matched_count == 1 and result.modified_count == 1


async def confirm_held_seats_atomically(
    database: AsyncIOMotorDatabase,
    show_id: str,
    seats: list[str],
    hold_id: str,
) -> bool:
    """
    Payment success: require live hold doc (TTL not deleted) + HELD seats → BOOKED.
    """
    now = datetime.now(timezone.utc)
    hold = await database["holds"].find_one({"_id": hold_id})
    if not hold or as_utc(hold["expireAt"]) <= now:
        return False

    still_held = [
        {
            "seats": {
                "$elemMatch": {
                    "seat": seat,
                    "status": "HELD",
                    "hold_id": hold_id,
                }
            }
        }
        for seat in seats
    ]
    result = await database["shows"].update_one(
        {"_id": show_id, "$and": still_held},
        {
            "$set": {
                "seats.$[s].status": "BOOKED",
                "seats.$[s].hold_id": None,
                "seats.$[s].held_until": None,
            }
        },
        array_filters=[
            {
                "s.seat": {"$in": seats},
                "s.status": "HELD",
                "s.hold_id": hold_id,
            }
        ],
    )
    return result.matched_count == 1 and result.modified_count == 1


async def release_hold_by_id(
    database: AsyncIOMotorDatabase,
    show_id: str,
    seats: list[str],
    hold_id: str,
) -> bool:
    """User abandoned payout early — delete hold doc, free seats, expire the booking."""
    await database["holds"].delete_one({"_id": hold_id})
    await expire_awaiting_bookings(database, show_id)
    result = await database["shows"].update_one(
        {"_id": show_id},
        {
            "$set": {
                "seats.$[s].status": "AVAILABLE",
                "seats.$[s].hold_id": None,
                "seats.$[s].held_until": None,
            }
        },
        array_filters=[
            {"s.seat": {"$in": seats}, "s.status": "HELD", "s.hold_id": hold_id},
        ],
    )
    return result.modified_count == 1


def compute_amount(show: dict[str, Any], seat_labels: list[str]) -> int:
    """Sum ticket prices for the selected seats using show.prices[category]."""
    price_by_seat = {s["seat"]: show["prices"][s["category"]] for s in show["seats"]}
    total = 0
    for label in seat_labels:
        if label not in price_by_seat:
            raise HTTPException(status_code=400, detail=f"Seat {label} does not exist on this show")
        total += price_by_seat[label]
    return total


# ============================================================
# UI route
# ============================================================

@app.get("/")
async def home():
    """Serve the single-page booking UI."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="static/index.html missing")
    return FileResponse(index)


# ============================================================
# Movie / show / seat APIs
# ============================================================

@app.get("/api/movies")
async def list_movies(
    city: Optional[str] = Query(None, description="Filter movies playing in this city"),
    language: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
):
    """Browse movies — optional filters for city, language, genre."""
    database = require_db()
    query: dict[str, Any] = {}

    if city:
        # movies.cities is an array — equality matches if city is in the array
        query["cities"] = city
    if language:
        query["language"] = language
    if genre:
        query["genres"] = genre

    cursor = database["movies"].find(query).sort("title", 1)
    movies = [serialize_doc(doc) async for doc in cursor]
    return {"movies": movies}


@app.get("/api/movies/{movie_id}/shows")
async def list_shows_for_movie(
    movie_id: str,
    city: Optional[str] = Query(None),
    date: Optional[str] = Query(None, description="YYYY-MM-DD (UTC day window)"),
):
    """
    Shows for one movie, optional city + date filters.
    Enriches each show with theater name for the UI.
    """
    database = require_db()

    movie = await database["movies"].find_one({"_id": movie_id})
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    query: dict[str, Any] = {"movie_id": movie_id}
    if city:
        query["city"] = city

    if date:
        # Match shows whose start_time falls on that calendar day (UTC)
        try:
            day_start = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
        day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)
        query["start_time"] = {"$gte": day_start, "$lte": day_end}

    # Do not send the full seats[] array in the list view (large + unnecessary)
    projection = {"seats": 0}
    cursor = database["shows"].find(query, projection).sort("start_time", 1)
    shows: list[dict[str, Any]] = []

    async for show in cursor:
        theater = await database["theaters"].find_one({"_id": show["theater_id"]})
        item = serialize_doc(show) or {}
        item["theater_name"] = theater["name"] if theater else show["theater_id"]
        item["movie_title"] = movie["title"]
        # Quick availability count for the UI badge
        await release_stale_held_seats(database, show["_id"])
        full = await database["shows"].find_one({"_id": show["_id"]}, {"seats.status": 1})
        available = sum(1 for s in full["seats"] if s["status"] == "AVAILABLE") if full else 0
        item["available_seats"] = available
        item["total_seats"] = len(full["seats"]) if full else 0
        shows.append(item)

    return {"movie": serialize_doc(movie), "shows": shows}


@app.get("/api/shows/{show_id}")
async def get_show(show_id: str):
    """Show details without the heavy seats array (use /seats for that)."""
    database = require_db()
    show = await database["shows"].find_one({"_id": show_id}, {"seats": 0})
    if not show:
        raise HTTPException(status_code=404, detail="Show not found")

    movie = await database["movies"].find_one({"_id": show["movie_id"]})
    theater = await database["theaters"].find_one({"_id": show["theater_id"]})
    payload = serialize_doc(show) or {}
    payload["movie_title"] = movie["title"] if movie else show["movie_id"]
    payload["theater_name"] = theater["name"] if theater else show["theater_id"]
    return payload


@app.get("/api/shows/{show_id}/seats")
async def get_show_seats(show_id: str):
    """Current seat map + prices. Expired unpaid holds are released first."""
    database = require_db()
    await release_stale_held_seats(database, show_id)

    show = await database["shows"].find_one({"_id": show_id})
    if not show:
        raise HTTPException(status_code=404, detail="Show not found")

    movie = await database["movies"].find_one({"_id": show["movie_id"]})
    theater = await database["theaters"].find_one({"_id": show["theater_id"]})

    return {
        "show_id": show["_id"],
        "movie_title": movie["title"] if movie else show["movie_id"],
        "theater_name": theater["name"] if theater else show["theater_id"],
        "city": show["city"],
        "start_time": show["start_time"].isoformat(),
        "prices": show["prices"],
        "seats": show["seats"],
        "hold_minutes": HOLD_MINUTES,
    }


# ============================================================
# Seat hold (lock for 5 minutes) + booking confirmation
# ============================================================

@app.post("/api/holds")
async def create_hold(payload: SeatHoldCreate):
    """
    Called when the user clicks "Continue to payout".

    1. Atomically mark seats HELD on the show document
    2. Insert a `holds` document with expireAt = now+5min
       → MongoDB TTL index deletes that doc after 5 minutes
    3. Insert the booking as AWAITING_PAYMENT (not BOOKED yet)
    4. Next seat/hold/book request syncs HELD → AVAILABLE and
       AWAITING_PAYMENT → EXPIRED if the hold doc is gone
    """
    database = require_db()
    await release_stale_held_seats(database, payload.show_id)

    show = await database["shows"].find_one({"_id": payload.show_id})
    if not show:
        raise HTTPException(status_code=404, detail="Show not found")

    if payload.city.casefold() != str(show["city"]).casefold():
        raise HTTPException(
            status_code=400,
            detail=f"Selected city '{payload.city}' does not match this show's city '{show['city']}'",
        )

    amount = compute_amount(show, payload.seats)

    now = datetime.now(timezone.utc)
    hold_id = f"HLD-{uuid.uuid4().hex[:10]}"
    booking_id = f"BKG-{uuid.uuid4().hex[:8]}"
    held_until = now + timedelta(minutes=HOLD_MINUTES)

    locked = await hold_seats_atomically(
        database, payload.show_id, payload.seats, hold_id, held_until
    )
    if not locked:
        raise HTTPException(
            status_code=409,
            detail="One or more selected seats are no longer available. Please pick again.",
        )

    # TTL target document — MongoDB deletes this when expireAt is reached.
    # The booking exists already, but only as AWAITING_PAYMENT until Pay succeeds.
    try:
        await database["holds"].insert_one(
            {
                "_id": hold_id,
                "show_id": payload.show_id,
                "seats": payload.seats,
                "city": show["city"],
                "booking_id": booking_id,
                "created_at": now,
                "expireAt": held_until,  # TTL index field
            }
        )
        await database["bookings"].insert_one(
            {
                "_id": booking_id,
                "show_id": payload.show_id,
                "mobile_number": "",
                "seats": payload.seats,
                "amount": amount,
                "status": "AWAITING_PAYMENT",
                "status_history": [make_status_entry("AWAITING_PAYMENT", now)],
                "payment_status": "PENDING",
                "hold_id": hold_id,
                "created_at": now,
                "movie_id": show["movie_id"],
                "city": show["city"],
            }
        )
    except Exception:
        await database["bookings"].delete_one({"_id": booking_id})
        await release_hold_by_id(database, payload.show_id, payload.seats, hold_id)
        raise HTTPException(status_code=500, detail="Could not create hold record")

    return {
        "message": "Seats locked — booking is awaiting payment",
        "hold_id": hold_id,
        "booking_id": booking_id,
        "booking_status": "AWAITING_PAYMENT",
        "payment_status": "PENDING",
        "show_id": payload.show_id,
        "seats": payload.seats,
        "amount": amount,
        "city": show["city"],
        "held_until": held_until.isoformat(),
        "hold_minutes": HOLD_MINUTES,
    }


@app.post("/api/holds/{hold_id}/release")
async def release_hold_early(
    hold_id: str,
    show_id: str = Query(...),
    seats: str = Query(..., description="Comma-separated seat labels, e.g. A1,A5"),
):
    """Release a hold early (user went back from payout without paying)."""
    database = require_db()
    seat_list = _normalize_seats(seats.split(","))
    ok = await release_hold_by_id(database, show_id, seat_list, hold_id)
    if not ok:
        # Already expired / confirmed / different hold — treat as success for the UI
        return {"message": "Hold already gone or released", "hold_id": hold_id}
    return {"message": "Hold released — seats available again", "hold_id": hold_id}


@app.post("/api/bookings")
async def create_booking(payload: BookingCreate):
    """
    Payment.

    Requires a live hold from POST /api/holds.
    Converts seats HELD → BOOKED and the booking AWAITING_PAYMENT → BOOKED
    only if the 5-minute window has not expired.
    """
    database = require_db()
    await release_stale_held_seats(database, payload.show_id)

    show = await database["shows"].find_one({"_id": payload.show_id})
    if not show:
        raise HTTPException(status_code=404, detail="Show not found")

    if payload.city.casefold() != str(show["city"]).casefold():
        raise HTTPException(
            status_code=400,
            detail=f"Selected city '{payload.city}' does not match this show's city '{show['city']}'",
        )

    amount = compute_amount(show, payload.seats)

    booking = await database["bookings"].find_one(
        {"hold_id": payload.hold_id, "status": "AWAITING_PAYMENT"}
    )
    if not booking:
        await release_stale_held_seats(database, payload.show_id)
        raise HTTPException(
            status_code=409,
            detail=(
                "This booking is no longer awaiting payment. "
                "The hold may have expired — please select seats and try again."
            ),
        )

    # --- confirm hold → seat BOOKED (fails if TTL deleted the hold or seats taken) ---
    confirmed = await confirm_held_seats_atomically(
        database, payload.show_id, payload.seats, payload.hold_id
    )
    if not confirmed:
        # Sync seats and flip the unpaid booking to EXPIRED
        await release_stale_held_seats(database, payload.show_id)
        raise HTTPException(
            status_code=409,
            detail=(
                "Seat hold expired or is no longer valid. "
                "The booking is EXPIRED and seats were released — please select seats and try again."
            ),
        )

    now = datetime.now(timezone.utc)
    status_history = append_status_history(
        list(booking.get("status_history") or []),
        "BOOKED",
    )
    paid = await database["bookings"].update_one(
        {"_id": booking["_id"], "status": "AWAITING_PAYMENT"},
        {
            "$set": {
                "mobile_number": payload.mobile_number,
                "amount": amount,
                "status": "BOOKED",
                "status_history": status_history,
                "payment_status": "PAID",
            }
        },
    )
    if paid.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Booking changed before payment could finish. Please try again.",
        )

    # Payment done — remove hold so TTL no longer applies. Booking stays BOOKED.
    await database["holds"].delete_one({"_id": payload.hold_id})

    booking.update(
        {
            "mobile_number": payload.mobile_number,
            "amount": amount,
            "status": "BOOKED",
            "status_history": status_history,
            "payment_status": "PAID",
        }
    )
    return {
        "message": "Booking booked",
        "booking": serialize_doc(booking),
    }


@app.get("/api/bookings/{booking_id}")
async def get_booking(booking_id: str):
    """Fetch one booking by id (receipt). Refreshes an unpaid hold if it has expired."""
    database = require_db()
    booking = await database["bookings"].find_one({"_id": booking_id})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.get("status") == "AWAITING_PAYMENT":
        await release_stale_held_seats(database, booking.get("show_id"))
        booking = await database["bookings"].find_one({"_id": booking_id})
    return serialize_doc(booking)


@app.get("/api/bookings")
async def list_bookings(
    mobile_number: Optional[str] = Query(
        None, description="Optional phone filter (6–10 digits). Omit to list recent bookings."
    ),
):
    """
    View bookings.
    - With mobile_number: that customer's bookings
    - Without: latest bookings (for the My Bookings screen)

    Unpaid holds that have already lapsed are marked EXPIRED first.
    """
    database = require_db()
    await release_stale_held_seats(database)
    query: dict[str, Any] = {}

    if mobile_number is not None and mobile_number.strip() != "":
        raw = mobile_number.strip()
        if not raw.isdigit() or len(raw) < 6 or len(raw) > 10:
            raise HTTPException(
                status_code=400,
                detail="mobile_number must be digits only, between 6 and 10 characters",
            )
        query["mobile_number"] = raw

    cursor = database["bookings"].find(query).sort("created_at", -1).limit(50)
    bookings = [serialize_doc(doc) async for doc in cursor]
    return {"bookings": bookings, "mobile_number": query.get("mobile_number")}


@app.post("/api/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str):
    """
    Cancel a BOOKED booking.

    Rules
    - Only a paid BOOKED booking can be cancelled.
    - AWAITING_PAYMENT ends as EXPIRED when the hold lapses (not via cancel).
    - CANCELLED bookings cannot be changed back to BOOKED.
    - status_history keeps at most 2 snapshots (BOOKED then CANCELLED).
    - Seats are released back to AVAILABLE on the show.
    """
    database = require_db()
    booking = await database["bookings"].find_one({"_id": booking_id})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    history = list(booking.get("status_history") or [])
    if not history:
        history = [make_status_entry(booking.get("status", "BOOKED"), booking.get("created_at"))]

    current = current_status_from_history(history, fallback=booking.get("status", "BOOKED"))

    if current == "CANCELLED":
        raise HTTPException(
            status_code=400,
            detail="Booking is already CANCELLED and cannot be changed back to BOOKED",
        )

    if current != "BOOKED":
        raise HTTPException(
            status_code=400,
            detail=f"Only BOOKED bookings can be cancelled (current status: {current})",
        )

    # Append CANCELLED; trim to max 2 history entries
    new_history = append_status_history(history, "CANCELLED")
    new_status = current_status_from_history(new_history)  # -> CANCELLED

    await database["bookings"].update_one(
        {"_id": booking_id},
        {
            "$set": {
                "status": new_status,
                "status_history": new_history,
                "payment_status": "REFUNDED",  # simulated refund on cancel
            }
        },
    )

    # Free the seats so someone else can book them
    await release_seats(database, booking["show_id"], booking["seats"])

    updated = await database["bookings"].find_one({"_id": booking_id})
    return {
        "message": "Booking cancelled",
        "booking": serialize_doc(updated),
    }


# ============================================================
# Part D — Analytics aggregations
# ============================================================

from analytics_pipelines import (  # noqa: E402  — kept near routes for clarity
    occupancy_per_show_pipeline,
    peak_time_slots_pipeline,
    revenue_per_movie_pipeline,
    top_movies_pipeline,
)


@app.get("/api/analytics/revenue-by-movie")
async def analytics_revenue_by_movie():
    """Total paid booking amount grouped by movie."""
    database = require_db()
    cursor = database["bookings"].aggregate(revenue_per_movie_pipeline())
    return {"metric": "revenue_by_movie", "rows": [doc async for doc in cursor]}


@app.get("/api/analytics/occupancy-by-show")
async def analytics_occupancy_by_show():
    """Booked seats / total seats for each show."""
    database = require_db()
    cursor = database["shows"].aggregate(occupancy_per_show_pipeline())
    rows = []
    async for doc in cursor:
        if isinstance(doc.get("start_time"), datetime):
            doc["start_time"] = doc["start_time"].isoformat()
        rows.append(doc)
    return {"metric": "occupancy_by_show", "rows": rows}


@app.get("/api/analytics/top-movies")
async def analytics_top_movies(limit: int = Query(5, ge=1, le=20)):
    """Top movies by tickets sold (then revenue)."""
    database = require_db()
    cursor = database["bookings"].aggregate(top_movies_pipeline(limit))
    return {"metric": "top_movies", "rows": [doc async for doc in cursor]}


@app.get("/api/analytics/peak-time-slots")
async def analytics_peak_time_slots():
    """Bookings / revenue grouped by hour of show start_time (UTC)."""
    database = require_db()
    cursor = database["bookings"].aggregate(peak_time_slots_pipeline())
    return {"metric": "peak_time_slots", "rows": [doc async for doc in cursor]}


@app.get("/api/analytics/summary")
async def analytics_summary():
    """One-shot payload for the Analytics UI (all four Part D metrics)."""
    database = require_db()

    revenue = [doc async for doc in database["bookings"].aggregate(revenue_per_movie_pipeline())]
    top = [doc async for doc in database["bookings"].aggregate(top_movies_pipeline(5))]
    peak = [doc async for doc in database["bookings"].aggregate(peak_time_slots_pipeline())]

    occupancy = []
    async for doc in database["shows"].aggregate(occupancy_per_show_pipeline()):
        if isinstance(doc.get("start_time"), datetime):
            doc["start_time"] = doc["start_time"].isoformat()
        occupancy.append(doc)

    return {
        "revenue_by_movie": revenue,
        "occupancy_by_show": occupancy,
        "top_movies": top,
        "peak_time_slots": peak,
    }


# ============================================================
# Health check (handy while developing)
# ============================================================

@app.get("/api/health")
async def health():
    database = require_db()
    await database.command("ping")
    return {"status": "ok", "database": MONGODB_DB}
