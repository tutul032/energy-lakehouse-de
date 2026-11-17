# Part IV — Transfer and exam

## Chapter 9 — Pipeline design decisions, and why

A good engineer can explain **why each step is where it is**. These are the decisions in this project, with the reasoning you'll reuse elsewhere.

### 9.1 The order of the daily job

```
run_pipeline → quality_checks → apply_contracts → verify_contracts → score_forecast → forecast_accuracy → detect_anomalies → dq_summary_llm
```

| Position | Why here |
| --- | --- |
| **run_pipeline first** | Everything downstream reads gold. Fresh gold first. |
| **quality_checks, verify_contracts before forecasting** | They are **gates**: if gold is stale, empty or violates its contract, forecasting on it would produce confident nonsense. A failing gate stops the job before bad data spreads. |
| **apply_contracts before verify_contracts** | Comments/tags/grants are applied, then the promise is checked. |
| **score_forecast before forecast_accuracy** | Accuracy compares *past* forecasts with actuals; today's scoring adds tomorrow's row. Order matters less than both existing. |
| **detect_anomalies after forecasting** | See 9.2. |
| **dq_summary_llm last, `run_if: ALL_DONE`** | It summarises everything, including failures, and must never block anything. |

### 9.2 "Why is anomaly detection after forecasting, not before?"

This is a good question, and the honest answer is: **it depends on what kind of check it is.** Our `detect_anomalies` task mixes two kinds:

