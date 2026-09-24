from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_admin
from app.exceptions.errors import PermissionDeniedError, UserNotFoundError
from app.models.user import User
from app.schemas.common import ResponseModel
from app.schemas.user import UserResponse, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=ResponseModel[UserResponse])
def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> ResponseModel[UserResponse]:
    return ResponseModel(data=UserResponse.model_validate(current_user))


@router.get("", response_model=ResponseModel[list[UserResponse]])
def get_users(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[list[UserResponse]]:
    """查看全部用户（仅 ADMIN）。"""
    users = [UserResponse.model_validate(u) for u in user_service.get_all_users(db)]
    return ResponseModel(data=users)


@router.get("/{user_id}", response_model=ResponseModel[UserResponse])
def get_user(
    user_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[UserResponse]:
    """查看用户详情（仅 ADMIN）。"""
    user = user_service.get_user_by_id(db, user_id)

    if user is None:
        raise UserNotFoundError()

    return ResponseModel(data=UserResponse.model_validate(user))


@router.put("/{user_id}", response_model=ResponseModel[UserResponse])
def update_user(
    user_id: int,
    update_data: UserUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[UserResponse]:
    """更新用户信息（只能更新自己的资料）。

    UserUpdate schema 不含 role 字段，客户端无法借此提权。
    """
    if user_id != current_user.id:
        raise PermissionDeniedError(message="只能修改自己的信息")

    user = user_service.update_user(db, user_id, update_data)
    return ResponseModel(data=user)
