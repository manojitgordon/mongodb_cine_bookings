# CineBook

Movie tickets on **MongoDB Atlas**. The browser talks to a FastAPI app. Seats live inside each show document, so two people cannot book the same seat. Unpaid seats are held for 5 minutes. Booking changes are streamed to Kafka by a **Confluent Cloud MongoDB Atlas Source connector**, and `consumer.py` prints those events.

```
Browser
   │
   ▼
app.py  ──serves──►  static/index.html, static/js/app.js, static/css/styles.css
   │
   ▼
MongoDB Atlas  (database: cinebook)
   movies · theaters · shows · bookings · holds
   │
   │  Atlas Source connector  (already running)
   ▼
Confluent Cloud   topic cdc.cinebook.bookings
   │
   ▼
consumer.py
```

Every command in this file is run from the `assignments` folder, with the virtual environment active.

---

## Before you start

- Python 3.11 or newer
- A MongoDB Atlas cluster, a database user, and your IP allowed on the cluster
- The Confluent Cloud cluster and the Atlas Source connector already created and running. This repo does not start the connector

---

## First-time setup

```powershell
cd assignments
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy example.env .env
```

Edit `.env`. Never commit it.

| Variable | Used by | Value |
|---|---|---|
| `MONGODB_URI` | every script and the app | Atlas connection string, `mongodb+srv://...` |
| `MONGODB_DB` | every script and the app | `cinebook` |
| `KAFKA_BOOTSTRAP` | `consumer.py` only | Confluent bootstrap servers |
| `KAFKA_API_KEY` | `consumer.py` only | Confluent Cloud API key |
| `KAFKA_API_SECRET` | `consumer.py` only | Confluent Cloud API secret |
| `KAFKA_TOPIC` | `consumer.py` only | `cdc.cinebook.bookings` |
| `KAFKA_GROUP_ID` | `consumer.py` only | `cinebook-cdc-consumer` |

Then load sample data and indexes:

```powershell
python seeds.py
python create_indexes.py
```

---

## Run the app

