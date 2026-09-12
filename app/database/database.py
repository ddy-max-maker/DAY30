from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


database_url = URL.create(
    drivername="mysql+pymysql",
    username=settings.DB_USER,
    password=settings.DB_PASSWORD,
    host=settings.DB_HOST,
    port=settings.DB_PORT,
    database=settings.DB_NAME,
)


engine = create_engine(
    database_url,
    echo=False,

    pool_size=10,

    max_overflow=20,

    pool_pre_ping=True
)


SessionLocal = sessionmaker(
    bind=engine
)


def get_db() -> Iterator[Session]:

    with SessionLocal() as db:
        yield db