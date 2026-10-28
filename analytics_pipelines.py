"""
analytics_pipelines.py
----------------------
Part D — aggregation pipelines for CineBook operational analytics.

Design notes (assignment)
- Filter early with $match on paid bookings only (status BOOKED).
- $group is a blocking stage — keep the matched set small before grouping.
- $project only fields the UI / report needs.
- Join movies/shows with $lookup when a title or start_time is required.

Paid booking filter
    status == "BOOKED"
"""

from typing import Any

# Only completed (paid) bookings count toward revenue / tickets / peak slots.
PAID_STATUS_MATCH: dict[str, Any] = {
    "status": "BOOKED",
}


def revenue_per_movie_pipeline() -> list[dict[str, Any]]:
    """
    Total booking amount (and ticket count) grouped by movie.

    Stages
    1. $match  — paid bookings only (indexed-friendly on status)
    2. $group  — sum amount, count seats sold  [BLOCKING]
    3. $lookup — attach movie title
    4. $project / $sort
    """
    return [
        {"$match": PAID_STATUS_MATCH},
        {
            "$group": {
                "_id": "$movie_id",
                "revenue": {"$sum": "$amount"},
                "bookings": {"$sum": 1},
                "tickets_sold": {"$sum": {"$size": "$seats"}},
            }
        },
        {
            "$lookup": {
                "from": "movies",
                "localField": "_id",
                "foreignField": "_id",
                "as": "movie",
            }
        },
        {
            "$project": {
                "_id": 0,
                "movie_id": "$_id",
                "title": {"$ifNull": [{"$arrayElemAt": ["$movie.title", 0]}, "$_id"]},
                "revenue": 1,
                "bookings": 1,
                "tickets_sold": 1,
            }
        },
        {"$sort": {"revenue": -1}},
    ]


def occupancy_per_show_pipeline() -> list[dict[str, Any]]:
    """
    Occupancy = booked seats / total seats for each show.

    Runs on `shows` (seat status lives on the show document).
    HELD seats are not counted as booked — only BOOKED.
    """
    return [
        {
            "$project": {
                "_id": 0,
                "show_id": "$_id",
                "movie_id": 1,
                "theater_id": 1,
                "city": 1,
                "start_time": 1,
                "total_seats": {"$size": "$seats"},
                "booked_seats": {
                    "$size": {
                        "$filter": {
                            "input": "$seats",
                            "as": "s",
                            "cond": {"$eq": ["$$s.status", "BOOKED"]},
                        }
                    }
                },
            }
        },
        {
            "$addFields": {
                "occupancy_pct": {
                    "$cond": [
                        {"$eq": ["$total_seats", 0]},
                        0,
                        {
                            "$round": [
                                {
                                    "$multiply": [
                                        {"$divide": ["$booked_seats", "$total_seats"]},
                                        100,
                                    ]
                                },
                                1,
                            ]
                        },
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "movies",
                "localField": "movie_id",
                "foreignField": "_id",
                "as": "movie",
            }
        },
        {
            "$project": {
                "show_id": 1,
                "movie_id": 1,
                "title": {"$ifNull": [{"$arrayElemAt": ["$movie.title", 0]}, "$movie_id"]},
                "theater_id": 1,
                "city": 1,
                "start_time": 1,
                "booked_seats": 1,
                "total_seats": 1,
                "occupancy_pct": 1,
            }
        },
        {"$sort": {"occupancy_pct": -1, "start_time": 1}},
    ]


def top_movies_pipeline(limit: int = 5) -> list[dict[str, Any]]:
    """
    Top movies ranked by tickets sold, then revenue as tie-breaker.
    """
    return [
        {"$match": PAID_STATUS_MATCH},
        {
            "$group": {
                "_id": "$movie_id",
                "tickets_sold": {"$sum": {"$size": "$seats"}},
                "revenue": {"$sum": "$amount"},
                "bookings": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "movies",
                "localField": "_id",
                "foreignField": "_id",
                "as": "movie",
            }
        },
        {
            "$project": {
                "_id": 0,
                "movie_id": "$_id",
                "title": {"$ifNull": [{"$arrayElemAt": ["$movie.title", 0]}, "$_id"]},
                "tickets_sold": 1,
                "revenue": 1,
                "bookings": 1,
            }
        },
        {"$sort": {"tickets_sold": -1, "revenue": -1}},
        {"$limit": limit},
    ]


def peak_time_slots_pipeline() -> list[dict[str, Any]]:
    """
    Bookings + revenue grouped by hour-of-day of the show start_time (UTC).

    Stages
    1. $match paid bookings
    2. $lookup show → need start_time
    3. $unwind / $addFields hour
    4. $group by hour  [BLOCKING]
    5. $sort by hour
    """
    return [
        {"$match": PAID_STATUS_MATCH},
        {
            "$lookup": {
                "from": "shows",
                "localField": "show_id",
                "foreignField": "_id",
                "as": "show",
            }
        },
        {"$unwind": "$show"},
        {
            "$project": {
                "amount": 1,
                "tickets": {"$size": "$seats"},
                "hour": {"$hour": "$show.start_time"},
            }
        },
        {
            "$group": {
                "_id": "$hour",
                "bookings": {"$sum": 1},
                "tickets_sold": {"$sum": "$tickets"},
                "revenue": {"$sum": "$amount"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "hour": "$_id",
                "hour_label": {
                    "$concat": [
                        {"$cond": [{"$lt": ["$_id", 10]}, "0", ""]},
                        {"$toString": "$_id"},
                        ":00",
                    ]
                },
                "bookings": 1,
                "tickets_sold": 1,
                "revenue": 1,
            }
        },
        {"$sort": {"hour": 1}},
    ]
