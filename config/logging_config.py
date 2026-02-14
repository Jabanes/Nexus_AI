import logging
import logging.config
import os
import sys
from src.core.context import get_context

# Try to import colorlog, fall back to standard logging if not available
try:
    import colorlog
    COLORLOG_AVAILABLE = True
except ImportError:
    COLORLOG_AVAILABLE = False


class ContextFilter(logging.Filter):
    """
    Injects context vars (tenant_id, request_id) into every log record.
    """
    def filter(self, record):
        ctx = get_context()
        record.request_id = ctx["request_id"]
        record.tenant_id = ctx["tenant_id"]
        return True


class ColoredContextFormatter(logging.Formatter):
    """
    Custom formatter that adds beautiful color coding to logs.
    - Green for INFO
    - Red for ERROR
    - Cyan for Tenant IDs
    - Yellow for WARNING
    - Magenta for DEBUG
    """
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[95m',      # Magenta
        'INFO': '\033[92m',       # Green
        'WARNING': '\033[93m',    # Yellow
        'ERROR': '\033[91m',      # Red
        'CRITICAL': '\033[91m\033[1m',  # Bold Red
        'TENANT': '\033[96m',     # Cyan
        'RESET': '\033[0m',       # Reset
        'BOLD': '\033[1m',        # Bold
        'DIM': '\033[2m',         # Dim
    }

    def format(self, record):
        # Get the base formatted message
        log_fmt = (
            f"{self.COLORS['DIM']}[%(asctime)s]{self.COLORS['RESET']} "
            f"%(level_color)s[%(levelname)-8s]{self.COLORS['RESET']} "
            f"{self.COLORS['TENANT']}[T: %(tenant_id)s]{self.COLORS['RESET']} "
            f"{self.COLORS['DIM']}[R: %(request_id)s]{self.COLORS['RESET']} "
            f"%(name)s: %(message)s"
        )
        
        # Add level-specific color
        level_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        record.level_color = level_color
        
        formatter = logging.Formatter(log_fmt, datefmt="%H:%M:%S")
        return formatter.format(record)


def setup_logging():
    """
    Configures system-wide logging based on ENV variables.
    
    Environment Variables:
        LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: INFO)
        LOG_FORMAT: TEXT, JSON, COLOR (default: COLOR if colorlog available, else TEXT)
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "COLOR" if COLORLOG_AVAILABLE else "TEXT").upper()

    # Define formatters based on format type
    if log_format == "JSON":
        # Production-ready structured logging
        format_str = '{"time": "%(asctime)s", "level": "%(levelname)s", "tenant": "%(tenant_id)s", "req_id": "%(request_id)s", "logger": "%(name)s", "msg": "%(message)s"}'
        formatter_class = "standard"
    elif log_format == "COLOR" and COLORLOG_AVAILABLE:
        # Beautiful colored logs for local development (using colorlog)
        formatter_class = "colored_colorlog"
    elif log_format == "COLOR":
        # Fallback to custom ANSI color formatter
        formatter_class = "colored_custom"
    else:
        # Plain text format
        format_str = '[%(asctime)s] [%(levelname)-8s] [T: %(tenant_id)s] [R: %(request_id)s] %(name)s: %(message)s'
        formatter_class = "standard"

    # Build logging configuration
    formatters_config = {
        "standard": {
            "format": format_str if 'format_str' in locals() else '[%(asctime)s] [%(levelname)s] [T: %(tenant_id)s] %(name)s: %(message)s',
            "datefmt": "%H:%M:%S"
        },
        "colored_custom": {
            "()": ColoredContextFormatter,
        }
    }

    # Add colorlog formatter if available
    if COLORLOG_AVAILABLE:
        formatters_config["colored_colorlog"] = {
            "()": "colorlog.ColoredFormatter",
            "format": "%(log_color)s[%(asctime)s] [%(levelname)-8s]%(reset)s %(cyan)s[T: %(tenant_id)s]%(reset)s %(blue)s[R: %(request_id)s]%(reset)s %(name)s: %(message)s",
            "datefmt": "%H:%M:%S",
            "log_colors": {
                'DEBUG': 'purple',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            },
        }

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "context_filter": {
                "()": ContextFilter
            }
        },
        "formatters": formatters_config,
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": formatter_class,
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
            "httpx": {"level": "WARNING"},
        }
    }

    logging.config.dictConfig(logging_config)
    
    # Log a startup message to confirm colored logging is active
    logger = logging.getLogger("nexus.logging")
    logger.info(f"Logging initialized: Level={log_level}, Format={log_format}")

    # Suppress noisy third-party logs
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)
    logging.getLogger("websockets.server").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)