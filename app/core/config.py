import os

from pydantic_settings import BaseSettings

ENV_FILE = f".env.{os.getenv('APP_ENV', 'dev')}"


class Settings(BaseSettings):
    APP_ENV: str = "development"

    DB_HOST: str

    DB_PORT: int = 3306

    DB_USER: str

    DB_PASSWORD: str

    DB_NAME: str

    SECRET_KEY: str

    ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    REDIS_HOST: str = "127.0.0.1"

    REDIS_PORT: int = 6379

    REDIS_DB: int = 0

    RABBITMQ_HOST: str = "127.0.0.1"

    RABBITMQ_PORT: int = 5672

    RABBITMQ_USER: str = "guest"

    RABBITMQ_PASSWORD: str = "guest"

    # MQ 连接开关：测试环境置为 false，避免单测强依赖真实 RabbitMQ
    RABBITMQ_ENABLED: bool = True

    class Config:
        env_file = ENV_FILE
        extra = "ignore"


settings = Settings()

DB_HOST = settings.DB_HOST
DB_PORT = settings.DB_PORT
DB_USER = settings.DB_USER
DB_PASSWORD = settings.DB_PASSWORD
DB_NAME = settings.DB_NAME

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

REDIS_HOST = settings.REDIS_HOST
REDIS_PORT = settings.REDIS_PORT
REDIS_DB = settings.REDIS_DB

RABBITMQ_HOST = settings.RABBITMQ_HOST
RABBITMQ_PORT = settings.RABBITMQ_PORT
RABBITMQ_USER = settings.RABBITMQ_USER
RABBITMQ_PASSWORD = settings.RABBITMQ_PASSWORD
RABBITMQ_ENABLED = settings.RABBITMQ_ENABLED
