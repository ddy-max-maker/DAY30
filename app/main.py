from fastapi import FastAPI

from app.routers.todo import router as todo_router
from app.routers.user import router as user_router


app = FastAPI(
    title="Backend Learning API"
)


app.include_router(todo_router)
app.include_router(user_router)