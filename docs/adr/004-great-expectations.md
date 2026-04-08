# ADR 004 — Great Expectations Pinned to 0.18.x

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** VenkatAmit

---

## Context

The Bronze validation layer uses Great Expectations (GX) to run data quality checks against the ingested `raw_trips` partition after each monthly ingest. GX released version 1.0 in mid-2024, which introduced significant breaking changes to the API. A decision was needed on which version to use and whether to pin it.

---

## Decision

Pin Great Expectations to `==0.18.19` in `docker/Dockerfile.airflow`. Do not upgrade to 1.x without a full rewrite of `pipeline/bronze/validator.py`.

---

## Rationale

**GX 1.x removed `context.sources`.** The pipeline's `GXValidator` uses `context.sources.add_postgres(...)` to connect to the Postgres datasource. This API was removed in GX 1.0 and replaced with a different configuration model. Installing GX 1.x causes an `AttributeError` at runtime and completely breaks Bronze validation.

**The 0.18.x API is stable and sufficient.** GX 0.18.19 supports SQL datasources, expectation suites, and HTML report generation — all the features the pipeline uses. There is no feature in GX 1.x that the current validation suite requires.

**Unpinned GX is dangerous.** If `great-expectations` is listed without a version pin, `pip install` resolves to the latest version (currently 1.x). This caused the pipeline to break during initial setup. The pin was added after debugging the `AttributeError` in `context.sources`.

**0.18.x receives security fixes.** GX 0.18.x remains on PyPI and is not end-of-life. Patch upgrades within `0.18.x` are safe and do not change the API.

---

## What changed when this was discovered

The original `requirements.txt` contained `great-expectations>=0.18`. On a fresh install, pip resolved this to GX 1.x. The `GXValidator.run()` method raised:

```
AttributeError: 'EphemeralDataContext' object has no attribute 'sources'
```

The fix was to change the pin to `great-expectations==0.18.19` in both the Dockerfile and any requirements files.

---

## Migration path to GX 1.x

If GX 1.x migration becomes necessary, the following changes are required in `pipeline/bronze/validator.py`:

1. Replace `context.sources.add_postgres(...)` with the new `context.data_sources.add_postgres(...)` API
2. Replace `BatchRequest` with the new `BatchDefinition` pattern
3. Replace `ExpectationSuite` instantiation with the new fluent API
4. Re-test all four expectation classes against a real TLC partition

This is a significant rewrite. The pin ensures the migration is a deliberate, tested change rather than an accidental breakage from a dependency upgrade.

---

## Alternatives considered

**Upgrade to GX 1.x immediately** — Rejected. The API changes are extensive and would require significant development time. The 0.18.x API works correctly and there is no user-facing benefit to upgrading.

**Replace GX with custom SQL assertions** — Considered as a fallback. Plain SQL `COUNT`, `MIN`, `MAX` queries against the ingested partition could replace GX expectations. This would remove the GX dependency entirely and produce simpler, more maintainable validation code. If GX 1.x migration proves too complex, this is the recommended alternative path.

---

## Consequences

- `great-expectations==0.18.19` must be pinned in all environments (Dockerfile, pyproject.toml, CI).
- Any `pip install great-expectations` without a version pin in a new environment will install 1.x and break validation.
- Dependabot or automated dependency updates must be configured to ignore GX version bumps beyond `0.18.x`.
