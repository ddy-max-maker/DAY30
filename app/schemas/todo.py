from pydantic import BaseModel, ConfigDict


class TodoCreate(BaseModel):
    title: str
    completed: bool = False


class TodoResponse(BaseModel):
    id: int
    title: str
    completed: bool
    user_id: int | None

    model_config = ConfigDict(from_attributes=True)

class TodoUpdate(BaseModel):
    title: str | None = None
    completed: bool | None = None
    user_id: int | None = None