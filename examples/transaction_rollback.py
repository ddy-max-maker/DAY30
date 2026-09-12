"""Demonstrate rolling back a transaction after an error."""

from app.database.database import SessionLocal
from app.models.todo import Todo


def main() -> None:
    with SessionLocal() as db:
        try:
            todo = Todo(title="Transaction Todo", completed=False)
            db.add(todo)
            db.flush()
            print("Todo 已 flush，id =", todo.id)

            raise RuntimeError("模拟后续操作失败")
        except RuntimeError as exc:
            print("发生异常：", exc)
            db.rollback()
            print("事务已 rollback")


if __name__ == "__main__":
    main()
