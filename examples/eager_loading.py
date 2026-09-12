"""Demonstrate eager loading of a user's todos."""

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database.database import SessionLocal
from app.models.user import User


def main() -> None:
    with SessionLocal() as db:
        users = db.scalars(select(User).options(selectinload(User.todos))).all()

        for user in users:
            print(f"User: {user.name}")
            for todo in user.todos:
                print(f"  Todo: {todo.title}")


if __name__ == "__main__":
    main()
