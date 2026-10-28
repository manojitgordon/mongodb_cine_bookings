"""
consumer.py
-----------
Part E — listen to the CDC topic and print each message.

The MongoDB Atlas Source connector publishes cinebook.bookings
changes to cdc.cinebook.bookings. This script only reads that topic.

How to run (from the assignments folder, venv active)
    python consumer.py

Stop with Ctrl+C. The first run reads from the beginning of the topic.
Later runs continue from the last committed offset.
"""

import json
import os
import signal
import sys
from pathlib import Path

from confluent_kafka import Consumer, KafkaError, KafkaException
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def require_env(name: str) -> str:
    """Read a required env var. Exit if it is missing or still a placeholder."""
    value = (os.getenv(name) or "").strip()
    if not value or "{{" in value or "}}" in value:
        print(
            f"{name} is missing or still a placeholder.\n"
            "Add your Confluent Cloud values to assignments/.env "
            "(see the Kafka section in example.env), then run this again.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return value


def build_consumer() -> tuple[Consumer, str]:
    """Connect to Confluent Cloud and return the consumer plus the CDC topic."""
    topic = (os.getenv("KAFKA_TOPIC") or "cdc.cinebook.bookings").strip()
    consumer = Consumer(
        {
            "bootstrap.servers": require_env("KAFKA_BOOTSTRAP"),
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": require_env("KAFKA_API_KEY"),
            "sasl.password": require_env("KAFKA_API_SECRET"),
            "group.id": (os.getenv("KAFKA_GROUP_ID") or "cinebook-cdc-consumer").strip(),
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
            "client.id": "cinebook-consumer",
        }
    )
    return consumer, topic


def decode(raw: bytes | None):
    """CDC values are JSON bytes. Return the object, or the text if it is not JSON."""
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def consume(consumer: Consumer, topic: str) -> None:
    """Subscribe to the CDC topic and print every message as it arrives."""
    consumer.subscribe([topic])
    running = True
    seen = 0

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    print(f"Listening on '{topic}'. Ctrl+C to stop.\n")

    try:
        while running:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(msg.error())

            seen += 1
            key = msg.key().decode("utf-8", errors="replace") if msg.key() else None
            print(
                f"#{seen}  topic={msg.topic()}  partition={msg.partition()}  "
                f"offset={msg.offset()}  key={key}"
            )
            print(json.dumps(decode(msg.value()), indent=2, default=str))
            print()
    finally:
        print(f"Stopping. Printed {seen} message(s).")
        consumer.close()


def main() -> None:
    consumer, topic = build_consumer()
    try:
        consume(consumer, topic)
    except KafkaException as exc:
        print(f"Kafka error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
