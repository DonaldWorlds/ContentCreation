import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT_DIR / "logs"
RECORDINGS_DIR = ROOT_DIR / "recordings"
CLIPS_DIR = ROOT_DIR / "clips"
RENDERS_DIR = ROOT_DIR / "renders"
CONTENT_DIR = ROOT_DIR / "content"
DB_PATH = ROOT_DIR / "zerino.db"

load_dotenv(ROOT_DIR / ".env")
ZERNIO_API_KEY = os.environ.get("ZERNIO_API_KEY", "")

_LOGGER_CONFIGURED = False


def get_logger(name: str = "zerino") -> logging.Logger:
    global _LOGGER_CONFIGURED

    logger = logging.getLogger(name)
    if _LOGGER_CONFIGURED:
        return logger

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("zerino")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    stream.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        LOGS_DIR / "zerino.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    root.addHandler(stream)
    root.addHandler(file_handler)

    _LOGGER_CONFIGURED = True
    return logger