```powershell
uvicorn app:app --reload --port 8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

`--reload` restarts the server when `app.py` changes. Leave this terminal open. Use a second terminal for the scripts below.

Health check:

```powershell
curl http://127.0.0.1:8000/api/health
```

A healthy reply looks like `{"status":"ok","database":"cinebook"}`.

### What to click

1. **Book** — pick a city (Delhi, Mumbai, or Bengaluru). Movies for that city appear.
2. Open a movie, then a show. The seat map loads.
3. Select seats. **Continue to payout** locks them for 5 minutes and creates a booking in `AWAITING_PAYMENT`.
4. Enter a phone number (digits only, 6 to 10 characters) and choose **Pay now**. The booking becomes `BOOKED`.
5. **My bookings** — search by that phone, or **Show recent**.
6. **Analytics** — revenue, occupancy, top movies, and peak hours. **Refresh** reloads them.

Going back from payout releases the hold. If the 5 minutes run out, the seats open again and the booking becomes `EXPIRED`.

### Booking statuses

There is no users collection. The phone number on the booking is the customer.

| What happened | Seat on the show | Booking `status` | `payment_status` |
|---|---|---|---|
| Continue to payout | `HELD` | `AWAITING_PAYMENT` | `PENDING` |
| Pay within 5 minutes | `BOOKED` | `BOOKED` | `PAID` |
| Leave payout, or the hold expires | `AVAILABLE` | `EXPIRED` | `UNPAID` |
| Cancel a paid booking | `AVAILABLE` | `CANCELLED` | `REFUNDED` |

`BOOKED` is the only paid status. `status_history` keeps the latest two snapshots.

The hold is a document in `holds` with `expireAt` set 5 minutes ahead. A TTL index deletes that document when the time passes. The next API read then frees the seats and marks the booking `EXPIRED`.

### API routes

The UI calls these. You can call them with `curl` as well.

| Method | Path | What it does |
|---|---|---|
| `GET` | `/` | The booking page |
| `GET` | `/api/health` | Ping Atlas |
| `GET` | `/api/movies?city=Mumbai` | Movies in a city |
| `GET` | `/api/movies/{movie_id}/shows?city=Mumbai` | Shows for that movie |
| `GET` | `/api/shows/{show_id}` | One show, without the seat array |
| `GET` | `/api/shows/{show_id}/seats` | Seat map. Also releases expired holds |
| `POST` | `/api/holds` | Lock seats and create an `AWAITING_PAYMENT` booking |
| `POST` | `/api/holds/{hold_id}/release` | Give the seats back early |
| `POST` | `/api/bookings` | Pay. Turns the booking into `BOOKED` |
| `GET` | `/api/bookings` | Recent bookings, or filter with `?mobile_number=` |
| `GET` | `/api/bookings/{booking_id}` | One booking |
| `POST` | `/api/bookings/{booking_id}/cancel` | Cancel a `BOOKED` booking and free its seats |
| `GET` | `/api/analytics/summary` | All four analytics in one response |
| `GET` | `/api/analytics/revenue-by-movie` | Revenue grouped by movie |
| `GET` | `/api/analytics/occupancy-by-show` | Booked seats / total seats |
| `GET` | `/api/analytics/top-movies` | Top movies by tickets sold |
| `GET` | `/api/analytics/peak-time-slots` | Bookings grouped by show hour (UTC) |

---

## Collections

| Collection | What is in it | Written by |
|---|---|---|
| `movies` | Title, language, genres, cities | `seeds.py` |
| `theaters` | Name, city, screens, seat layout | `seeds.py` |
| `shows` | One screening. Every seat is embedded on this document | `seeds.py`, then the app and `query.py` / `race_test.py` update seat status |
| `bookings` | One customer booking. This is the collection CDC watches | the app, `query.py`, `race_test.py` |
| `holds` | Unpaid 5-minute locks. TTL deletes them | the app, on startup |

---

## Every file

### `requirements.txt`

Python packages: FastAPI, Uvicorn, Motor, PyMongo, python-dotenv, Pydantic, and confluent-kafka.

```powershell
pip install -r requirements.txt
```

Run this once after creating the virtual environment, and again if the file changes.

### `example.env`

A template. It has placeholders, not real passwords.

```powershell
copy example.env .env
```

Then fill `.env`. Scripts read `.env` from the `assignments` folder. `example.env` itself is never loaded.

### `seeds.py`

Inserts the sample catalog: movies, three theaters (Delhi, Mumbai, Bengaluru), and 12 shows. Each show gets a full seat map, all `AVAILABLE`.

```powershell
python seeds.py
```

It clears `movies`, `theaters`, and `shows` first (`RESET_COLLECTIONS = True` at the top of the file), then inserts fresh documents. It does **not** delete `bookings` or `holds`. After it runs, open Compass and check the `cinebook` database. A sample line prints the seat count for show `SHW-000512`.

Run this before the app if the database is empty. Running it again resets seat maps, so seats that were booked on those shows go back to `AVAILABLE` even if a booking document still exists.

### `create_indexes.py`

Creates the indexes used by city browse and the analytics queries.

```powershell
python create_indexes.py
```

| Collection | Index |
|---|---|
| `shows` | `city`, `movie_id`, `start_time` |
| `bookings` | `status` + `movie_id` |
| `bookings` | `status` + `show_id` |
| `bookings` | `status` + `created_at` |
| `bookings` | `mobile_number` + `created_at` |
| `movies` | `cities`, `language`, `genres` |

Safe to run more than once. Existing indexes with the same name are kept. The hold TTL index is separate: `app.py` creates it on startup.

### `app.py`

The server. It is the API, the seat-hold logic, and the host for the static UI.

```powershell
uvicorn app:app --reload --port 8000
```

On startup it connects to Atlas, makes sure `bookings` and `holds` exist, creates the TTL index on `holds.expireAt`, and frees any seats whose hold has already disappeared.

Do not run it with `python app.py`. Uvicorn is the process that serves it.

### `static/index.html`

The page. Sections for movies, shows, seats, payout, the ticket, My bookings, and Analytics. You do not open this file from disk. `app.py` serves it at `/`.

### `static/js/app.js`

The browser logic: city filter, seat selection, the 5-minute countdown, pay, cancel, and the analytics tables. It only calls the routes in `app.py`. Refresh the browser after you change it. Uvicorn reload does not apply to static files the way it does to Python, but a normal browser refresh picks up the new file.

### `static/css/styles.css`

Layout and the status colours: green for `BOOKED`, gold for `AWAITING_PAYMENT`, muted for `EXPIRED`, red for `CANCELLED`. Refresh the browser after a change.

### `analytics_pipelines.py`

The four aggregation pipelines. Nothing runs this file by itself. `app.py` and `run_analytics.py` import it.

| Function | Question it answers | Collection |
|---|---|---|
| `revenue_per_movie_pipeline` | How much money per movie | `bookings` |
| `occupancy_per_show_pipeline` | What percent of seats are `BOOKED` | `shows` |
| `top_movies_pipeline` | Top movies by tickets sold | `bookings` |
| `peak_time_slots_pipeline` | Which show hour has the most bookings | `bookings` joined to `shows` |

Revenue, top movies, and peak hours count only bookings with `status: "BOOKED"`. Held seats are not counted as occupied.

### `run_analytics.py`

Prints those four pipelines in the terminal.

```powershell
python run_analytics.py
```

The app does not need to be running. You need some `BOOKED` rows first, from the UI or from `query.py`, or the booking reports will be empty. Occupancy still prints, because it reads seats on shows.

The same numbers are on the Analytics page in the browser, loaded from `GET /api/analytics/summary`.

### `mongo_analytical_agg_queries.txt`

The same four pipelines as JSON you can paste into MongoDB Compass (Aggregations tab).

- Pipelines 1, 3, and 4 run on `bookings`
- Pipeline 2 runs on `shows`

This file is not a script. There is no `python` command for it.

### `query.py`

Creates random paid bookings straight in Atlas, skipping the 5-minute hold. Each attempt takes 1 to 5 free seats on a random show and inserts a `BOOKED` / `PAID` document. If the seats were just taken, that attempt is skipped.

```powershell
python query.py
python query.py --count 20
python query.py --count 10 --min-seats 2 --max-seats 5
```

`--count` defaults to 15. Seat counts must stay between 1 and 5. Useful when you want analytics and CDC events without clicking through the UI. Each insert shows up on the CDC topic.

### `race_test.py`

Two bookings try to take seat `D5` on show `SHW-000502` at the same time. The script resets that seat to `AVAILABLE`, fires both writes together, and prints the result. One should succeed and one should be rejected.

```powershell
python race_test.py
```

Expected ending:

```
Wins: 1 (expected 1)
```

It writes a real `BOOKED` document for the winner, so the connector will publish that insert. The app does not need to be running.

### `consumer.py`

Reads `cdc.cinebook.bookings` and prints each message. It does not update MongoDB and it does not aggregate anything.

The Atlas Source connector is already running on Confluent Cloud. It watches `cinebook.bookings` and publishes every insert, update, and delete to that topic. Seat edits on a show are not on this topic.

In a second terminal, from `assignments`:

```powershell
.\.venv\Scripts\Activate.ps1
python consumer.py
```

You should see:

```
Listening on 'cdc.cinebook.bookings'. Ctrl+C to stop.
```

Then, in the app, pay for a seat, cancel a booking, or let a hold expire. The consumer prints the topic, partition, offset, key, and the JSON body.

Stop it with `Ctrl+C`.

- The first run of consumer group `cinebook-cdc-consumer` starts at the beginning of the topic, so older events print first.
- Later runs continue from the last committed offset and print only new events.
- To replay from the start, change `KAFKA_GROUP_ID` in `.env` and run it again.

If a Kafka variable is missing or still a `{{placeholder}}`, the script stops and names the variable.

---

## A full pass, in order

```powershell
cd assignments
.\.venv\Scripts\Activate.ps1
python seeds.py
python create_indexes.py
uvicorn app:app --reload --port 8000
```

Second terminal:

```powershell
cd assignments
.\.venv\Scripts\Activate.ps1
python consumer.py
```

Third terminal, when you want sample paid bookings or the reports without the browser:

```powershell
cd assignments
.\.venv\Scripts\Activate.ps1
python query.py --count 10
python run_analytics.py
python race_test.py
```
