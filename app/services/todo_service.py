from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.redis import redis_client
from app.models.todo import Todo
from app.schemas.todo import (
    TodoCreate,
    TodoCursorResponse,
    TodoPageResponse,
    TodoResponse,
    TodoUpdate,
)

CACHE_TTL_SECONDS = 60


def get_todo_by_id(db: Session, todo_id: int) -> Todo | None:
    return db.get(Todo, todo_id)


def create_todo(db: Session, todo_data: TodoCreate, user_id: int) -> Todo:
    new_todo = Todo(
        title=todo_data.title,
        completed=todo_data.completed,
        user_id=user_id,
    )

    try:
        db.add(new_todo)
        db.commit()
        db.refresh(new_todo)
        invalidate_todo_cache(user_id)
        return new_todo
    except IntegrityError:
        db.rollback()
        raise


def update_todo(db: Session, todo: Todo, todo_data: TodoUpdate) -> Todo:
    if todo_data.title is not None:
        todo.title = todo_data.title

    if todo_data.completed is not None:
        todo.completed = todo_data.completed

    db.commit()
    db.refresh(todo)

    if todo.user_id is not None:
        invalidate_todo_cache(todo.user_id)

    return todo


def delete_todo(db: Session, todo: Todo) -> None:
    user_id = todo.user_id
    db.delete(todo)
    db.commit()

    if user_id is not None:
        invalidate_todo_cache(user_id)


def get_todos_by_user(
    db: Session, user_id: int, page: int, page_size: int
) -> TodoPageResponse:
    cache_key = f"todos:user:{user_id}:page:{page}:size:{page_size}"
    cached_data = redis_client.get(cache_key)

    if cached_data is not None:
        return TodoPageResponse.model_validate_json(cached_data)

    statement = (
        select(Todo)
        .where(Todo.user_id == user_id)
        .order_by(Todo.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    todos = db.scalars(statement).all()
    total = db.scalar(
        select(func.count()).select_from(Todo).where(Todo.user_id == user_id)
    )
    result = TodoPageResponse(
        items=_to_responses(todos),
        page=page,
        page_size=page_size,
        total=total or 0,
    )
    redis_client.set(
        cache_key,
        result.model_dump_json(),
        ex=CACHE_TTL_SECONDS,
    )
    return result


def get_todos_by_cursor(
    db: Session, user_id: int, last_id: int, page_size: int
) -> TodoCursorResponse:
    statement = (
        select(Todo)
        .where(Todo.user_id == user_id, Todo.id > last_id)
        .order_by(Todo.id)
        .limit(page_size)
    )
    todos = db.scalars(statement).all()
    return TodoCursorResponse(
        items=_to_responses(todos),
        next_cursor=todos[-1].id if todos else None,
    )


def invalidate_todo_cache(user_id: int) -> None:
    keys = list(redis_client.scan_iter(match=f"todos:user:{user_id}:*"))
    if keys:
        redis_client.delete(*keys)


def _to_responses(todos: Sequence[Todo]) -> list[TodoResponse]:
    return [TodoResponse.model_validate(todo) for todo in todos]
