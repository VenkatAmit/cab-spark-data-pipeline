"""
Tests for pipeline/bronze/validator.py.

Great Expectations is mocked throughout — no real Postgres instance
or GX context is needed to run these tests.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipeline.bronze.validator import (
    DEFAULT_TRIPS_EXPECTATIONS,
    DEFAULT_ZONES_EXPECTATIONS,
    Expectation,
    ExpectationResult,
    GXValidator,
    TripsDateRangeExpectation,
    TripsNotNullExpectation,
    TripsPositiveAmountExpectation,
    ValidationReport,
    ZonesLocationIdExpectation,
)
from pipeline.exceptions import (
    ConfigurationError,
    ExpectationFailedError,
    ValidationError,
)
from pipeline.settings import PostgresSettings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> PostgresSettings:
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    return PostgresSettings(
        host="localhost",
        port=5432,
        db="test_db",
        user="test_user",
        password="test",  # type: ignore[arg-type]
    )


@pytest.fixture()
def validator(settings: PostgresSettings) -> GXValidator:
    return GXValidator(settings=settings)


def _pass_result(name: str = "test_expectation") -> dict[str, Any]:
    return {"success": True, "result": {"unexpected_count": 0}}


def _fail_result(name: str = "test_expectation", count: int = 5) -> dict[str, Any]:
    return {"success": False, "result": {"unexpected_count": count}}


def _make_gx_validator(passing: bool = True) -> MagicMock:
    """Return a mock GX Validator where all expectations pass or fail."""
    mock = MagicMock()
    result = _pass_result() if passing else _fail_result()
    mock.expect_column_values_to_not_be_null.return_value = result
    mock.expect_column_values_to_be_between.return_value = result
    return mock


# ---------------------------------------------------------------------------
# Expectation ABC contract
# ---------------------------------------------------------------------------


class TestExpectationABC:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            Expectation()  # type: ignore[abstract]

    def test_concrete_subclass_works(self) -> None:
        class MyExpectation(Expectation):
            @property
            def name(self) -> str:
                return "my_expectation"

            def run(
                self,
                validator: Any,
                table: str,
                partition_date: date | None,
            ) -> ExpectationResult:
                return ExpectationResult(
                    expectation_name=self.name,
                    success=True,
                )

        e = MyExpectation()
        assert e.name == "my_expectation"
        result = e.run(MagicMock(), "raw_trips", None)
        assert result.success


# ---------------------------------------------------------------------------
# ExpectationResult
# ---------------------------------------------------------------------------


class TestExpectationResult:
    def test_frozen(self) -> None:
        r = ExpectationResult(expectation_name="test", success=True)
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]

    def test_defaults(self) -> None:
        r = ExpectationResult(expectation_name="test", success=True)
        assert r.failure_count == 0
        assert r.observed_value is None
        assert r.details == ""


# ---------------------------------------------------------------------------
# Built-in expectations — unit tests with mocked GX validator
# ---------------------------------------------------------------------------


class TestTripsNotNullExpectation:
    def test_name(self) -> None:
        assert TripsNotNullExpectation().name == "trips_critical_columns_not_null"

    def test_passes_when_all_not_null(self) -> None:
        gx_v = _make_gx_validator(passing=True)
        result = TripsNotNullExpectation().run(gx_v, "raw_trips", date(2024, 1, 1))
        assert result.success
        assert result.failure_count == 0

    def test_fails_when_nulls_present(self) -> None:
        gx_v = _make_gx_validator(passing=False)
        result = TripsNotNullExpectation().run(gx_v, "raw_trips", date(2024, 1, 1))
        assert not result.success
        assert result.failure_count > 0

    def test_checks_all_critical_columns(self) -> None:
        gx_v = _make_gx_validator(passing=True)
        TripsNotNullExpectation().run(gx_v, "raw_trips", date(2024, 1, 1))
        assert (
            gx_v.expect_column_values_to_not_be_null.call_count
            == len(TripsNotNullExpectation.CRITICAL_COLUMNS)
        )


class TestTripsPositiveAmountExpectation:
    def test_name(self) -> None:
        assert TripsPositiveAmountExpectation().name == "trips_amounts_non_negative"

    def test_passes_when_amounts_positive(self) -> None:
        gx_v = _make_gx_validator(passing=True)
        result = TripsPositiveAmountExpectation().run(gx_v, "raw_trips", None)
        assert result.success

    def test_fails_when_negative_amounts(self) -> None:
        gx_v = _make_gx_validator(passing=False)
        result = TripsPositiveAmountExpectation().run(gx_v, "raw_trips", None)
        assert not result.success


class TestTripsDateRangeExpectation:
    def test_name(self) -> None:
        assert (
            TripsDateRangeExpectation().name
            == "trips_pickup_within_partition_month"
        )

    def test_skips_when_no_partition_date(self) -> None:
        gx_v = _make_gx_validator(passing=True)
        result = TripsDateRangeExpectation().run(gx_v, "raw_trips", None)
        assert result.success
        assert "skipped" in result.details
        gx_v.expect_column_values_to_be_between.assert_not_called()

    def test_passes_within_range(self) -> None:
        gx_v = _make_gx_validator(passing=True)
        result = TripsDateRangeExpectation().run(gx_v, "raw_trips", date(2024, 1, 1))
        assert result.success

    def test_fails_outside_range(self) -> None:
        gx_v = _make_gx_validator(passing=False)
        result = TripsDateRangeExpectation().run(gx_v, "raw_trips", date(2024, 1, 1))
        assert not result.success

    def test_details_contain_date_range(self) -> None:
        gx_v = _make_gx_validator(passing=True)
        result = TripsDateRangeExpectation().run(gx_v, "raw_trips", date(2024, 1, 1))
        assert "2024-01-01" in result.details


class TestZonesLocationIdExpectation:
    def test_name(self) -> None:
        assert ZonesLocationIdExpectation().name == "zones_location_id_positive"

    def test_passes_positive_ids(self) -> None:
        gx_v = _make_gx_validator(passing=True)
        result = ZonesLocationIdExpectation().run(gx_v, "raw_zones", None)
        assert result.success

    def test_fails_non_positive_ids(self) -> None:
        gx_v = _make_gx_validator(passing=False)
        result = ZonesLocationIdExpectation().run(gx_v, "raw_zones", None)
        assert not result.success


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------


class TestValidationReport:
    def test_success_true_when_all_pass(self) -> None:
        results = (
            ExpectationResult("e1", success=True),
            ExpectationResult("e2", success=True),
        )
        report = ValidationReport(table="raw_trips", partition_date=None, results=results)
        assert report.success

    def test_success_false_when_any_fails(self) -> None:
        results = (
            ExpectationResult("e1", success=True),
            ExpectationResult("e2", success=False, failure_count=3),
        )
        report = ValidationReport(table="raw_trips", partition_date=None, results=results)
        assert not report.success

    def test_failed_expectations_filtered(self) -> None:
        results = (
            ExpectationResult("e1", success=True),
            ExpectationResult("e2", success=False, failure_count=3),
        )
        report = ValidationReport(table="raw_trips", partition_date=None, results=results)
        assert len(report.failed_expectations) == 1
        assert report.failed_expectations[0].expectation_name == "e2"

    def test_total_failure_count(self) -> None:
        results = (
            ExpectationResult("e1", success=False, failure_count=2),
            ExpectationResult("e2", success=False, failure_count=5),
        )
        report = ValidationReport(table="raw_trips", partition_date=None, results=results)
        assert report.total_failure_count == 7

    def test_raise_if_failed_raises(self) -> None:
        results = (ExpectationResult("e1", success=False, failure_count=1),)
        report = ValidationReport(table="raw_trips", partition_date=None, results=results)
        with pytest.raises(ExpectationFailedError):
            report.raise_if_failed()

    def test_raise_if_failed_silent_on_success(self) -> None:
        results = (ExpectationResult("e1", success=True),)
        report = ValidationReport(table="raw_trips", partition_date=None, results=results)
        report.raise_if_failed()  # must not raise

    def test_frozen(self) -> None:
        results = (ExpectationResult("e1", success=True),)
        report = ValidationReport(table="raw_trips", partition_date=None, results=results)
        with pytest.raises(Exception):
            report.table = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GXValidator._run (integration of Expectation + ValidationReport)
# ---------------------------------------------------------------------------


class TestGXValidatorRun:
    def test_all_pass_returns_success_report(
        self, validator: GXValidator
    ) -> None:
        gx_v = _make_gx_validator(passing=True)
        expectations = [TripsNotNullExpectation()]

        with patch.object(validator, "_build_gx_validator", return_value=gx_v):
            report = validator._run("raw_trips", date(2024, 1, 1), expectations)

        assert report.success
        assert report.table == "raw_trips"

    def test_failed_expectation_in_report(self, validator: GXValidator) -> None:
        gx_v = _make_gx_validator(passing=False)
        expectations = [TripsNotNullExpectation()]

        with patch.object(validator, "_build_gx_validator", return_value=gx_v):
            report = validator._run("raw_trips", date(2024, 1, 1), expectations)

        assert not report.success
        assert len(report.failed_expectations) == 1

    def test_expectation_exception_wrapped(self, validator: GXValidator) -> None:
        broken = MagicMock(spec=Expectation)
        broken.name = "broken"
        broken.run.side_effect = RuntimeError("gx internal error")

        with patch.object(validator, "_build_gx_validator", return_value=MagicMock()):
            with pytest.raises(ValidationError, match="raised unexpectedly"):
                validator._run("raw_trips", None, [broken])


# ---------------------------------------------------------------------------
# GXValidator public API
# ---------------------------------------------------------------------------


class TestGXValidatorPublicAPI:
    def test_validate_trips_uses_default_expectations(
        self, validator: GXValidator
    ) -> None:
        gx_v = _make_gx_validator(passing=True)
        with patch.object(validator, "_build_gx_validator", return_value=gx_v):
            report = validator.validate_trips(partition_date=date(2024, 1, 1))
        assert len(report.results) == len(DEFAULT_TRIPS_EXPECTATIONS)

    def test_validate_zones_uses_default_expectations(
        self, validator: GXValidator
    ) -> None:
        gx_v = _make_gx_validator(passing=True)
        with patch.object(validator, "_build_gx_validator", return_value=gx_v):
            report = validator.validate_zones()
        assert len(report.results) == len(DEFAULT_ZONES_EXPECTATIONS)

    def test_validate_trips_custom_expectations(
        self, validator: GXValidator
    ) -> None:
        gx_v = _make_gx_validator(passing=True)
        custom = [ZonesLocationIdExpectation()]
        with patch.object(validator, "_build_gx_validator", return_value=gx_v):
            report = validator.validate_trips(expectations=custom)
        assert len(report.results) == 1

    def test_missing_gx_raises_configuration_error(
        self, validator: GXValidator
    ) -> None:
        with patch.dict("sys.modules", {"great_expectations": None}):
            with pytest.raises(ConfigurationError):
                validator.validate_trips()


# ---------------------------------------------------------------------------
# GXValidator.__repr__
# ---------------------------------------------------------------------------


class TestGXValidatorRepr:
    def test_repr_contains_host_and_db(self, validator: GXValidator) -> None:
        r = repr(validator)
        assert "localhost" in r
        assert "test_db" in r
