"""Tests for logging configuration."""

from __future__ import annotations

import logging

from omnifetch.logging import configure_logging, get_logger, LOGGER_NAMESPACE


def test_configure_logging_sets_level_and_single_handler() -> None:
    logger = configure_logging("DEBUG")
    assert logger.name == LOGGER_NAMESPACE
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    assert logger.propagate is False


def test_configure_logging_unknown_level_defaults_to_info() -> None:
    logger = configure_logging("NOT_A_LEVEL")
    assert logger.level == logging.INFO


def test_get_logger_returns_base_and_child() -> None:
    assert get_logger().name == LOGGER_NAMESPACE
    assert get_logger("sub").name == f"{LOGGER_NAMESPACE}.sub"
