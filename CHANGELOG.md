# Changelog

All notable changes, by GitHub release. Versions follow [semver](https://semver.org/); dates are
release dates (UTC). Each release is deployed to prod by `deploy-prod.yml` after approval.

## [Unreleased]

### Added
- Portfolio documentation: README, ADRs 0002–0009, runbook (failed refresh, safe rerun, DST,
  secret rotation, release checklist), tutorial index, this changelog.

## [v0.6.1] - 2026-10-27 — prod dashboard drift handling

### Added
- `deploy-prod.yml`: `force` input for manual runs (`bundle deploy --force`) to overwrite a
  dashboard modified in the prod UI; release-triggered runs never force. ([#10])
- `docs/runbook.md`: "Dashboard modified remotely" (cause, repo-wins rule, force command).

### Changed
- Dashboard resource keeps only `energy-consumers` `CAN_READ`; `CAN_MANAGE` comes from the target
  permissions (removes "already has permissions set" warnings).

## [v0.6.0] - 2026-10-27 — wheel packaging

### Changed
- `energy_lakehouse` is built as a wheel (`databricks.yml` artifact, `uv build --wheel`) and
  installed in every job environment and the pipeline environment; notebooks and pipeline files
  no longer modify `sys.path`. ([#9])
- Runtime dependencies come from `pyproject.toml`; job environments add only exact pins of
  holidays, scikit-learn and mlflow (identical for training and scoring).
- `requires-python` is `>=3.11` so the wheel installs on serverless environment version 2.

### Fixed
- `apply_contracts` / `verify_contracts` failed with `FileNotFoundError` once the package was
  installed: `contracts/*.yaml` now ship in the wheel as package data and are loaded through
  `importlib.resources`.

## [v0.5.0] - 2026-10-25 — dashboard and Genie

### Added
- AI/BI dashboard *Energy Market DE* with pages Market, Merit order, Forecast and Data quality;
  KPIs use complete days only. ([#8])
- Per-environment dashboard build (`scripts/build_dashboard.py`): one dev source, one built file
  per target with that target's schema; tests guard the schema names.
- Genie agent definition (`docs/genie/ask-the-energy-lakehouse.md`): tables, instructions,
  example SQL, benchmarks, recreation steps per environment.

## [v0.4.1] - 2026-10-23 — ML-assisted data quality

### Added
- Anomaly detectors (`anomalies.py`): seasonal robust z-score, multivariate IsolationForest,
  completeness, physics rule (solar vs. radiation), forecast drift; warning/critical severity
  design. ([#7])
- `detect_anomalies` task → `gold_dq_anomalies`, `gold_dq_daily_summary` (draft contracts); fails
  only on critical findings.
- Injection test (`inject_test_anomaly=true`, dev only) and optional LLM summary via `ai_query`
  → `gold_dq_daily_summary_text`.
- `daily_refresh_job` runs one at a time with queueing.

## [v0.4.0] - 2026-10-23 — ML-assisted data quality (no changes)

Tagged on the same commit as v0.3.0, before the data quality PR was merged; contains no changes.
The data quality work shipped in v0.4.1.

## [v0.3.0] - 2026-10-23 — day-ahead forecast

### Added
- Day-ahead forecast of `total_load_mwh` and `price_eur_mwh` for every hour of the next
  Europe/Berlin day: leakage-checked features (`forecast.py`), `HistGradientBoostingRegressor`
  vs. seasonal-naive baseline. ([#6])
- `train_forecast_job` (weekly): MLflow tracking, UC model registry, `champion` alias only if the
  model beats the baseline.
- `score_forecast` → `gold_forecast_hourly` (MERGE) and `forecast_accuracy` →
  `gold_forecast_accuracy` in `daily_refresh_job`.
- Weather forecast branch: `silver_weather_forecast_hourly`, `gold_weather_forecast_hourly`.
- `tests/test_job_environments.py`: notebook tasks get their libraries from job environments only.

## [v0.2.0] - 2026-10-22 — data contracts

### Added
- Data contracts (`contracts/*.yaml`) for the gold tables, parsed with pydantic; generated product
  pages in `docs/data-products/`. ([#5])
- `apply_contracts` (comments, tags, grants) and `verify_contracts` (schema, quality rules,
  freshness) in `daily_refresh_job`; CI test that contracts match the pipeline's columns.

### Fixed
- `python-preference` moved from `pyproject.toml` to the user's uv config; CI uses uv-managed
  Python.

## [v0.1.1] - 2026-10-21 — first production deployment

### Fixed
- Per-target name prefix (`[staging] `, `[prod] `) so resources of different targets in one
  workspace no longer collide (HTTP 409). ([#4])

## [v0.1.0] - 2026-10-21 — first production release

### Added
- SMARD extractor and Open-Meteo extractor (daily forecast and archive modes) writing raw JSON to
  the landing zone; upload scripts with verification.
- Lakeflow Spark Declarative Pipeline: bronze (Auto Loader) → silver (long, deduplicated,
  expectations) → gold (`gold_energy_hourly`, `gold_energy_daily`, `gold_energy_weather_hourly`).
- Daily refresh: GitHub Actions extracts and uploads, then runs `daily_refresh_job` (pipeline +
  quality checks) as a service principal (OAuth M2M).
- Environments dev/staging/prod: shared raw zone, schema per target, staging/prod run as the CI/CD
  service principal. ([#1])
- CI/CD: PR checks, staging deploy on merge, prod deploy on release with approval.

### Fixed
- Private bundle root and explicit permissions for staging/prod. ([#2])
- Use the CI/CD service principal's application ID (not the secret); test guard against secrets
  and non-UUID IDs. ([#3])

[Unreleased]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.6.1...HEAD
[v0.6.1]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.6.0...v0.6.1
[v0.6.0]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.5.0...v0.6.0
[v0.5.0]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.4.1...v0.5.0
[v0.4.1]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.4.0...v0.4.1
[v0.4.0]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.3.0...v0.4.0
[v0.3.0]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.2.0...v0.3.0
[v0.2.0]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.1.1...v0.2.0
[v0.1.1]: https://github.com/tutul032/energy-lakehouse-de/compare/v0.1.0...v0.1.1
[v0.1.0]: https://github.com/tutul032/energy-lakehouse-de/releases/tag/v0.1.0
[#1]: https://github.com/tutul032/energy-lakehouse-de/pull/1
[#2]: https://github.com/tutul032/energy-lakehouse-de/pull/2
[#3]: https://github.com/tutul032/energy-lakehouse-de/pull/3
[#4]: https://github.com/tutul032/energy-lakehouse-de/pull/4
[#5]: https://github.com/tutul032/energy-lakehouse-de/pull/5
[#6]: https://github.com/tutul032/energy-lakehouse-de/pull/6
[#7]: https://github.com/tutul032/energy-lakehouse-de/pull/7
[#8]: https://github.com/tutul032/energy-lakehouse-de/pull/8
[#9]: https://github.com/tutul032/energy-lakehouse-de/pull/9
[#10]: https://github.com/tutul032/energy-lakehouse-de/pull/10
