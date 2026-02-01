import logging
import logging.config
import os
import sys
from src.core.context import get_context

class ContextFilter(logging.Filter):
    """
    Injects context vars (tenant_id, request_id) into every log record.
    """
    def filter(self, record):
        ctx = get_context()
        record.request_id = ctx["request_id"]
        record.tenant_id = ctx["tenant_id"]
        return True

def setup_logging():
    """
    Configures system-wide logging based on ENV variables.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "TEXT").upper()

    # Define formats
    if log_format == "JSON":
        # Useful for production/cloud logging
        format_str = '{"time": "%(asctime)s", "level": "%(levelname)s", "tenant": "%(tenant_id)s", "req_id": "%(request_id)s", "msg": "%(message)s"}'
    else:
        # Useful for local development
        format_str = '[%(asctime)s] [%(levelname)s] [T: %(tenant_id)s] %(name)s: %(message)s'

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "context_filter": {
                "()": ContextFilter
            }
        },
        "formatters": {
            "standard": {
                "format": format_str,
                "datefmt": "%H:%M:%S"
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "standard",
                "filters": ["context_filter"],
                "level": log_level,
            },
        },
        "root": {
            "handlers": ["console"],
            "level": log_level,
        },
        # Silence noisy external libraries
        "loggers": {
            "uvicorn.access": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        }
    }

    logging.config.dictConfig(logging_config)