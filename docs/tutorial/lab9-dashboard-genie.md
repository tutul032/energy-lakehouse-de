# Lab 9 — AI/BI Dashboard as Code and a Genie Agent: Making the Lakehouse Visible and Askable

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 8 — ML-assisted data quality. Next: Lab 10 — Documentation, packaging and cleanup.

## 1. Goal

Until now, every result lived in SQL queries that only you can run. In this lab you make the lakehouse **visible** and **askable**:

- An **AI/BI dashboard** "Energy Market DE" with four pages: Market, Merit order, Forecast, Data quality
- The dashboard **defined as code** in the bundle, deployed by CI to dev, staging and prod, each showing its own data
- A **Genie Agent** "Ask the energy lakehouse" that answers questions in plain language and shows its SQL
- **Tests** that catch a blank, hardcoded or half-finished dashboard before it reaches prod
- A gold-layer fix that the dashboard exposed: what a **complete day** really means

**My results:** prod dashboard working on all pages; **Genie benchmark 6/6**; release `v0.5.0`.

**Time:** about one to one and a half working days.

---

## 2. Concepts in plain words

### 2.1 AI/BI dashboards

Databricks' built-in BI tool (formerly "Lakeview"). A dashboard has:

| Part | What it is |
| --- | --- |
| **Datasets** | SQL queries (Data tab) |
| **Widgets** | Charts, counters, tables, text, placed on **pages** (canvas) |
| **SQL warehouse** | The compute that runs the queries (Free Edition: *Serverless Starter Warehouse*) |
| **Draft vs. published** | You edit a draft; viewers see the published version |
| **Embedded credentials** | Published queries run as the **owner** (in prod: the service principal), so viewers need no table access of their own |

### 2.2 Dashboard as code: UI first, then code

A dashboard is stored as a JSON file (`*.lvdash.json`). The practical workflow:

```
design in the dev UI  →  databricks bundle generate dashboard  →  commit  →  PR  →  CI deploys staging/prod
```

From then on, **never edit staging or prod dashboards by hand**; the next deploy would overwrite them, and a hand-made copy drifts away from the code.

### 2.3 One dashboard file, three environments

The same JSON must show dev data in dev and prod data in prod. We use **unqualified table names** in every dataset (`FROM gold_energy_daily`), plus two bundle settings:

```yaml
dataset_catalog: ${var.catalog}   # workspace
dataset_schema:  ${var.schema}    # energy_dev / energy_staging / energy_prod
```

Databricks then looks up each table in the target's schema. (The fallback, if a UI forces qualified names, is a small build step that rewrites the schema per target. We didn't need it.)

### 2.4 Genie Agents

**Genie** (in the UI: *Genie Agents*) is a natural-language interface. You give it **sources** (tables), **instructions** (units, time zones, business rules) and **example queries**. Users ask questions; Genie writes SQL, runs it, and shows the answer **with the SQL**.

Its quality depends almost entirely on **your instructions and examples**: the data-product thinking of Lab 6, applied to AI. Genie can write wrong SQL, so we test it with **benchmark questions** whose answers we know.

---

## 3. Prerequisites

- Labs 0–8 completed, prod refreshed daily
- Golden rules: **branch first** (and the pre-commit hook from Step 09), commit before experiments, local checks before push, release only after merge + green staging, German dates via `from_utc_timestamp(…, 'Europe/Berlin')`

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c feat/dashboard-genie
```

---

## 4. Steps

### Step 01 — Create the dashboard and its datasets (dev UI)

**Dashboards → Create dashboard** → name `Energy Market DE`.

On the **Data** tab, for **every** dataset:
1. **+ Add SQL dataset**
2. In the two dropdowns above the editor, **select** catalog `workspace` and schema **`energy_dev`**. Select them explicitly, even if they seem to be shown already (Troubleshooting P4).
3. Paste the SQL with **unqualified table names** → **Run** → **⋮ → Rename**

**Look up column names** with a `SELECT`, not `DESCRIBE` (Troubleshooting P1):

```sql
SELECT * FROM gold_energy_daily LIMIT 5
```

**The ten datasets** (column names as in my project; check yours):

```sql
-- ds_daily_kpis: latest COMPLETE day
SELECT date_local,
       round(total_load_mwh / 1000, 1)      AS load_gwh,
       round(price_avg_eur_mwh, 1)          AS avg_price,
       round(renewables_share_avg * 100, 1) AS renewables_pct
