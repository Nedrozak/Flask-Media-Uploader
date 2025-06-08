from datetime import datetime
import logging
import logging.config
import os

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILENAME = os.path.join(LOG_DIR, f"app-{stamp}.log")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "standard": {
            # include timestamp, level, logger name, message, and source location
            "format": "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "INFO",
        },
        "file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "formatter": "standard",
            "level": "INFO",
            "filename": LOG_FILENAME,
            "when": "midnight",
            "backupCount": 7,  # keep last week’s logs
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
    "loggers": {
        "app": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "werkzeug": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


def setup_logging():
    logging.config.dictConfig(LOGGING)
