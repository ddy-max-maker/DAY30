from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.schemas.user import UserResponse
from app.database.database import get_db
from app.schemas.todo import (
    TodoCreate,
    TodoResponse,
    TodoUpdate,
)
from app.services import todo_service
from app.services import todo_service, user_service

router = APIRouter(
    prefix="/todos",
    tags=["Todos"]
)
from app.dependencies.auth import get_current_user
from app.models.user import User

@router.get(
    "",
    response_model=list[TodoResponse]
)
def get_todos(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return todo_service.get_todos_by_user(
        db,
        current_user.id
    )


@router.get(
    "/{todo_id}",
    response_model=TodoResponse
)
def get_todo(
    todo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    todo = todo_service.get_todo_by_id(
        db,
        todo_id
    )

    if todo is None:
        raise HTTPException(
            status_code=404,
            detail="Todo not found"
        )

    if todo.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot access this Todo"
        )

    return todo

@router.post(
    "",
    response_model=TodoResponse,
    status_code=201
)
@router.post(
    "",
    response_model=TodoResponse,
    status_code=201
)
def create_todo(
    todo_data: TodoCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    

    try:
        return todo_service.create_todo(
                db,
                todo_data,
                current_user.id
            )

    except IntegrityError:
        raise HTTPException(
            status_code=400,
            detail="Invalid todo data"
        )

@router.patch(
    "/{todo_id}",
    response_model=TodoResponse
)
def update_todo(
    todo_id: int,
    todo_data: TodoUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    todo = todo_service.get_todo_by_id(
        db,
        todo_id
    )

    if todo is None:
        raise HTTPException(
            status_code=404,
            detail="Todo not found"
        )

    if todo.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot modify this Todo"
        )

    return todo_service.update_todo(
        db,
        todo,
        todo_data
    )


@router.delete("/{todo_id}")
def delete_todo(
    todo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    todo = todo_service.get_todo_by_id(
        db,
        todo_id
    )

    if todo is None:
        raise HTTPException(
            status_code=404,
            detail="Todo not found"
        )

    if todo.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot delete this Todo"
        )

    todo_service.delete_todo(
        db,
        todo
    )

    return {
        "message": "Todo deleted successfully"
    }

@router.get(
    "/{todo_id}/user",
    response_model=UserResponse
)
def get_todo_user(
    todo_id: int,
    db: Session = Depends(get_db)
):
    user = todo_service.get_todo_user(
        db,
        todo_id
    )

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Todo or User not found"
        )

    return user