FROM gold_energy_daily
WHERE date_local = (SELECT max(date_local) FROM gold_energy_daily WHERE is_complete)

-- ds_hourly_14d
SELECT hour_local, total_load_mwh, price_eur_mwh, solar_mwh, wind_onshore_mwh, wind_offshore_mwh,
       lignite_mwh, hard_coal_mwh, natural_gas_mwh, renewables_share
FROM gold_energy_hourly
WHERE hour_utc >= current_timestamp() - INTERVAL 14 DAYS

-- ds_merit_order
SELECT round(wind_speed_100m_onshore_avg_ms) AS wind_ms, round(avg(price_eur_mwh), 1) AS avg_price,
       round(avg(renewables_share), 2) AS ren_share, count(*) AS hours
FROM gold_energy_weather_hourly
WHERE wind_speed_100m_onshore_avg_ms IS NOT NULL AND hour(hour_local) BETWEEN 0 AND 5
GROUP BY 1 HAVING count(*) >= 20 ORDER BY 1

-- ds_forecast_tomorrow
WITH f AS (
  SELECT target_hour_local, target, forecast FROM gold_forecast_hourly
  WHERE issue_date = (SELECT max(issue_date) FROM gold_forecast_hourly)
)
SELECT f.target_hour_local, f.target, f.forecast,
       CASE f.target WHEN 'total_load_mwh' THEN h.total_load_mwh ELSE h.price_eur_mwh END AS same_hour_last_week
FROM f LEFT JOIN gold_energy_hourly h ON h.hour_local = f.target_hour_local - INTERVAL 7 DAYS

-- ds_forecast_vs_actual
SELECT f.target_hour_local, f.target, f.forecast,
       CASE f.target WHEN 'total_load_mwh' THEN h.total_load_mwh ELSE h.price_eur_mwh END AS actual
