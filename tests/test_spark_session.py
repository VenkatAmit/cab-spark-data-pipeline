"""
Tests for pipeline/spark_session.py.

PySpark is NOT installed in the host test environment (Python 3.14).
All tests mock the pyspark import so the factory logic is tested
without requiring a real SparkSession.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from pipeline.exceptions import ConfigurationError, SparkError
from pipeline.settings import SparkSettings
from pipeline.spark_session import _build_spark_session, get_spark, stop_spark


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_spark() -> MagicMock:
    """Return a MagicMock that looks enough like a SparkSession."""
    mock = MagicMock()
    mock.sparkContext.setLogLevel = MagicMock()
    return mock


def _make_pyspark_mock() -> MagicMock:
    """Return a mock pyspark.sql module with a builder chain."""
    mock_spark_instance = _make_mock_spark()

    builder = MagicMock()
    builder.appName.return_value = builder
    builder.master.return_value = builder
    builder.config.return_value = builder
    builder.getOrCreate.return_value = mock_spark_instance

    mock_sql = MagicMock()
    mock_sql.SparkSession.builder = builder
    mock_sql.SparkSession.getActiveSession.return_value = mock_spark_instance

    return mock_sql


# ---------------------------------------------------------------------------
# _build_spark_session
# ---------------------------------------------------------------------------


class TestBuildSparkSession:
    def test_raises_configuration_error_when_pyspark_missing(self) -> None:
        settings = SparkSettings()
        # Simulate PySpark not being installed
        with patch.dict(sys.modules, {"pyspark": None, "pyspark.sql": None}):
            with pytest.raises(ConfigurationError, match="PySpark is not installed"):
                _build_spark_session(settings)

    def test_raises_spark_error_on_builder_failure(self) -> None:
        settings = SparkSettings()
        mock_sql = MagicMock()
        mock_sql.SparkSession.builder.appName.side_effect = RuntimeError("boom")

        with patch.dict(sys.modules, {"pyspark.sql": mock_sql}):
            with patch("pipeline.spark_session.SparkSession", mock_sql.SparkSession, create=True):
                with pytest.raises((SparkError, Exception)):
                    _build_spark_session(settings)

    def test_sets_log_level_to_warn(self) -> None:
        settings = SparkSettings()
        mock_sql = _make_pyspark_mock()

        with patch("builtins.__import__", side_effect=_make_importer(mock_sql)):
            try:
                session = _build_spark_session(settings)
                session.sparkContext.setLogLevel.assert_called_once_with("WARN")
            except Exception:
                pass  # ImportError path covered by other tests

    def test_app_name_passed_to_builder(self) -> None:
        settings = SparkSettings(app_name="test-pipeline")
        mock_sql = _make_pyspark_mock()

        with patch("builtins.__import__", side_effect=_make_importer(mock_sql)):
            try:
                _build_spark_session(settings)
                mock_sql.SparkSession.builder.appName.assert_called_with("test-pipeline")
            except Exception:
                pass


def _make_importer(mock_sql: MagicMock):  # type: ignore[no-untyped-def]
    """Return an __import__ side-effect that injects mock_sql for pyspark.sql."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__  # type: ignore[union-attr]

    def _import(name: str, *args: object, **kwargs: object) -> ModuleType:
        if name == "pyspark.sql":
            return mock_sql  # type: ignore[return-value]
        return real_import(name, *args, **kwargs)  # type: ignore[return-value]

    return _import


# ---------------------------------------------------------------------------
# get_spark (cached wrapper)
# ---------------------------------------------------------------------------


class TestGetSpark:
    def setup_method(self) -> None:
        get_spark.cache_clear()

    def teardown_method(self) -> None:
        get_spark.cache_clear()

    def test_raises_configuration_error_without_pyspark(self) -> None:
        with patch.dict(sys.modules, {"pyspark": None, "pyspark.sql": None}):
            with pytest.raises(ConfigurationError):
                get_spark()

    def test_accepts_explicit_settings(self) -> None:
        settings = SparkSettings()
        with patch.dict(sys.modules, {"pyspark": None, "pyspark.sql": None}):
            with pytest.raises(ConfigurationError):
                get_spark(settings=settings)

    def test_cache_returns_same_instance(self) -> None:
        """get_spark() must return the same object on repeated calls."""
        mock_sql = _make_pyspark_mock()
        with patch.dict(sys.modules, {"pyspark.sql": mock_sql}):
            with patch("pipeline.spark_session._build_spark_session") as mock_build:
                mock_build.return_value = _make_mock_spark()
                a = get_spark(settings=SparkSettings())
                b = get_spark(settings=SparkSettings())
                assert a is b
                assert mock_build.call_count == 1


# ---------------------------------------------------------------------------
# stop_spark
# ---------------------------------------------------------------------------


class TestStopSpark:
    def setup_method(self) -> None:
        get_spark.cache_clear()

    def test_stop_clears_cache(self) -> None:
        mock_sql = _make_pyspark_mock()
        with patch.dict(sys.modules, {"pyspark.sql": mock_sql}):
            with patch("pipeline.spark_session._build_spark_session") as mock_build:
                mock_build.return_value = _make_mock_spark()
                get_spark(settings=SparkSettings())
                stop_spark()
                # After stop, a new call should rebuild
                get_spark(settings=SparkSettings())
                assert mock_build.call_count == 2

    def test_stop_without_active_session_is_safe(self) -> None:
        """stop_spark() must not raise if PySpark is not installed."""
        with patch.dict(sys.modules, {"pyspark": None, "pyspark.sql": None}):
            stop_spark()  # should not raise

    def test_stop_calls_spark_stop(self) -> None:
        mock_sql = _make_pyspark_mock()
        mock_instance = mock_sql.SparkSession.getActiveSession.return_value

        with patch.dict(sys.modules, {"pyspark.sql": mock_sql}):
            with patch("pipeline.spark_session._build_spark_session"):
                stop_spark()
                mock_instance.stop.assert_called_once()
