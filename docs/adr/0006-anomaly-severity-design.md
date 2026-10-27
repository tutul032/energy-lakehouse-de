# 6. Severity design for ML-assisted data quality

- **Status:** Accepted
- **Date:** 2026-10-24 (recorded 2026-10-27)

## Context
Rule-based expectations catch malformed data but not plausible-looking errors, such as a series
that drops by half or solar output at night. Statistical detectors find those but also flag real
events: lower solar in autumn, more gas on windless days, negative prices. A detector that fails
the daily job on real market events would soon be switched off.

## Decision
- Every detector returns the same columns (check, entity, timestamp, value, expected, bounds,
  score, severity, reason) for the last 7 days. Results are merged into `gold_dq_anomalies` and
  counted per check and severity in `gold_dq_daily_summary`.
- **warning** = unusual but possibly real; never fails the job:
  - `seasonal_robust_z` per series and local hour-of-week (median/MAD of the previous 8 weeks;
    flagged only if |z| ≥ 4 and the deviation is ≥ 25 % of the median; prices excluded);
  - `multivariate_isolation_forest` over load, price, generation and weather (contamination
    0.005);
  - `forecast_drift_mae` (model worse than the baseline on 3 consecutive issue dates).
- **critical** = likely a data error; fails the job after the tables are written:
  - `completeness` (> 20 % of the expected 23/24/25 local hours missing; > 5 % is a warning);
  - `physics_rules` (solar > 100 MWh while radiation < 1 W/m² at both H and H+1);
  - `forecast_drift_mape` (load MAPE > 6 % on 2 consecutive issue dates).
- Detection is tested end to end by injecting a marked `TEST:total_load` anomaly (dev schemas
  only; `TEST:` entities never fail the job and are hidden from the dashboard).
- An optional LLM summary (`ai_query`) turns the counts and top 10 findings into 3–5 sentences.
  It runs even if detection failed and never fails the job itself.

## Consequences
- The job fails only on findings that need action; warnings are ranked by score on the Data
  quality dashboard page for review.
- Thresholds are explicit constants with unit tests, so changing them is a reviewed code change.
- The statistical detectors need history (at least 5 points per hour-of-week); new series are not
  scored until enough history exists.
