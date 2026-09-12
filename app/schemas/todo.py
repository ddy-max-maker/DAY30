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


class TodoPageResponse(BaseModel):
    items: list[TodoResponse]
    page: int
    page_size: int
    total: int


class TodoCursorResponse(BaseModel):
    items: list[TodoResponse]
    next_cursor: int | None


class TodoUpdate(BaseModel):
    title: str | None = None
    completed: bool | None = None
