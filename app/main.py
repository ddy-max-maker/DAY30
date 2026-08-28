from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

users = []

class UserCreate(BaseModel):
    name: str
    age: int
    email: str

@app.get("/")
def home():
    return {
        "message": "Hello FastAPI"
    }



@app.post("/users")
def create_user(user: UserCreate):

    new_user = {
        "id": len(users) + 1,
        "name": user.name,
        "age": user.age,
        "email": user.email
    }

    users.append(new_user)

    return new_user

@app.get("/users")
def get_users():
    return users

@app.get("/users/{user_id}")
def get_user(user_id: int):

    for user in users:
        if user["id"] == user_id:
            break
            return user

    raise HTTPException(
    status_code=404,
    detail="User not found"
    )
    