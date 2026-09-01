from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.todo import Todo
from app.schemas.todo import TodoCreate, TodoUpdate


def get_all_todos(db: Session):
    return db.scalars(
        select(Todo)
    ).all()


def get_todo_by_id(
    db: Session,
    todo_id: int
):
    return db.get(Todo, todo_id)


def create_todo(
    db: Session,
    todo_data: TodoCreate,
    user_id: int
):
    new_todo = Todo(
        title=todo_data.title,
        completed=todo_data.completed,
        user_id=user_id
    )

    try:
        db.add(new_todo)
        db.commit()
        db.refresh(new_todo)

        return new_todo

    except IntegrityError:
        db.rollback()
        raise


def update_todo(
    db: Session,
    todo: Todo,
    todo_data: TodoUpdate
):
    if todo_data.title is not None:
        todo.title = todo_data.title

    if todo_data.completed is not None:
        todo.completed = todo_data.completed

    if todo_data.user_id is not None:
        todo.user_id = todo_data.user_id

    db.commit()
    db.refresh(todo)

    return todo


def delete_todo(
    db: Session,
    todo: Todo
):
    db.delete(todo)
    db.commit()

def get_todo_user(
    db: Session,
    todo_id: int
):
    todo = db.get(Todo, todo_id)

    if todo is None:
        return None

    return todo.user

def get_todos_by_user(
    db: Session,
    user_id: int
):
    return db.scalars(
        select(Todo).where(
            Todo.user_id == user_id
        )
    ).all()