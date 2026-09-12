from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.dependencies.auth import get_current_user
from app.exceptions.errors import PermissionDeniedError, TodoNotFoundError
from app.models.todo import Todo
from app.models.user import User
from app.schemas.common import ResponseModel
from app.schemas.todo import (
    TodoCreate,
    TodoCursorResponse,
    TodoPageResponse,
    TodoResponse,
    TodoUpdate,
)
from app.services import todo_service

router = APIRouter(prefix="/todos", tags=["Todos"])


def _get_owned_todo(db: Session, todo_id: int, user_id: int) -> Todo:
    todo = todo_service.get_todo_by_id(db, todo_id)

    if todo is None:
        raise TodoNotFoundError()

    if todo.user_id != user_id:
        raise PermissionDeniedError(message="无权访问该 Todo")

    return todo


@router.get(
    "/{todo_id}",
    response_model=ResponseModel[TodoResponse],
)
def get_todo(
    todo_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[TodoResponse]:
    todo = _get_owned_todo(db, todo_id, current_user.id)
    return ResponseModel(data=TodoResponse.model_validate(todo))


@router.get(
    "/cursor",
    response_model=ResponseModel[TodoCursorResponse],
)
def get_todos_cursor(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    last_id: Annotated[int, Query(ge=0)] = 0,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel[TodoCursorResponse]:
    return ResponseModel(
        data=todo_service.get_todos_by_cursor(db, current_user.id, last_id, page_size)
    )


@router.get(
    "",
    response_model=ResponseModel[TodoPageResponse],
)
def get_todos(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel[TodoPageResponse]:
    return ResponseModel(
        data=todo_service.get_todos_by_user(db, current_user.id, page, page_size)
    )


@router.post(
    "",
    response_model=ResponseModel[TodoResponse],
)
def create_todo(
    todo_data: TodoCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[TodoResponse]:
    todo = todo_service.create_todo(db, todo_data, current_user.id)
    return ResponseModel(data=TodoResponse.model_validate(todo))


@router.patch(
    "/{todo_id}",
    response_model=ResponseModel[TodoResponse],
)
def update_todo(
    todo_id: int,
    todo_data: TodoUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[TodoResponse]:
    todo = _get_owned_todo(db, todo_id, current_user.id)
    updated = todo_service.update_todo(db, todo, todo_data)
    return ResponseModel(data=TodoResponse.model_validate(updated))


@router.delete(
    "/{todo_id}",
    response_model=ResponseModel[dict],
)
def delete_todo(
    todo_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[dict]:
    todo = _get_owned_todo(db, todo_id, current_user.id)
    todo_service.delete_todo(db, todo)
    return ResponseModel(data={"message": "Todo 删除成功"})
