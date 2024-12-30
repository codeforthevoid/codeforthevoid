import logging
from logging.handlers import RotatingFileHandler
import os
from .settings import LOG_LEVEL, LOG_FILE, BASE_DIR


def setup_logging():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            RotatingFileHandler(
                os.path.join(BASE_DIR, 'logs', LOG_FILE),
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5
            ),
            logging.StreamHandler()
        ]
    )

    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('websockets').setLevel(logging.WARNING)