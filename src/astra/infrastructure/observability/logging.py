"""Structured logging configuration using structlog with human-readable dev format and JSON prod format."""

import logging
import sys
import structlog


def configure_logging(log_level: str = "INFO", env: str = "dev") -> None:
    """Configures structured logging across the application with dev/prod formatters."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    if env == "prod":
        renderer = structlog.processors.JSONRenderer()
        timestamper = structlog.processors.TimeStamper(fmt="iso")
    else:
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            pad_event_to=28,
        )
        timestamper = structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