| Kind | Examples | Best position | Why |
| --- | --- | --- | --- |
| **Monitoring of outputs** | Forecast drift (model worse than baseline for 3 days) | **After** forecasting and accuracy | It needs the forecast results and `gold_forecast_accuracy`. It can't run before them. |
| **Unusual-but-possible inputs** | Seasonal z-scores, Isolation Forest warnings | Before or after (doesn't block anything) | They're **warnings**: often real events (storms, holidays, the autumn shift). Blocking the forecast because of a windy day would be wrong; the forecast should *react* to the wind. |
| **Likely-broken inputs** | Completeness (> 20 % hours missing), physics rules (solar at night) | **Before** forecasting, as a **gate** | If the input is broken, the forecast built on it is broken too. These belong with the other gates. |

**What we built:** a single task after forecasting. That works because:
- the hard input gates already run *before* forecasting (expectations, `quality_checks`, `verify_contracts` with completeness and freshness rules)
- the anomaly warnings shouldn't block anything
- forecast drift needs the accuracy results

**The cleaner design** (and a good exercise, Exercise 11.3): split it into two tasks:

```
… → verify_contracts → detect_input_anomalies (critical = gate) → score_forecast → forecast_accuracy → detect_output_drift → dq_summary_llm
```

**General principle you can reuse:**
- **Gates** (checks that decide whether downstream steps may run) go **before** their consumers.
- **Monitors** (checks that observe outputs over time) go **after** the outputs exist.
- **Warnings** never block; **critical** findings may.

### 9.3 Other decisions worth explaining

| Decision | Reason | Alternative and trade-off |
| --- | --- | --- |
| Extract in GitHub Actions, not Databricks | Free Edition has no outbound internet | With internet: a Databricks extract task (simpler, one orchestrator) |
| Bronze = streaming table | Only new files are processed (incremental, cheap) | Batch reload of everything: simple but slow and expensive at scale |
| Silver/gold = materialized views | Always consistent with inputs; dedupe logic is simple | Streaming everywhere: faster, but complex for dedupe and late data |
| "Newest file wins" deduplication | Late and corrected data replaces earlier versions | First-wins: never picks up corrections |
| Long silver, wide gold | Long is easy to extend; wide is easy to query and chart | Wide everywhere: schema changes for every new series |
| Contracts with CI guard + runtime verify | Catches breaking changes before merge and drift after deploy | Docs only: drift goes unnoticed |
| Batch scoring, not serving | A daily forecast needs no API | Real-time serving: needed for per-request decisions (fraud) |
| Champion alias gated by baseline | A worse model never reaches prod automatically | Always promote: risky |
| One dashboard, schema per target | No copy drift between environments | Three hand-made dashboards: drift and manual work |
| Severity design | Prevents alert fatigue | Everything critical: alerts get ignored |

### Exam questions — Chapter 9

1. **Why can't forecast-drift detection run before forecasting?**
   *Answer:* it needs the forecasts and their accuracy against actuals.
2. **Should a seasonal z-score warning block the forecast?**
   *Answer:* no. Warnings are often real events, and the forecast should react to them.
3. **Where do "solar at night" checks belong ideally?**
   *Answer:* before forecasting, as a gate. Impossible input data makes the forecast meaningless.
4. **What's the difference between a gate and a monitor?**
   *Answer:* a gate decides whether downstream steps may run; a monitor observes outputs over time.

---

## Chapter 10 — Transferring the method to other datasets

### 10.1 What stays the same, and what changes

| Stays the same (template, ~70–80 %) | Changes (domain, ~20–30 %) |
| --- | --- |
| Repo layout, bundle with three targets, CI/CD workflows, service principals | Sources and extractors |
| Medallion structure, Auto Loader, lineage columns, "newest file wins" | Silver keys, types, units, dedupe rules |
| Contracts framework, guard tests, job environments, wheel | Gold tables and KPIs |
| Anomaly framework, severity design, LLM summary | Domain rules (the "physics" of the domain) |
| Dashboard-as-code routine, Genie setup method | Dashboard content, Genie instructions |
| Lifecycle, playbook, plausibility method | Plausible ranges, business questions |

### 10.2 The transfer recipe (apply to any dataset)

1. **Story:** two sentences (Chapter 1.4).
2. **Understand the data:** the ten questions and the profile (Chapter 4).
3. **Architectures:** both diagrams.
4. **Gold first:** name the 2–4 gold tables the users need.
5. **Stage plan:** choose stages 0–11 (Handbook README / Chapter 3), and **skip** what isn't needed.
6. **Thin slice:** one source → bronze → silver → one gold table → one chart, deployed through dev → staging → prod.
7. **Widen:** the second source, contracts, ML, monitoring, one at a time, each through the full lifecycle.
8. **Document** every real problem as you go.

### 10.3 Worked example: NYC taxi demand

**Story:** *"Every day, load the public NYC taxi trip records and build hourly demand per pickup zone. Forecast tomorrow's demand per zone, and show the busiest zones on a dashboard."*

**Data:** public NYC TLC trip records (monthly Parquet files, millions of rows; check the current terms of use before starting). Databricks also includes a small `samples.nyctaxi` table for experiments.

**The ten questions (sample answers):**

| # | Answer |
| --- | --- |
| Grain | One trip |
| Key | No natural key → a hash of (pickup time, dropoff time, zones, fare, vendor) |
| Time | Local New York time (DST!), pickup and dropoff timestamps |
| Units | Miles, USD, minutes |
| Freshness | Monthly files, published with a delay |
| Late data | Corrections possible → "newest file wins" per month |
| Nulls | Missing passenger counts, unknown zones |
| Ranges | Fare ≥ 0, trip duration 1 min–3 h, distance < 100 miles |
| Size | Millions of trips per month → partition by month |
| Licence / privacy | Public data; no names, but locations: aggregate before sharing |

**Lab-by-lab mapping:**

| Energy lab | Taxi equivalent | What changes |
| --- | --- | --- |
| 0 Setup | Same | Project name, schemas `taxi_*` |
| 1 Extract | Download monthly Parquet files into the landing volume | File format; monthly instead of weekly chunks |
| 2 Pipeline | Bronze trips → silver (typed, deduplicated, plausible trips) → gold `demand_hourly_zone` | **Physics rules:** dropoff after pickup, fare ≥ 0, duration limits |
| 3 Second source | Weather (rain, temperature) per hour, NYC | The same Open-Meteo extractor, different coordinates |
| 4 Automation | Monthly + daily refresh | Schedule |
| 5 CI/CD | Same | Nothing |
| 6 Contracts | Contracts for `demand_hourly_zone`, `zones` | Columns, rules (e.g. "trips ≥ 0") |
| 7 ML | Forecast tomorrow's demand per zone; baseline: same hour last week | Many series (per zone): group models or one global model with a zone feature |
| 8 Monitoring | Seasonal z-scores per zone and hour, completeness per month | New "physics": a negative duration is critical |
| 9 Dashboard / Genie | Map of busiest zones, demand vs. weather, forecast vs. actual | Content; Genie instructions (NY time zone, zone names) |
| 10 Docs | Same | Story, numbers |

**The same mistakes, in new forms:** DST in New York (different dates than Germany), monthly files that get corrected later, trips with dropoff before pickup, and outliers like 0-minute trips that cost $500.

### 10.4 Quick mappings for other domains

| Domain | Grain | Gold | ML | Domain "physics" rules |
| --- | --- | --- | --- | --- |
| **Payments (PaySim, Project 2)** | Transaction | Fraud rate per type/hour, customer features | Fraud classification (precision/recall, thresholds by cost) | Amount ≥ 0; balance changes consistent |
| **Retail sales** | Sales line | Sales per store/day, product mix | Demand forecast per store | Quantity ≥ 0; returns flagged; store open days |
| **IoT / machines** | Sensor reading | Per machine per 10 min | Anomaly/failure detection | Temperature limits; no readings from switched-off machines |
| **Web / product analytics** | Event | Sessions, funnels per day | Churn / conversion | Session ≤ 24 h; no events before signup |
| **Documents (Project 3)** | Chunk of a document | Chunk table + embeddings | RAG / agent | Every answer cites a source; no answer without retrieval |

### Exam questions — Chapter 10

1. **Which parts do you rewrite for a new domain?**
   *Answer:* extractors, silver/gold logic, contracts content, domain rules, dashboard and Genie content.
2. **What's a "thin slice" and why start with it?**
   *Answer:* one source to one chart through all environments. Problems appear early and cheaply.
3. **What's the taxi equivalent of "solar at night"?**
   *Answer:* a physically impossible trip, e.g. dropoff before pickup or a negative duration.

---

## Chapter 11 — Final exam, exercises and capstone

### 11.1 Final exam (25 questions with answers)

**Architecture and design**

1. **What are the two architectures, and which question does each answer?** Data/product: how data flows to users. Delivery: how changes reach prod safely.
2. **What does each medallion layer guarantee?** Bronze: raw + lineage. Silver: clean, typed, deduplicated, rules applied. Gold: business-ready.
3. **Why a shared raw zone with a schema per environment?** The raw facts are identical everywhere; curated layers are environment-specific.
4. **Give one gate and one monitor from this project.** Gate: `verify_contracts`. Monitor: forecast drift.
5. **Why batch scoring for a day-ahead forecast?** It's needed once a day; serving adds cost without benefit.

**Data**

6. **What made 596 GWh look like a complete day?** `hour_count` counted rows, not hours with published values.
7. **How many hours does 25 Oct 2026 have in Germany, and why?** 25, because DST ends and 02:00–03:00 occurs twice.
8. **Why "newest file wins"?** Late and corrected data must replace earlier versions.
9. **What do you check first in a new dataset?** Grain, key, time column and time zone (the ten questions).
10. **Why not judge data by its first rows?** Problems are often at the end (incomplete data) or on special days.

**Quality**

11. **Why are expectations null-safe?** All rules evaluate on the same rows; `NULL` counts as a failure and double-counts.
12. **What makes a contract change MAJOR?** Removing or renaming a column, or changing a type or the grain.
13. **What did the 28 "critical" anomalies teach?** Unusual ≠ broken; severity design prevents alert fatigue.
14. **Why median/MAD?** They're robust against outliers.
15. **What does "test the test" mean?** Make a guard fail on purpose once, to prove it can fail.

**ML**

16. **Define leakage, with an example from this project.** Information unavailable at issue time; e.g. a 1-hour lag when forecasting tomorrow.
17. **Why MAE for price, not MAPE?** Prices near zero or negative make percentages explode.
18. **When is the champion alias moved?** Only when the new model beats the baseline.
19. **Why retrain after changing job environments?** Pickled models depend on library versions (the NumPy mismatch).

**Delivery and operations**

20. **Name the release checklist.** PR merged → file present on master → staging green → release → approve → prod run.
21. **What does `SUCCESS_WITH_FAILURES` mean?** At least one task failed, but an `ALL_DONE` task still ran. Treat it as a failure.
22. **How do you find the root error of a failed job?** The first FAILED (not UPSTREAM_FAILED) task, then `get-run-output`.
23. **The bundle says "dashboard modified remotely". What now?** The repo wins: port intended changes via dev, then an approved opt-in force deploy.
24. **What protects you from committing on master?** A pre-commit hook locally, and a ruleset on GitHub.
25. **What's the safest way to recover lost uncommitted work?** Stop, don't run more Git commands, and restore from the deployed bundle copy or editor history.

### 11.2 Exercises

| # | Exercise | Skill |
| --- | --- | --- |
| 11.1 | Draw both architectures for the bike-sharing idea (Exercise 1.1), then implement the **thin slice**: one source → one gold table → one chart, deployed to dev | Architecture, lifecycle |
| 11.2 | Profile a new public dataset with the Chapter 4 prompt, and answer the ten questions | Data understanding |
| 11.3 | Split `detect_anomalies` into `detect_input_anomalies` (gate, before `score_forecast`) and `detect_output_drift` (after accuracy). Update tests and the tutorial. | Design (Chapter 9.2) |
| 11.4 | Fix the dashboard follow-ups: freshness = latest hour **with load**; the DST double hour in the generation-mix chart; an `is_final` column for accuracy | Plausibility |
| 11.5 | Create `/verify-dev` and `scripts/smoke_checks.py`, add the smoke checks to `deploy-staging.yml` | Automation |
| 11.6 | Introduce a deliberate bug (e.g. a 1-hour lag). Find it using only the playbook (Chapter 7.2) and the tests | Debugging |
| 11.7 | Write an ADR for a decision you'd make differently (e.g. streaming silver) | Communication |

### 11.3 Capstone project

**Task:** build a similar end-to-end solution on a **new dataset** of your choice (e.g. NYC taxi demand, retail sales, or IoT sensor data).

**Minimum scope (pass):**
- [ ] Two-sentence story, both architectures in the README
- [ ] Data profile + the ten questions answered (`docs/data-profile.md`)
- [ ] Bundle with dev/staging/prod; CI with lint, tests, validate
- [ ] Idempotent extraction into a landing volume
- [ ] Bronze/silver/gold with null-safe expectations
- [ ] At least one contract with a CI guard you've seen fail
- [ ] A dashboard deployed as code, with KPIs that pass a plausibility check
- [ ] A release to prod via PR → staging → approval
- [ ] A README with results and screenshots; at least 3 real problems documented with logs and fixes

**Extended scope (distinction):**
- [ ] A forecast or classifier with a baseline, MLflow, a champion alias and a leakage test
- [ ] Anomaly detection with a severity design
- [ ] A Genie agent with ≥ 5 benchmark questions passing
- [ ] Automated smoke checks after every deploy, plus `/verify-dev` and `/ship` commands
- [ ] ADRs for at least 3 decisions

**Self-assessment rubric:**

| Area | 1 — basic | 2 — solid | 3 — professional |
| --- | --- | --- | --- |
| Data understanding | Columns listed | Profile + ten questions | Domain plausibility ranges, late-data strategy |
| Pipeline | Runs | Incremental, rules | Gates and monitors in the right places |
| Quality | Some checks | Contracts + CI guard | Guards proven to fail; incidents turned into tests |
| Delivery | Manual deploy | PR + CI | Staging + approval-gated prod, no manual prod changes |
| Documentation | README | Results + screenshots | ADRs, runbook, honest limitations |

**When you can pass the capstone without looking at the energy tutorials, you've completed the learning path.**
