import logging


logging.basicConfig(
    level=logging.INFO,
    filename="logs/app.log",
    encoding="utf-8",
    format="%(asctime)s - %(levelname)s - %(message)s"
)


logger = logging.getLogger(__name__)