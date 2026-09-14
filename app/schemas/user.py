from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.user import UserRole


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    """更新用户信息。

    version 必填（乐观锁）：客户端必须携带 GET 时拿到的版本号，
    服务端 WHERE version = ? 条件更新，防止并发互相覆盖。
    """

    name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    version: int


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: UserRole
    version: int

    model_config = ConfigDict(from_attributes=True)