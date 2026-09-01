from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import hash_password, verify_password

def get_all_users(db: Session):
    return db.scalars(
        select(User)
    ).all()


def get_user_by_id(
    db: Session,
    user_id: int
):
    return db.get(User, user_id)


def create_user(
    db: Session,
    user_data: UserCreate
):
    hashed_password = hash_password(
        user_data.password
    )

    new_user = User(
        name=user_data.name,
        email=user_data.email,
        password_hash=hashed_password
    )

    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return new_user

    except IntegrityError:
        db.rollback()
        raise

def get_user_todos(
    db: Session,
    user_id: int
):
    user = db.get(User, user_id)

    if user is None:
        return None

    return user.todos

def get_user_by_email(
    db: Session,
    email: str
):
    return db.scalar(
        select(User).where(
            User.email == email
        )
    )

def authenticate_user(
    db: Session,
    email: str,
    password: str
):
    user = get_user_by_email(
        db,
        email
    )

    if user is None:
        return None

    if user.password_hash is None:
        return None

    if not verify_password(
        password,
        user.password_hash
    ):
        return None

    return user