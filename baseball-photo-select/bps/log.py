"""Logging setup: console + rotating file in base_dir/logs (spec 02 §1)."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(logs_dir: Path | None = None, level: str = "INFO") -> logging.Logger:
    """Configure the 'bps' logger once per process. Safe to call repeatedly."""
    global _CONFIGURED
    logger = logging.getLogger("bps")
    if _CONFIGURED:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            logs_dir / "bps.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    _CONFIGURED = True
    return logger


def get_logger(name: str = "bps") -> logging.Logger:
    return logging.getLogger(name)
