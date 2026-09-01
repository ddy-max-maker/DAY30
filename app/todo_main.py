from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.schemas.todo import TodoCreate, TodoResponse, TodoUpdate
from app.database.database import get_db
from app.models.todo import Todo
from fastapi import Depends, FastAPI, HTTPException

app = FastAPI()


@app.get(
        
    "/todos",
    response_model=list[TodoResponse]
)
def get_todos(
    db: Session = Depends(get_db)
):
    todos = db.scalars(
        select(Todo)
    ).all()

    return todos

@app.get(
    "/todos/{todo_id}",
    response_model=TodoResponse
)
def get_todo(
    todo_id: int,
    db: Session = Depends(get_db)
):
    todo = db.get(Todo, todo_id)

    if todo is None:
        raise HTTPException(
            status_code=404,
            detail="Todo not found"
        )

    return todo

@app.post(
    "/todos",
    response_model=TodoResponse,
    status_code=201
)
def create_todo(
    todo_data: TodoCreate,
    db: Session = Depends(get_db)
):
    new_todo = Todo(
        title=todo_data.title,
        completed=todo_data.completed,
        user_id=todo_data.user_id
    )

    db.add(new_todo)
    db.commit()
    db.refresh(new_todo)

    return new_todo

@app.patch(
    "/todos/{todo_id}",
    response_model=TodoResponse
)
def update_todo(
    todo_id: int,
    todo_data: TodoUpdate,
    db: Session = Depends(get_db)
):
    todo = db.get(Todo, todo_id)

    if todo is None:
        raise HTTPException(
            status_code=404,
            detail="Todo not found"
        )

    if todo_data.title is not None:
        todo.title = todo_data.title

    if todo_data.completed is not None:
        todo.completed = todo_data.completed

    if todo_data.user_id is not None:
        todo.user_id = todo_data.user_id

    db.commit()
    db.refresh(todo)

    return todo

@app.delete("/todos/{todo_id}")
def delete_todo(
    todo_id: int,
    db: Session = Depends(get_db)
):
    todo = db.get(Todo, todo_id)

    if todo is None:
        raise HTTPException(
            status_code=404,
            detail="Todo not found"
        )

    db.delete(todo)
    db.commit()

    return {
        "message": "Todo deleted successfully"
    }