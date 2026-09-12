from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.database.database import get_db
from app.exceptions.errors import AuthError, BusinessError
from app.schemas.auth import CodeRequest, CodeVerify, TokenResponse, UserLogin
from app.schemas.common import ResponseModel
from app.schemas.user import UserCreate, UserResponse
from app.services import auth_service, user_service


router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/login",
    response_model=ResponseModel[TokenResponse],
)
def login(
    login_data: UserLogin,
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[TokenResponse]:

    user = auth_service.authenticate_user(
        db,
        login_data.email,
        login_data.password,
    )

    if user is None:
        raise AuthError(message="账号或密码错误")

    return ResponseModel(
        data=TokenResponse(
            access_token=create_access_token(user.id),
            token_type="bearer",
        )
    )


@router.post(
    "/code/request",
    response_model=ResponseModel[dict],
)
def request_verify_code(data: CodeRequest) -> ResponseModel[dict]:
    result = auth_service.create_verify_code(data.email)

    if result == "rate_limited":
        raise BusinessError(message="请求过于频繁，请稍后再试", code=10000)

    if result == "cooldown":
        raise BusinessError(message="请稍后再请求验证码", code=10000)

    return ResponseModel(data={"message": "验证码已发送"})


@router.post(
    "/code/verify",
    response_model=ResponseModel[dict],
)
def verify_code(data: CodeVerify) -> ResponseModel[dict]:
    result = auth_service.verify_code(data.email, data.code)

    if result == "expired":
        raise BusinessError(message="验证码已过期", code=10000)

    if result == "invalid":
        raise BusinessError(message="验证码错误", code=10000)

    return ResponseModel(data={"message": "验证成功"})


@router.post(
    "/register",
    response_model=ResponseModel[UserResponse],
)
def register(
    user_data: UserCreate,
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[UserResponse]:

    user = user_service.create_user(db, user_data)

    return ResponseModel(data=UserResponse.model_validate(user))
