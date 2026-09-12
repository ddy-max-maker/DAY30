from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.config import ALGORITHM, SECRET_KEY
from app.database.database import get_db
from app.exceptions.errors import AuthError, TokenInvalidError, TokenExpiredError, UserNotFoundError
from app.models.user import User

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        user_id = payload.get("sub")

        if user_id is None:
            raise TokenInvalidError()

        user_id = int(user_id)
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError() from None
    except (InvalidTokenError, TypeError, ValueError):
        raise TokenInvalidError() from None

    user = db.get(User, user_id)

    if user is None:
        raise UserNotFoundError(message="当前用户不存在")

    return user
