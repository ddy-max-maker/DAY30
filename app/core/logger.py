import logging
import os
from logging.handlers import RotatingFileHandler

from app.core.request_context import request_id_context

LOG_DIR = "logs"

os.makedirs(LOG_DIR, exist_ok=True)


logger = logging.getLogger("backend")

logger.setLevel(logging.INFO)


class RequestIdFilter(logging.Filter):
    def filter(self, record):

        record.request_id = request_id_context.get()

        return True


formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | request_id=%(request_id)s | %(message)s"
)


file_handler = RotatingFileHandler(
    "logs/app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)


file_handler.addFilter(RequestIdFilter())


file_handler.setFormatter(formatter)


if not logger.handlers:
    logger.addHandler(file_handler)