FROM gold_forecast_hourly f
JOIN gold_energy_hourly h ON h.hour_utc = f.target_hour_utc
WHERE f.issue_date >= date_sub(to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin')), 14)

-- ds_accuracy
SELECT issue_date, target, n_hours, round(mae, 1) AS mae, round(baseline_mae, 1) AS baseline_mae,
       round(mape, 2) AS mape, mae < baseline_mae AS model_better
FROM gold_forecast_accuracy ORDER BY issue_date DESC

-- ds_dq_counts
SELECT to_date(from_utc_timestamp(detected_at, 'Europe/Berlin')) AS day, severity, check_name, count(*) AS n
FROM gold_dq_anomalies WHERE entity NOT LIKE 'TEST:%' GROUP BY 1, 2, 3

-- ds_dq_top: latest detection run (not "last 24 h", which is empty when a run is skipped)
SELECT check_name, entity, ts_utc, round(value) AS value, round(expected) AS expected,
       round(score, 1) AS score, severity, reason
FROM gold_dq_anomalies
WHERE entity NOT LIKE 'TEST:%'
  AND detected_at >= (SELECT max(detected_at) FROM gold_dq_anomalies) - INTERVAL 1 HOUR
ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, abs(score) DESC
LIMIT 15

-- ds_dq_summary
SELECT detection_date, model, summary_text FROM gold_dq_daily_summary_text
ORDER BY detection_date DESC LIMIT 1

-- ds_freshness
SELECT 'energy' AS source, max(hour_utc) AS latest_utc FROM gold_energy_hourly
UNION ALL SELECT 'weather (observed)', max(hour_utc) FROM gold_energy_weather_hourly WHERE wind_speed_100m_onshore_avg_ms IS NOT NULL
UNION ALL SELECT 'forecast issued', max(issue_time_utc) FROM gold_forecast_hourly
```

**Expected:** every dataset shows a green ✓ in the list.

📸 `lab9-step01-datasets.png`

---

### Step 02 — Build the four pages (with the dashboard assistant)

Rename *Untitled page* to `Market`, and add `Merit order`, `Forecast`, `Data quality` with **+**.

Widgets can be built by hand (toolbar at the bottom of the canvas → **Add a visualization**) or by the **dashboard assistant** (the ✨ button). **Always add "Use unqualified table names" to assistant prompts** (Troubleshooting P3). Example prompts:

```text
Using dataset ds_hourly_14d, create a line chart titled "Load and price – last 14 days" with hour_local on the
x-axis, total_load_mwh on the left y-axis and price_eur_mwh on a secondary right y-axis. Use unqualified table names.
```
```text
Using dataset ds_hourly_14d, create a stacked area chart titled "Generation mix (MWh per hour)" with hour_local on
the x-axis and solar_mwh, wind_onshore_mwh, wind_offshore_mwh, lignite_mwh, hard_coal_mwh, natural_gas_mwh as series.
```
```text
Using dataset ds_daily_kpis, create three counter widgets side by side: "Load (GWh)" from load_gwh,
"Avg price (EUR/MWh)" from avg_price, and "Renewables (%)" from renewables_pct, with date_local as subtitle.
```
```text
Using dataset ds_merit_order, create a chart titled "More wind, lower price (night hours)" with wind_ms on the
x-axis and avg_price on the y-axis.
```

**Page layout:**

| Page | Widgets |
| --- | --- |
| **Market** | 3 counters (`ds_daily_kpis`), load/price line chart and generation-mix area chart (`ds_hourly_14d`) |
| **Merit order** | Wind vs. price chart (`ds_merit_order`) |
| **Forecast** | Load and price forecast vs. same hour last week (`ds_forecast_tomorrow`), forecast vs. actual (`ds_forecast_vs_actual`), accuracy table (`ds_accuracy`) |
| **Data quality** | Findings per day by severity (`ds_dq_counts`), top findings (`ds_dq_top`), LLM report (`ds_dq_summary`), freshness (`ds_freshness`) |

**Check every counter's aggregation:** a counter set to *COUNT* shows **1** for a one-row dataset. Set **Value** to the field with aggregation **Sum** (Troubleshooting P2).

**Sanity-check the numbers against domain knowledge:**
- Germany uses roughly **1,100–1,500 GWh per day**
- Prices are mostly 0–250 EUR/MWh

My first KPI showed **596 GWh**, which led to Troubleshooting P5.

**What my pages showed:**
- **Merit order:** the night price falls from ~136 EUR/MWh at calm wind to ~28 EUR/MWh at 11 m/s, the merit-order effect in one chart
- **Forecast:** the load forecast follows last week's curve closely, while the price forecast for windy 24 October is far below last week's
- **Market:** daily load rhythm and price spikes, with solar peaks at midday and windy days visible in the mix

📸 `lab9-step02-market.png`, `…-merit-order.png`, `…-forecast.png`, `…-dq.png`

---

### Step 03 — Export the dashboard into the bundle

```powershell
databricks bundle generate dashboard --existing-path "/Workspace/Users/<you>/Energy Market DE.lvdash.json" -t dev -p free
git status
```

**Expected:** `src/energy_market_de.lvdash.json` and `resources/energy_market_de.dashboard.yml`.

**Claude Code prompt** (settings + tests):

```text
Read CLAUDE.md first. Make the exported AI/BI dashboard deployable to dev/staging/prod. Do not deploy.
1. resources/energy_market_de.dashboard.yml: file_path to the exported .lvdash.json, warehouse_id: ${var.warehouse_id}
   (look it up with `databricks warehouses list -p free`, define the variable in databricks.yml),
   dataset_catalog: ${var.catalog}, dataset_schema: ${var.schema}, embed_credentials: true,
   permissions: group energy-consumers CAN_READ, user ${var.alert_email} CAN_MANAGE, SP ${var.cicd_sp_app_id} CAN_MANAGE.
2. tests/test_dashboard.py:
   - the .lvdash.json parses;
   - no dataset SQL (queryLines only) contains energy_dev/energy_staging/energy_prod;
   - every dataset is used by at least one widget (pages[].layout[].widget.queries[].query.datasetName);
   - every page has at least one widget;
   - the resource sets dataset_catalog and dataset_schema.
   Each check has a fixture proving it fails on a bad dashboard.
3. CLAUDE.md: "edit in the dev UI → export → restore the YAML → test → commit".
```

**Commit the YAML immediately**, because the next `generate --force` overwrites it (Troubleshooting P7):

```powershell
uv run pytest -q
git add -A ; git commit -m "feat: dashboard as code with per-environment schema"
git ls-files resources | Select-String "dashboard"     # must list the YAML
```

**The re-export routine** after every UI change:

```powershell
databricks bundle generate dashboard --existing-path "/Workspace/Users/<you>/Energy Market DE.lvdash.json" -t dev -p free --force
git checkout -- resources/energy_market_de.dashboard.yml
uv run pytest -q
git add src/energy_market_de.lvdash.json ; git commit -m "chore: update dashboard"
```

**Check that the export is really complete:**

```powershell
$d = Get-Content src\energy_market_de.lvdash.json -Raw | ConvertFrom-Json
$d.pages | ForEach-Object { "{0}: {1} layout items" -f $_.displayName, $_.layout.Count }
$d.datasets | Select-Object displayName, catalog, schema | Format-Table -AutoSize
(Select-String -Path src\energy_market_de.lvdash.json -Pattern "workspace\.energy_dev\.").Count
```

**Expected:** every page with several layout items, every dataset with schema `energy_dev`, and a qualified-name count of **0**.

---

### Step 04 — Deploy dev and check

```powershell
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
```

**Expected:** `[dev <you>] Energy Market DE` appears in **Dashboards**, identical to the UI version.

In dev, *forecast vs. actual* and *accuracy* may be empty, because dev isn't refreshed daily. In prod they fill up.

---

### Step 05 — Create the Genie Agent

**Genie Agents → New** → **Connect your data**:
1. Click **workspace ›** → **energy_dev**, and select **six gold tables**: `gold_energy_hourly`, `gold_energy_daily`, `gold_energy_weather_hourly`, `gold_forecast_hourly`, `gold_forecast_accuracy`, `gold_dq_anomalies`
2. **Never mix schemas.** The *Recent* list may show a dev table next to a prod one.
3. Don't add bronze/silver tables.
4. **Create**, then name it `Ask the energy lakehouse`.

**Configure → Instructions** (paste):

```text
German electricity market data (SMARD, CC BY 4.0) and weather (Open-Meteo).
- Times: columns ending _utc are UTC; hour_local / target_hour_local / date_local are Europe/Berlin.
  "Today", "yesterday", "last week" always mean German local dates.
- A day is complete only if is_complete = true in gold_energy_daily; use only complete days for daily totals
  and "yesterday" KPIs.
- Units: *_mwh = MWh per hour; price_eur_mwh = EUR/MWh (day-ahead, DE-LU); renewables_share is 0–1 (×100 for %).
- Night = local hours 0–5. Windy = wind_speed_100m_onshore_avg_ms >= 8.
- Forecasts: gold_forecast_hourly, one row per target hour and target ('total_load_mwh' or 'price_eur_mwh');
  the latest forecast is max(issue_date). Accuracy: gold_forecast_accuracy (mae vs baseline_mae).
- Data quality: gold_dq_anomalies; 'warning' = unusual but possibly real, 'critical' = likely data error;
  ignore entities starting with 'TEST:'.
- Round prices to 1 decimal, energy to whole MWh.
```

**Configure → Examples:** the **top line** is the question, and the **code box** is the SQL (Troubleshooting P9). Three examples:

| Question (top line) | SQL (code box) |
| --- | --- |
| What was the average price on windy nights in the last 7 days? | `SELECT round(avg(price_eur_mwh), 1) AS avg_price FROM gold_energy_weather_hourly WHERE wind_speed_100m_onshore_avg_ms >= 8 AND hour(hour_local) BETWEEN 0 AND 5 AND hour_local >= date_sub(to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin')), 7)` |
| What is tomorrow's peak load forecast? | `SELECT target_hour_local, round(forecast) AS forecast_mwh FROM gold_forecast_hourly WHERE target = 'total_load_mwh' AND issue_date = (SELECT max(issue_date) FROM gold_forecast_hourly) ORDER BY forecast DESC LIMIT 1` |
| How accurate was the load forecast on the latest evaluated day? | `SELECT issue_date, round(mae, 1) AS mae, round(baseline_mae, 1) AS baseline_mae, round(mape, 2) AS mape FROM gold_forecast_accuracy WHERE target = 'total_load_mwh' ORDER BY issue_date DESC LIMIT 1` |

---

### Step 06 — Benchmark Genie

Ask in the chat, and compare each answer (and Genie's SQL) with your own query:

| # | Question | Check |
| --- | --- | --- |
| 1 | What was the average day-ahead price yesterday? | Matches `ds_daily_kpis`, using a complete day |
| 2 | Which hour had the highest load last week? | One plausible weekday daytime hour |
| 3 | Average price on windy vs. calm nights in the last 30 days? | Windy clearly lower |
| 4 | What is tomorrow's peak load forecast and at what time? | Matches `ds_forecast_tomorrow` |
| 5 | Was the model better than the baseline on the latest evaluated day? | Matches `ds_accuracy` |
| 6 | Were there any critical data-quality issues this week? | 0, TEST rows ignored |

**My result: 6/6.** Save the questions on the **Benchmark** tab, so you can rerun them after every change to instructions or tables.

If an answer is wrong, read Genie's SQL and fix the **instructions** or add an **example**, never the question. Typical causes: UTC instead of German dates, incomplete days, counting TEST rows.

📸 `lab9-step06-genie-answer.png` (an answer with its SQL visible)

---

### Step 07 — Genie definition into Git

```text
Create docs/genie/ask-the-energy-lakehouse.md with: the six source tables, the full instructions text, the three
example questions with SQL, the six benchmark questions with their expected-answer SQL, and a section
"Recreate per environment". Check `databricks bundle schema` for a Genie resource; if supported, add it for all targets.
```

Paste the instructions exactly as they are in the agent's **Instructions** tab, then commit.

---

### Step 08 — PR → staging → release → prod

```powershell
git branch --show-current            # feat/dashboard-genie, NOT master
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
git push -u origin HEAD
gh pr create --fill --base master
```

Wait ~10 s → `gh pr checks --watch` → ✓ → `gh pr merge --squash --delete-branch`:

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
gh run list --workflow deploy-staging.yml --limit 1                                  # ✓
git --no-pager ls-tree -r --name-only origin/master | Select-String "lvdash|genie"   # files present
gh release create v0.5.0 --target master --generate-notes --title "v0.5.0 - dashboard and Genie"
```

Approve → `deploy-prod` ✓.

**This release changed a gold table** (`is_complete`, from Troubleshooting P5), so **run the prod pipeline once** before checking the dashboard (Troubleshooting P10):

```powershell
gh workflow run daily-refresh.yml
```

Wait ~10 s, then `gh run watch` → ✓.

Then open **`[prod] Energy Market DE`** (owner **sp-energy-cicd**) → check all four pages show **prod** data → **Publish** with embedded credentials → copy the link.

**Prod Genie:** create `Ask the energy lakehouse (prod)` from `docs/genie/…md` with the six tables from **`energy_prod`**, then rerun the benchmarks.

📸 `lab9-prod-market.png`, your portfolio hero image.

---

### Step 09 — Pre-commit hook against commits on master

I ended up committing on `master` three times during this project (Troubleshooting P8). Make Git refuse it:

```text
Add a pre-commit hook that refuses commits on master and main (pre-commit framework, .pre-commit-config.yaml with
'no-commit-to-branch' from pre-commit-hooks, args --branch master --branch main). Add pre-commit to the dev
dependencies and document `uv run pre-commit install` in CLAUDE.md.
```

```powershell
uv sync ; uv run pre-commit install
git switch master ; git commit --allow-empty -m test     # expected: refused
git switch -                                              # back to your branch
```

### Step 10 — Tag

```powershell
git fetch origin ; git tag lab9-complete origin/master ; git push origin lab9-complete
```

---

## 5. Checkpoint

1. `src/energy_market_de.lvdash.json` and its resource YAML are committed. All dashboard tests pass.
2. No dataset SQL contains a schema name. Every dataset is used by a widget.
3. `[dev …]`, `[staging]` and `[prod] Energy Market DE` exist, each showing its own environment's data.
4. Prod KPIs show the latest **complete** day with a realistic load.
5. The prod dashboard is published.
6. The Genie Agent answers the 6 benchmarks correctly (mine: 6/6), and its definition is in `docs/genie/`.
7. Commits on `master` are refused locally.

---

## 6. Troubleshooting — problems I actually hit

### P1 — `DESCRIBE` fails in a dashboard dataset

```
[PARSE_SYNTAX_ERROR] Syntax error at or near 'gold_energy_daily'
== SQL ==
DESCRIBE QUERY DESCRIBE gold_energy_daily
```

**Cause:** dashboard datasets must be `SELECT` queries; the editor wraps them in `DESCRIBE QUERY …` to infer columns.
**Fix:** `SELECT * FROM <table> LIMIT 5`, or query `information_schema.columns`, or use `DESCRIBE` in the **SQL Editor**.

### P2 — Counters showed "1"

**Cause:** the counter aggregated with **COUNT** over a one-row dataset.
**Fix:** widget → **Value** → the field, aggregation **Sum**.

### P3 — The assistant qualified every table name

**Symptom:** after using the assistant/Generate, every dataset read `FROM workspace.energy_dev.…`. In prod this would show **dev** data.
**Fix:** remove the prefix in every dataset. Add *"Use unqualified table names"* to every assistant prompt. The test `no dataset SQL contains a schema name` enforces it.

### P4 — Unqualified names failed: search path `workspace.default`

```
[TABLE_OR_VIEW_NOT_FOUND] The table or view `gold_energy_hourly` cannot be found.
Search path: [`system`.`session`, `system`.`builtin`, `system`.`ai`, `workspace`.`default`].
```

**Cause:** the dataset hadn't stored a default schema, although the editor seemed to show `energy_dev`.
**Fix:** in the dataset editor, **select** `energy_dev` in the schema dropdown explicitly, then run again. Verify in the export: every dataset shows `schema = energy_dev`.

(The **Schema** tab under the results shows the query's *output columns*. It's not the database schema.)

### P5 — The KPI showed 596 GWh for a "complete" day

**Symptom:** the daily load counter showed **596 GWh** for 23 October. Germany uses ~1,100–1,500 GWh a day, and the prod hourly table summed to **1,375 GWh** for the same day.

**Investigation:**

```sql
SELECT d.date_local, d.hour_count, round(d.total_load_mwh / 1000, 1) AS daily_table_gwh,
       round(sum(h.total_load_mwh) / 1000, 1) AS sum_of_hours_gwh
FROM workspace.energy_dev.gold_energy_daily d
JOIN workspace.energy_dev.gold_energy_hourly h ON to_date(h.hour_local) = d.date_local
WHERE d.date_local >= date'2026-10-18'
GROUP BY d.date_local, d.hour_count, d.total_load_mwh ORDER BY d.date_local;
```

| date_local | hour_count | daily_table_gwh | sum_of_hours_gwh |
| --- | --- | --- | --- |
| 2026-10-22 | 24 | 1385.1 | 1385.1 |
| 2026-10-23 | 24 | **596.0** | **596.0** |

The daily table summed **correctly**. But dev had been refreshed on the evening of the 23rd, when SMARD hadn't published load values for many hours yet. Those hours existed as rows with `total_load_mwh = NULL`, and `hour_count` counted **rows**, so the day looked complete.

**Fix:** a real completeness definition in `gold_energy_daily`:
- `load_hours = count(total_load_mwh)`, `price_hours = count(price_eur_mwh)`
- `expected_hours` (23/24/25, DST-aware)
- `is_complete = (load_hours = expected_hours AND price_hours = expected_hours)`

The KPIs use only `is_complete` days, and Genie's instructions say the same.

**Lesson:** every pipeline check passed. Only the **plausibility check against domain knowledge** found it.

### P6 — A cross-table contract rule broke `verify_contracts`

```
gold_energy_daily complete_day_load_matches_hourly: query failed: [ASSIGNMENT_ARITY_MISMATCH]
The number of columns or variables assigned or aliased: 18 does not match the number of source expressions: 1.
```

**Cause:** the new rule (*daily total = sum of hourly*) needs a second table, but `row_condition` wraps a condition over one table.
**Fix:** a new rule type **`sql_check`**: a full query returning `violations`, run as-is:

```sql
SELECT count(*) AS violations
FROM {catalog}.{schema}.gold_energy_daily d
JOIN (SELECT to_date(hour_local) AS date_local, sum(total_load_mwh) AS hourly_sum
      FROM {catalog}.{schema}.gold_energy_hourly GROUP BY 1) h USING (date_local)
WHERE d.is_complete AND abs(d.total_load_mwh - h.hourly_sum) > 0.001 * h.hourly_sum
```

Also `max_retries: 0` for `verify_contracts`: it had run **three times** with the same failure.

### P7 — `generate --force` overwrote the resource YAML

```
Writing configuration to resources/energy_market_de.dashboard.yml
git checkout -- resources/energy_market_de.dashboard.yml
error: pathspec '…' did not match any file(s) known to git
```

**Cause:** `bundle generate dashboard --force` writes **both** files, and the tuned YAML had never been committed, so it couldn't be restored.
**Fix:** re-apply the settings once, **commit the YAML**, and from then on restore it right after every export (the routine in Step 03).

### P8 — Committing on `master` (again)

```
head branch "master" is the same as base branch "master", cannot create a pull request
```

**Fix without losing work:**

```powershell
git switch -c feat/dashboard-genie-final       # takes the commits along
git branch -f master origin/master             # local master back to GitHub's state
git push -u origin HEAD ; gh pr create --fill --base master
```

**Prevention:** the pre-commit hook (Step 09). GitHub's ruleset blocks the *push*; the hook blocks the *commit*.

### P9 — A Genie example turned into parameters `:param :param1`

**Cause:** the question was typed into the **SQL code box**; `?` and the text after it were read as parameters.
**Fix:** the question goes in the **top line**, the SQL in the **code box**. Delete the stray parameters.

### P10 — Prod KPIs failed right after the release

**Symptom:** `[prod] Energy Market DE` showed the charts, but all three counters: *"Unable to render visualization"*.
**Cause:** `v0.5.0` deployed the new gold columns (`is_complete`), but the **prod pipeline hadn't run since**, so the column didn't exist in prod yet.
**Fix:** run the prod refresh once after a release that changes tables (`gh workflow run daily-refresh.yml`).

### P11 — A hand-made prod dashboard next to the deployed one

**Symptom:** the list showed `[Prod] Energy Market DE` (owner: me, hand-made) next to `[prod] Energy Market DE` (owner: sp-energy-cicd, deployed).
**Fix:** delete the hand-made copy. Prod comes **only** from the bundle. Keep the unprefixed `Energy Market DE` as the editing source for exports.

### Other things worth knowing

| Situation | Explanation |
| --- | --- |
| Empty *forecast vs. actual* / *accuracy* in dev | Dev isn't refreshed daily; prod fills them |
| Generation mix drops to 0 at the right edge | The newest hours aren't published yet (NULL is drawn as 0). Filter `WHERE total_load_mwh IS NOT NULL` if it bothers you. |
| "Top findings (24 h)" empty | Use "latest detection run" instead of a fixed 24 h window (`ds_dq_top`) |
| Genie and SQL use UTC "today" | The warehouse runs in UTC. Instructions and examples must use German dates. |
| Local metric view appeared in the dataset list | *Convert to Local Metric View* was clicked. Delete it; plain SQL datasets are enough here. |

---

## 7. What you learned

- Building an **AI/BI dashboard**: datasets, widgets, pages, counters, dual axes, stacked areas
- **Dashboards as code:** UI-first design, `bundle generate dashboard`, a repeatable export routine
- **One dashboard for three environments** with unqualified names + `dataset_catalog`/`dataset_schema`
- **Tests for dashboards:** no hardcoded schemas, no unused datasets, no empty pages
- **Genie Agents:** sources, instructions, example SQL, **benchmarks**, and why instructions matter more than the question
- A **plausibility check** that found a real gold-layer bug, and defining **completeness** properly (DST-aware)
- **Cross-table data-quality rules** (`sql_check`)
- Release discipline: **schema changes need a pipeline run**; prod artifacts come **only** from code
- Preventing repeated human mistakes with **automation** (pre-commit hook)

**Interview-ready summary:** *"I built an AI/BI dashboard for the energy lakehouse (market overview, merit-order effect, day-ahead forecast vs. actual, data-quality report with an LLM summary), defined as code and deployed by CI to dev, staging and prod, with per-environment schemas and tests that reject hardcoded schemas or unused datasets. A plausibility check on the dashboard exposed a gold-layer bug: days counted as complete although SMARD hadn't published half the hours. I introduced a DST-aware completeness definition and a cross-table contract rule. A Genie Agent with curated instructions and example SQL answers business questions in plain language and passes all six benchmark questions."*

---

## 8. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-24/26 |
| OS | Windows 11 (local), ubuntu-latest (GitHub runners) |
| Databricks | Free Edition, Serverless Starter Warehouse, one workspace for dev/staging/prod |
| Dashboard | AI/BI, 4 pages, 10 datasets, `dataset_catalog`/`dataset_schema` per target |
| Genie | Genie Agent, 6 gold tables, 3 examples, **benchmark 6/6** |
| Release | v0.5.0 |
| Databricks CLI | `<output of databricks -v>` |

**Next:** Lab 10 — Documentation, packaging and cleanup: README with an architecture diagram, ADRs, runbook, wheel packaging, GitHub Actions updates, and CV bullets. After that, Project 1 is portfolio-ready.
