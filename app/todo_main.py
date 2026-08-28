from fastapi import FastAPI,HTTPException
from pydantic import BaseModel

app = FastAPI()

todos = []

class TodoCreate(BaseModel):
    title: str
    completed: bool = False

class TodoUpdate(BaseModel):
    title:str | None = None
    completed:str | None = None

@app.post("/todos")
def create_todo(todo: TodoCreate):

    new_todo = {
        "id": len(todos) + 1,
        "title": todo.title,
        "completed": todo.completed
    }

    todos.append(new_todo)
    return new_todo


@app.get("/todos")
def get_todos():
    return todos

@app.get("/todos/{todo_id}")
def get_todo(todo_id:int):
    
    for todo in todos:
        if todo["id"] == todo_id:
            return todo
        
    raise HTTPException(
        status_code=404,
        detail="User not found"
        )

@app.patch("/todos/{todo_id}")
def update_todo(todo_id: int , data: TodoUpdate):

    for todo in todos:
        if todo["id"] == todo_id:
            if data.title is not None:
                todo["title"] = data.title
            if data.completed is not None:
                todo["completed"] = data.completed

            return todo

    raise HTTPException(
        status_code=404,
        detail="Todo not found"
    )

@app.delete("/todos/{todo_id}")
def delete_todo(todo_id:int):
    for todo in todos:
        if todo["id"] == todo_id:
            todos.remove(todo)
            return {
                "message": "Todo deleted successfully"
            }

    raise HTTPException(
        status_code=404,
        detail="Todo not found"
    )