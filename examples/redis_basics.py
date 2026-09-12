"""Demonstrate basic Redis read and write operations."""

from app.database.redis import redis_client


def main() -> None:
    print("Redis available:", redis_client.ping())
    redis_client.set("name", "Tom")
    print("Stored name:", redis_client.get("name"))


if __name__ == "__main__":
    main()
