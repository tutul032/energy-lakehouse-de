# Lab 6 — Data Products and Data Contracts: Promises About Your Data, Checked Automatically

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 5 — CI/CD from dev to staging to prod. Next: Lab 7 — Day-ahead load and price forecast.

## 1. Goal

After Lab 5 your gold tables are deployed professionally, but they're still "just tables". Nobody outside your head knows what each column means, how fresh the data is supposed to be, which values are allowed, who owns it, or what happens when a column changes.

In this lab, each gold table becomes a **data product** with a **data contract**: a versioned, machine-readable promise about its schema, meaning, quality, freshness and access. That promise is **checked automatically twice**:

1. **In every pull request (offline):** if code and contract disagree, for example because a column was renamed, CI turns red **before merge**.
2. **In every job run, in every environment (runtime):** the real table in Unity Catalog is compared with the contract: schema, quality rules, freshness. Comments, tags and grants are **generated from the contract**.

By the end you will have:

- `contracts/*.yaml`: one contract per gold table (`gold_energy_hourly`, `gold_energy_daily`, `gold_energy_weather_hourly`)
- `contracts.py`: a typed loader plus SQL generators for comments, tags, grants, quality and freshness
- A **breaking-change guard** in `pytest` that you've seen fail on purpose
- Two new job tasks: `apply_contracts` and `verify_contracts`
- Human-readable **product pages** in `docs/data-products/`
- Unity Catalog governance from the contract: descriptions, column comments, tags, and grants to a consumer group
- Release `v0.2.0` in prod

**Time:** about 4–6 hours.

---

## 2. Concepts: what is a data contract, and why do we need one?

### 2.1 The problem contracts solve

Imagine a dashboard, an ML model and an analyst all reading `gold_energy_hourly`. One day a developer renames `price_eur_mwh` to `price`, because it's shorter. The pipeline runs green, but the dashboard breaks, the model training job fails at night, and the analyst's notebook silently returns nulls. Nobody was warned, because the **producer** didn't know who the **consumers** were or what they relied on.

Other everyday versions of the same problem:

- A column suddenly contains values in a different **unit** (MWh vs. GWh).
- The table **stops updating** for three days, and nobody notices until a manager asks.
- **Duplicate rows** appear after a backfill, and every sum is now wrong.
- An analyst can't find out what `renewables_share` means, or whether it's 0–1 or 0–100.
- Access is granted ad hoc to individuals, and nobody knows who can read what.

All of these come from **implicit expectations**. A data contract makes them **explicit and testable**.

### 2.2 Definition

> A **data contract** is a formal, versioned agreement between the **producer** of a dataset and its **consumers**, describing what the dataset contains and what guarantees it gives, written in a machine-readable format so that it can be **validated automatically**.

It's like an API specification (OpenAPI) for a table: consumers build against the contract, not against whatever the table happens to look like today.

### 2.3 What goes into a contract

| Section | Question it answers | Example from this project |
| --- | --- | --- |
| **Identity & version** | Which product, which version of the promise? | `id: energy.gold_energy_hourly`, `version: 1.0.0` |
| **Ownership** | Who is responsible? Whom do I contact? | `owner: {name, email}` |
| **Description & grain** | What is it, and what is one row? | "One row per hour_utc" |
| **Schema** | Which columns, types, nullability, meaning? | `price_eur_mwh DOUBLE, nullable, "Day-ahead price DE-LU, hourly average (EUR/MWh)"` |
| **Primary key** | What makes a row unique? | `[hour_utc]` |
| **Quality rules** | Which statements are always true? | `renewables_share BETWEEN 0 AND 1` |
| **SLA** (service level agreement) | How fresh, how often updated? | `freshness_hours: 72`, daily ~05:17 UTC |
| **Tags / classification** | How is it categorised, licensed, sensitive? | `layer: gold`, `license: CC-BY-4.0` |
| **Consumers / access** | Who may read it? | `group: energy-consumers, access: SELECT` |

We use a simple YAML format **inspired by the Open Data Contract Standard (ODCS)**, an open specification for data contracts. Our format is smaller, but the ideas are the same, so moving to full ODCS later is straightforward.

### 2.4 Data product vs. data contract vs. table

| Term | What it is |
| --- | --- |
| **Table** | Storage: rows and columns in Unity Catalog |
| **Data contract** | The **promise** about a table: schema, meaning, quality, SLA, access |
| **Data product** | Table **plus** contract **plus** owner, documentation, governance and support. Something a consumer can find, understand, trust and request access to. |

In this lab each gold table becomes a data product. The product page (`docs/data-products/*.md`) and the Unity Catalog metadata are how consumers "see" it.

### 2.5 Versioning contracts: semantic versioning

Contracts change, but **consumers must be able to tell whether a change can break them**. We use semantic versioning, `MAJOR.MINOR.PATCH`:

| Change | Breaks consumers? | Version bump | Example |
| --- | --- | --- | --- |
| Remove or rename a column, change its type or meaning, change the grain | **Yes** | **MAJOR** (1.0.0 → 2.0.0) | `price_eur_mwh` → `price` |
| Add a new column, add a quality rule that already holds | No | **MINOR** (1.0.0 → 1.1.0) | new column `residual_load_mwh` |
| Fix a description or typo, clarify the SLA wording | No | **PATCH** (1.0.0 → 1.0.1) | better column comment |

A MAJOR change should be announced to consumers in advance. In bigger organisations the old version runs in parallel for a while.

### 2.6 How contracts are enforced: two moments

| | **Shift-left check (CI)** | **Runtime check (job)** |
| --- | --- | --- |
| When | Every pull request, before merge | Every pipeline run, in dev, staging and prod |
| Needs Spark/Databricks? | No (fast, offline `pytest`) | Yes |
| Catches | Code and contract disagree (rename, removed column, wrong description); invalid contract files | Real table drifted from the contract; bad data (rules violated); stale data (SLA missed) |
| Effect | PR can't be merged | Job fails, you get an email, the product is flagged "broken" |

You need **both**. CI can't see data, and the runtime check comes too late to prevent a bad merge.

### 2.7 Governance generated from the contract

Instead of setting comments, tags and grants by hand (and forgetting to update them), a job task **generates them from the contract** on every run:

- `COMMENT ON TABLE …` and column comments: documentation in Catalog Explorer
- `ALTER TABLE … SET TAGS (…)`: searchable classification
- `GRANT SELECT ON TABLE … TO \`energy-consumers\``: access for the consumer group

The contract is the **single source of truth**. If you change it, Unity Catalog follows automatically.

### 2.8 Where the "single source of truth" for column names lives

There's one subtle design point. The pipeline code and the contract both describe the gold columns. To compare them, the **pipeline must own its column list in code** (constants in `transforms.py`), and the contract must be a **separate** file. If the code read its columns *from* the contract, the comparison would compare the contract with itself and could never fail (Troubleshooting P3).

```
transforms.py  GOLD_TABLE_COLUMNS   ← used by src/pipelines/gold*.py (what the code PRODUCES)
contracts/*.yaml  columns           ← what we PROMISE
tests/test_contracts.py             ← must be equal, otherwise CI fails
```

---

## 3. Prerequisites

- Labs 0–5 completed. Staging and prod deploy through CI.
- Repo `C:\dev\energy-lakehouse-de`, on a clean, up-to-date default branch
- Golden rules from Lab 5 (branch first, local checks before push, read logs, never paste secrets)
- **New golden rule:** **commit before you experiment.** A `git checkout -- <file>` resets to the last commit and throws away *uncommitted* work too (Troubleshooting P2).

---

## 4. Steps

### Step 01 — Remove the old dev landing volume

**What and why:** since Lab 5, everything reads from `energy_raw`. An unused volume with write access for a service principal is unnecessary risk. Least privilege means removing access that's no longer needed.

Verify nothing uses it:

```powershell
Select-String -Path databricks.yml, resources\*.yml, src\**\*.py, scripts\* -Pattern "energy_dev/landing|energy_dev.landing"
```

**Expected:** no output.

```sql
SELECT DISTINCT regexp_extract(source_file, '^(/Volumes/[^/]+/[^/]+/[^/]+)', 1) AS volume
FROM workspace.energy_dev.bronze_smard_raw;
```

**Expected:** only `/Volumes/workspace/energy_raw/landing`.

Remove it:

```sql
REVOKE READ VOLUME, WRITE VOLUME ON VOLUME workspace.energy_dev.landing FROM `<refresh-sp-application-id>`;
DROP VOLUME workspace.energy_dev.landing;
SHOW VOLUMES IN workspace.energy_dev;
```

**Expected:** `landing` no longer appears.

---

### Step 02 — Create a consumer group

**What and why:** contracts grant read access to **groups**, never to individual people. When someone joins or leaves, you change group membership, not dozens of grants.

**Settings → Identity and access → Groups → Manage → Add group** → `energy-consumers` → add yourself as a member.

**Expected:** the group appears in the list.

If your workspace doesn't allow creating groups, grant to your user instead and note the limitation in the contract (`consumers: [{user: <email>, access: SELECT}]`).

📸 `lab6-step02-group.png`

---

### Step 03 — Write the contracts and the tooling

**What and why:** the contracts themselves, a typed loader, SQL generators, the two notebooks, the CI tests and the documentation generator.

Branch first:

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c feat/data-contracts
```

**Option A — Claude Code prompt** (already includes the fixes for P3):

```text
Read CLAUDE.md first. Add data contracts for the gold tables. Do not deploy.

1. contracts/ — one YAML per gold table: gold_energy_hourly.yaml, gold_energy_daily.yaml,
   gold_energy_weather_hourly.yaml. Format (ODCS-inspired), example:
     apiVersion: v1
     kind: DataContract
     id: energy.gold_energy_hourly
     version: 1.0.0
     status: active
     owner: {name: <my name>, email: <alert_email>}
     description: Hourly German power load, day-ahead price and generation mix (SMARD, CC BY 4.0).
     table: gold_energy_hourly            # catalog/schema come from the target
     grain: one row per hour_utc
     primary_key: [hour_utc]
     tags: {layer: gold, domain: energy, data_product: energy_market_hourly, license: CC-BY-4.0, source: smard}
     sla: {freshness_hours: 72, schedule: daily ~05:17 UTC}
     consumers: [{group: energy-consumers, access: SELECT}]
     columns:
       - {name: hour_utc, type: TIMESTAMP, nullable: false, description: Start of the hour in UTC}
       - ...every column the pipeline actually produces, with real types and a one-line description
     quality:
       - {name: unique_hour, type: unique, columns: [hour_utc]}
       - {name: share_in_range, type: row_condition, sql: "renewables_share IS NULL OR renewables_share BETWEEN 0 AND 1", max_violation_pct: 0}
       - {name: load_plausible, type: row_condition, sql: "total_load_mwh IS NULL OR total_load_mwh BETWEEN 20000 AND 100000", max_violation_pct: 1}
   Derive the column lists and types from the actual pipeline code in src/pipelines/gold*.py.
   Use exactly the type names that information_schema.columns reports (e.g. TIMESTAMP, TIMESTAMP_NTZ, DOUBLE, DATE, BIGINT).
   Pick sensible rules for daily (hours_count 23–25 except the newest 2 days) and energy_weather (wind 0–75 m/s).
2. src/energy_lakehouse/contracts.py — pydantic models + loader for the YAML, and pure functions that render SQL:
   COMMENT ON TABLE / ALTER TABLE ... ALTER COLUMN ... COMMENT, ALTER TABLE ... SET TAGS,
   GRANT SELECT ON TABLE ... TO `group`, quality-check queries (returning violation counts), freshness query.
   All SQL takes catalog and schema as parameters; quote identifiers with backticks.
3. Single source of truth for gold columns IN CODE: constants in src/energy_lakehouse/transforms.py
   (GOLD_ENERGY_HOURLY_COLUMNS etc. as (name, type, description) tuples, plus GOLD_TABLE_COLUMNS mapping
   table name -> list). transforms.py must NOT read contracts/*.yaml. The pipeline code in src/pipelines/gold*.py
   must use these constants (column selection and comments).
4. tests/test_contracts.py (offline): every contract parses; versions are semver; primary_key ⊆ columns;
   one contract per gold table; column names AND descriptions in each contract == GOLD_TABLE_COLUMNS[table]
   (order-insensitive; on failure list "only in code" / "only in contract");
   a test proving the guard works (a copy of a contract with one renamed column must be reported);
   rendered SQL snapshot for one contract.
5. src/notebooks/04_apply_contracts.py — params catalog, schema; for each contract apply comments, tags, grants.
   Idempotent. Print a summary.
6. src/notebooks/05_verify_contracts.py — params catalog, schema; for each contract:
   (a) schema check via {catalog}.information_schema.columns: missing/extra columns or type mismatch → fail;
   (b) quality rules → fail if violation pct > max_violation_pct;
   (c) freshness: max timestamp column vs now → fail if older than sla.freshness_hours.
   Print one result table; raise with a clear message listing ALL failures (not just the first).
7. resources/daily_refresh_job.yml — add tasks apply_contracts (after quality_checks) and verify_contracts
   (after apply_contracts), all targets.
8. scripts/render_contract_docs.py → docs/data-products/<table>.md (product page: description,
   owner, SLA, columns table, quality rules, how to request access). Link them from README.
9. Add pydantic and pyyaml to dependencies (update uv.lock). Do NOT add machine-specific uv settings
   (e.g. python-preference) to pyproject.toml. Update CLAUDE.md (contracts section: how to change a contract,
   semver rules: removing/renaming/type change = MAJOR, new column = MINOR, description = PATCH).
List changed files and checkpoint commands.
```

**Option B:** copy from my repo at tag `lab6-complete`.

**Expected result — new or changed files:**

```
energy-lakehouse-de/
├── contracts/
│   ├── gold_energy_hourly.yaml           (new)
│   ├── gold_energy_daily.yaml            (new)
│   └── gold_energy_weather_hourly.yaml   (new)
├── docs/data-products/                   (new, generated)
├── resources/daily_refresh_job.yml       (changed: 2 new tasks)
├── scripts/render_contract_docs.py       (new)
├── src/
│   ├── energy_lakehouse/
│   │   ├── contracts.py                  (new)
│   │   └── transforms.py                 (changed: GOLD_*_COLUMNS constants)
│   ├── notebooks/
│   │   ├── 04_apply_contracts.py         (new)
│   │   └── 05_verify_contracts.py        (new)
│   └── pipelines/gold*.py                (changed: use the constants)
├── tests/test_contracts.py               (new)
├── pyproject.toml, uv.lock               (changed: pydantic, pyyaml)
└── CLAUDE.md, README.md                  (changed)
```

#### Anatomy of a contract (shortened example)

```yaml
apiVersion: v1
kind: DataContract
id: energy.gold_energy_hourly
version: 1.0.0                     # semver: MAJOR.MINOR.PATCH
status: active
owner:
  name: Tutul
  email: you@example.com
description: Hourly German power load, day-ahead price and generation mix (SMARD, CC BY 4.0).
table: gold_energy_hourly          # catalog and schema come from the bundle target
grain: one row per hour_utc
primary_key: [hour_utc]
tags:
  layer: gold
  domain: energy
  data_product: energy_market_hourly
  license: CC-BY-4.0
  source: smard
sla:
  freshness_hours: 72              # newest hour must be at most 72 h old
  schedule: daily ~05:17 UTC
consumers:
  - group: energy-consumers
    access: SELECT
columns:
  - {name: hour_utc,         type: TIMESTAMP,     nullable: false, description: Start of the hour in UTC.}
  - {name: hour_local,       type: TIMESTAMP_NTZ, nullable: false, description: Start of the hour in Europe/Berlin wall-clock time.}
  - {name: total_load_mwh,   type: DOUBLE,        nullable: true,  description: Total electricity consumption in Germany (MWh).}
  - {name: price_eur_mwh,    type: DOUBLE,        nullable: true,  description: Day-ahead price DE-LU, hourly average (EUR/MWh).}
  - {name: renewables_share, type: DOUBLE,        nullable: true,  description: (solar + wind onshore + wind offshore) / total load, 0–1.}
  # ... solar_mwh, wind_onshore_mwh, wind_offshore_mwh, lignite_mwh, hard_coal_mwh, natural_gas_mwh, renewables_mwh
quality:
  - {name: unique_hour,    type: unique,        columns: [hour_utc]}
  - {name: share_in_range, type: row_condition, sql: "renewables_share IS NULL OR renewables_share BETWEEN 0 AND 1", max_violation_pct: 0}
  - {name: load_plausible, type: row_condition, sql: "total_load_mwh IS NULL OR total_load_mwh BETWEEN 20000 AND 100000", max_violation_pct: 1}
```

How to read the quality rules:
- **`unique`:** no two rows may share the same `hour_utc`. This catches duplicates after backfills.
- **`row_condition`** with **`max_violation_pct`:** the SQL must be true for every row, except a tolerated percentage. `0` means "never". `1` means "a rare outlier is acceptable, a systematic problem is not".
- Rules are **null-safe** (`x IS NULL OR …`). Nulls are handled by `nullable`, not by every rule (the lesson from Lab 2).

---

### Step 04 — Local checks and product pages

```powershell
uv sync
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
uv run python scripts/render_contract_docs.py
Get-ChildItem docs\data-products
```

**Expected result:**
- all tests pass. In my run that was **181** tests, including 67 in `test_contracts.py`.
- three `.md` files in `docs/data-products`

Open one in VS Code with the preview (**Ctrl+Shift+V**). It reads like a product page: description, owner, SLA, a column table, quality rules, and how to request access.

If `uv run python` fails with *"An Application Control policy has blocked this file (os error 4551)"*, see Troubleshooting P1.

**Commit now (new golden rule), before any experiment:**

```powershell
git branch --show-current          # feat/data-contracts
git add -A ; git commit -m "wip: data contracts, apply/verify notebooks, product docs"
```

📸 `lab6-step04-product-page.png`

---

### Step 05 — Deploy dev and run the job

```powershell
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run daily_refresh_job -t dev -p free
```

**Expected result:** four tasks, all `SUCCESS`:

```
run_pipeline → quality_checks → apply_contracts → verify_contracts
```

The `verify_contracts` output shows one result table, for example:

```
table                        check                  result   detail
gold_energy_hourly           schema                 PASS     12 columns match
gold_energy_hourly           unique_hour            PASS     0 violations
gold_energy_hourly           share_in_range         PASS     0.00 % (max 0 %)
gold_energy_hourly           load_plausible         PASS     0.00 % (max 1 %)
gold_energy_hourly           freshness              PASS     latest 2026-10-21 23:00 UTC (< 72 h)
gold_energy_daily            ...
gold_energy_weather_hourly   ...
```

(Exact wording and counts depend on the generated code and your data.)

📸 `lab6-step05-verify-output.png`

---

### Step 06 — See the governance in Unity Catalog

**Catalog Explorer → workspace → energy_dev → gold_energy_hourly:** the table description, column comments and tags are now filled in. The **Permissions** tab shows `energy-consumers` with `SELECT`.

📸 `lab6-step06-catalog-tags.png`

In SQL:

```sql
-- tags
SELECT table_name, tag_name, tag_value
FROM workspace.information_schema.table_tags
WHERE schema_name = 'energy_dev'
ORDER BY table_name, tag_name;

-- grants
SHOW GRANTS ON TABLE workspace.energy_dev.gold_energy_hourly;

-- column comments
SELECT column_name, data_type, comment
FROM workspace.information_schema.columns
WHERE table_schema = 'energy_dev' AND table_name = 'gold_energy_hourly'
ORDER BY ordinal_position;
```

**Expected result:**
- 5 tags per gold table (`layer`, `domain`, `data_product`, `license`, `source`)
- `energy-consumers` with `SELECT`
- a comment on every column, identical to the contract descriptions

**Why generated, not manual:** if someone edits a comment by hand in the UI, the next job run sets it back to the contract. The contract wins, so documentation can't drift.

---

### Step 07 — Prove the breaking-change guard (shift-left)

**What and why:** a test you've never seen fail could be broken. Prove that the guard really catches a breaking change.

**Precondition:** your Lab 6 work is committed (Step 04). Check:

```powershell
git status          # "nothing to commit, working tree clean"
git log --oneline -1
```

Simulate a careless change: in `src/energy_lakehouse/transforms.py`, inside `GOLD_ENERGY_HOURLY_COLUMNS`, rename the **string** `"price_eur_mwh"` to `"price"`. **Save** (the tab must show ✕, not ●). Don't touch the contract.

```powershell
git diff --stat                                  # must show transforms.py changed
uv run pytest tests/test_contracts.py -q
```

**Expected result:** 4 failures:

```
FAILED tests/test_contracts.py::test_columns_match_transforms[gold_energy_hourly]
       At index 5 diff: 'price_eur_mwh' != 'price'
FAILED tests/test_contracts.py::test_columns_match_transforms[gold_energy_weather_hourly]
FAILED tests/test_contracts.py::test_descriptions_match_pipeline_comments[gold_energy_hourly]
       Left contains 1 more item:  {'price_eur_mwh': 'Day-ahead price DE-LU, hourly average (EUR/MWh).'}
       Right contains 1 more item: {'price': 'Day-ahead price DE-LU, hourly average (EUR/MWh).'}
FAILED tests/test_contracts.py::test_descriptions_match_pipeline_comments[gold_energy_weather_hourly]
============================ 4 failed, 63 passed ============================
```

Notice that the **weather table fails too**: it extends the hourly columns, so the guard follows dependencies.

In real life, this change could only be merged by **also** updating the contract **and** bumping its version to **2.0.0** (MAJOR, because it breaks consumers).

Undo the demo. This is safe only because the work is committed:

```powershell
git checkout -- src/energy_lakehouse/transforms.py
uv run pytest -q
```

**Expected:** all tests pass again.

📸 `lab6-step07-breaking-change-caught.png`

---

### Step 08 — Prove the runtime check

**What and why:** the runtime check must fail loudly when a promise is broken, here the SLA.

In `contracts/gold_energy_hourly.yaml`, temporarily set `freshness_hours: 0`:

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run daily_refresh_job -t dev -p free
```

**Expected result:** `run_pipeline`, `quality_checks` and `apply_contracts` succeed, then `verify_contracts` **fails** with a message such as:

```
Contract violations:
- gold_energy_hourly: freshness FAIL — latest hour 2026-10-21 23:00 UTC is older than 0 h
```

You also receive the job's failure email.

Revert to `72`, redeploy, rerun: all green.

```powershell
git checkout -- contracts/gold_energy_hourly.yaml
databricks bundle deploy -t dev -p free
databricks bundle run daily_refresh_job -t dev -p free
```

📸 `lab6-step08-sla-violation.png`

Other things you can try the same way (and revert): set a quality rule to an impossible condition, or add a column to the contract that the table doesn't have. The schema check reports it as *missing*.

---

### Step 09 — Pull request → staging → prod

```powershell
git branch --show-current            # feat/data-contracts
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
git add -A ; git commit -m "feat: data contracts for gold tables (apply + verify), product docs"
git push -u origin feat/data-contracts
gh pr create --fill --base master
```

Wait ~10 seconds (so the check for your newest commit exists), then:

```powershell
gh pr checks --watch
```

**Expected:** `✓ ci/ci (pull_request)`. If it fails in `uv sync --locked`, see Troubleshooting P4.

Merge and deploy staging:

```powershell
gh pr merge --squash --delete-branch
git switch master ; git fetch origin ; git reset --hard origin/master
```

Wait ~10 s, then `gh run watch` (deploy-staging). **Expected:** ✓, including `apply_contracts` and `verify_contracts` in staging.

Release to prod:

```powershell
gh release create v0.2.0 --target master --generate-notes --title "v0.2.0 - data contracts"
```

Approve in the browser (**Actions → deploy-prod → Review deployments → production → Approve and deploy**), wait for ✓, then run the daily refresh once:

```powershell
gh workflow run daily-refresh.yml
```

Wait ~10 s, then `gh run watch`.

**Check prod governance:**

```sql
SELECT table_name, tag_name, tag_value FROM workspace.information_schema.table_tags
WHERE schema_name = 'energy_prod' ORDER BY table_name, tag_name;
SHOW GRANTS ON TABLE workspace.energy_prod.gold_energy_hourly;
```

**Expected:** the same tags and grants as in dev, applied by `sp-energy-cicd` as the table owner.

**Why `v0.2.0`?** A new, backward-compatible feature is a MINOR version. It's the same semver logic as for the contracts.

---

### Step 10 — Tag

```powershell
git tag lab6-complete origin/master
git push origin lab6-complete
```

---

## 5. Checkpoint

1. `contracts/` holds one valid contract per gold table.
2. `uv run pytest` passes, and you've **seen** the guard fail on a renamed column.
3. The job has four green tasks in dev, staging and prod.
4. `verify_contracts` fails loudly on a broken SLA, and passes again after reverting.
5. Catalog Explorer shows descriptions, column comments, tags and a `SELECT` grant for `energy-consumers` on all gold tables in prod.
6. `docs/data-products/` contains three product pages, linked from the README.
7. The old `energy_dev.landing` volume is gone.

---

## 6. How to change a contract (day-to-day workflow)

| You want to… | Do this | Version |
| --- | --- | --- |
| Improve a column description | Edit the description in **both** the contract and `transforms.py` (the tests compare them) | PATCH |
| Add a column | Add it in the pipeline, in `transforms.py` and in the contract | MINOR |
| Add a quality rule | Add it to the contract. Check first that current data passes (run `verify_contracts` in dev). | MINOR |
| Rename or remove a column, change a type | Announce it to consumers, update pipeline + `transforms.py` + contract together | **MAJOR** |
| Tighten the SLA | Only if the pipeline can really deliver it | MINOR (MAJOR if consumers relied on the old value) |

Then follow the normal flow: branch → PR (guard tests) → merge (staging verifies) → release (prod).

---

## 7. Troubleshooting — problems I actually hit

### P1 — `uv run python` blocked: "An Application Control policy has blocked this file (os error 4551)"

**Symptom:**

```
uv run python scripts/render_contract_docs.py
error: Failed to spawn: python
  cause: An Application Control policy has blocked this file. (os error 4551)
```

`uv run pytest` failed the same way.

**Investigation:** Windows logs every block in the Code Integrity log:

```powershell
Get-WinEvent -LogName "Microsoft-Windows-CodeIntegrity/Operational" -MaxEvents 10 |
  Where-Object { $_.Message -match "python" } |
  Select-Object TimeCreated, Id, @{n="Message";e={$_.Message.Split("`n")[0]}} | Format-List
```

Result (events 3033 / 3077):

```
Code Integrity determined that a process (...\.venv\Scripts\pytest.exe) attempted to load
...\.venv\Scripts\python.exe that did not meet the Enterprise signing level requirements ...
```

**Cause:** **Smart App Control** (Windows 11) was on. It blocks executables without a trusted signature or reputation. uv's own Python build, copied into `.venv\Scripts\python.exe`, is unsigned, and Windows re-evaluated it, although the same file had worked for days.

**Fix (keeps Smart App Control on):** use the **signed** Python from python.org:

```powershell
winget install --id Python.Python.3.12 -e
# close the terminal AND VS Code, open a new terminal
cd C:\dev\energy-lakehouse-de
py -3.12 -c "import sys; print(sys.executable)"      # ...\Programs\Python\Python312\python.exe
Remove-Item .venv -Recurse -Force
uv venv --python-preference only-system --python 3.12
uv sync
uv run python -c "import sys; print(sys.executable)"
uv run pytest -q
```

Make it permanent **for your machine only** (not in the repo, see P4):

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\uv" | Out-Null
Set-Content -Path "$env:APPDATA\uv\uv.toml" -Value 'python-preference = "only-system"'
```

In VS Code: **Ctrl+Shift+P → Python: Select Interpreter** → `.venv\Scripts\python.exe`.

**Should you turn Smart App Control off instead?** On a personal machine it's your choice, but know the facts: on most Windows 11 builds **it can't be switched back on without resetting Windows**. Microsoft Defender antivirus keeps protecting you either way. Try the signed-Python fix first. On a company-managed PC, never bypass the policy; ask IT.

---

### P2 — `git checkout -- <file>` deleted uncommitted work

**Symptom:** after undoing the breaking-change demo with `git checkout -- src/energy_lakehouse/transforms.py`, 8 tests failed:

```
AttributeError: module 'energy_lakehouse.transforms' has no attribute 'GOLD_TABLE_COLUMNS'. Did you mean: 'GOLD_HOURLY_COLUMNS'?
```

**Cause:** `git checkout -- <file>` resets the file to the **last commit**. Claude Code's Lab 6 changes to `transforms.py` weren't committed yet, so they were thrown away together with the demo rename.

**Fix:** restore from **VS Code's local history**, which is independent of Git: open the file → **Explorer → TIMELINE** panel → pick the newest entry before the checkout → view the diff → right-click → **Restore Contents** → save. Then commit immediately:

```powershell
git add -A ; git commit -m "wip: data contracts, apply/verify notebooks, product docs"
```

If the Timeline doesn't have it, ask Claude Code to rebuild the missing constants from the tests and contracts.

**Lesson:** **commit before you experiment.** A WIP commit costs nothing, and you can squash it later. Alternative for experiments: `git stash` saves *all* changes and gives them back with `git stash pop`.

---

### P3 — The guard test passed although a column was "renamed"

**Symptom:** after renaming `price_eur_mwh` → `price` in `transforms.py`:

```
tests\test_contracts.py ....................................................... 67 passed
```

**Investigation:**

```powershell
git diff src/energy_lakehouse/transforms.py      # empty = not saved
Select-String -Path src\energy_lakehouse\transforms.py -Pattern "price"
uv run python -c "from energy_lakehouse import transforms as t; print([c[0] for c in t.GOLD_TABLE_COLUMNS['gold_energy_hourly']])"
```

**Cause:** the rename hadn't been **saved**, or hadn't been applied inside the list the test compares against. After saving the change in `GOLD_ENERGY_HOURLY_COLUMNS`, the guard failed exactly as intended (Step 07).

**A real design risk to check:** if `transforms.py` had **loaded its columns from `contracts/*.yaml`**, the test would compare the contract with itself and could **never** fail. Make sure the code owns its column list (Concepts 2.8), and keep a test that proves the guard detects a renamed column.

**Lesson:** **test the test.** A guard you've never seen fail gives false confidence. Always do a deliberate "red" run once.

---

### P4 — CI failed in `uv sync --locked`: "No interpreter found for Python 3.12"

**Symptom (PR check failed after ~7 s):**

```
Run uv sync --locked
error: No interpreter found for Python 3.12 in search path
hint: A managed Python download is available for Python 3.12, but the Python preference is set to 'only system'
```

**Cause:** the Windows workaround from P1 had been added to **`pyproject.toml`** (`[tool.uv] python-preference = "only-system"`). `pyproject.toml` is shared project config, so the setting also applied on the **Linux GitHub runner**, which has no system Python 3.12, and uv wasn't allowed to download one.

**Fix:**
1. Put the setting in your **user** config `%APPDATA%\uv\uv.toml` (P1).
2. Remove it from the repo, and verify nothing is left **in the committed state**:
   ```powershell
   git grep -n -i "only-system\|python-preference\|PYTHON_PREFERENCE" HEAD
   ```
   (Mentions in CLAUDE.md prose are fine; config lines aren't.)
3. Commit, push, wait ~10 s, `gh pr checks --watch` → ✓.

**Lesson:** machine-specific settings (interpreter preference, CLI profiles, local paths) belong in **user config**, not in the repo. It's the same lesson as `profile: free` in Lab 4: the repo describes the project, your machine's quirks stay on your machine.

---

### P5 — "I fixed it, but CI shows the same error"

Two separate traps, both hit while fixing P4.

**(a) Partial commit.** The first "fix" commit contained only the CLAUDE.md change. The line in `pyproject.toml` was still there. `git status` said *clean*, because the edit had never been saved, or was saved after the commit.

**Check what's really committed:**

```powershell
git grep -n "<pattern>" HEAD          # searches the committed state
git show --stat HEAD                  # which files did the last commit change?
```

**(b) Stale check output.** `gh pr checks --watch` right after `git push` can show the **previous** run, before GitHub starts the new one. Check which commit a run belongs to:

```powershell
gh run list --workflow ci.yml --limit 3 --json headSha,status,conclusion,createdAt --jq '.[] | "\(.headSha[0:7])  \(.status)  \(.conclusion)  \(.createdAt)"'
```

**Lesson:** after `git push`, wait ~10 s, and confirm the run's commit matches `git log --oneline -1`.

---

### Other problems you may hit

| Problem | Cause | Fix |
| --- | --- | --- |
| `verify_contracts`: type mismatch, e.g. `TIMESTAMP_NTZ` vs `TIMESTAMP` | The contract type spelling differs from what `information_schema.columns.data_type` reports | Query `information_schema.columns` and copy the exact type names into the contract |
| `GRANT ... TO energy-consumers` fails: principal not found | Group missing, misspelt, or not in backticks (the hyphen needs quoting) | Check **Settings → Groups**. The generated SQL must use `` `energy-consumers` `` |
| `SET TAGS` fails in prod | The identity running the job isn't the table owner, or tags aren't supported for that object | Prod tables are owned by `sp-energy-cicd`; the job runs as the owner. Check `DESCRIBE TABLE EXTENDED`. |
| Comments flip back and forth between runs | The pipeline and `apply_contracts` set **different** comments | Keep descriptions identical in `transforms.py` and the contract (the test enforces this) |
| Quality rule fails right after adding it | Existing data already violates it | Run `verify_contracts` in dev first. Decide: fix the data, relax `max_violation_pct`, or fix the rule. |
| Freshness fails every Monday morning | The SLA is too tight for real publication delays | Choose `freshness_hours` from observed delays (we use 72 h for SMARD) |
| Daily `hours_count` rule fails on DST days | 23 or 25 hours on clock-change days | The rule allows 23–25, and the newest 2 days are excluded |
| `ModuleNotFoundError: pydantic` on Databricks | Dependency not available in the job environment | Declare `pydantic` and `pyyaml` in the job's serverless environment dependencies |
| `uv sync --locked`: "lockfile needs to be updated" | New dependencies, `uv.lock` not committed | `uv lock`, commit `uv.lock` |

---

## 8. What you learned

- **Why data contracts exist:** implicit expectations between producers and consumers break silently
- **What a contract contains:** identity, version, owner, description, grain, schema, primary key, quality rules, SLA, tags, consumers
- **Data product** = table + contract + owner + docs + governance
- **Semantic versioning** for contracts: MAJOR for breaking changes, MINOR for additions, PATCH for fixes
- **Two enforcement moments:** an offline shift-left guard in CI, and a runtime check against the real table, data and SLA
- **Governance as code:** comments, tags and grants generated from the contract, so documentation can't drift
- **Single source of truth** design, and why a test must never compare a file with itself
- **Test the test:** prove that every guard can fail
- **Commit before experiments.** `git checkout --` and `git stash` behave differently.
- **Machine-specific settings** belong in user config, not in the repo
- Working with **Windows Smart App Control** as a developer

**Interview-ready summary:** *"I turned the gold tables into data products with ODCS-style data contracts: schema, owner, grain, quality rules, a freshness SLA, tags and consumer access, all versioned with semver. CI runs an offline guard that blocks merges when code and contract diverge; I proved it by renaming a column on purpose. At runtime, a Lakeflow Job task applies comments, tags and grants from the contract and verifies schema, quality and freshness against Unity Catalog in every environment, failing loudly with an alert."*

---

## 9. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-22 |
| OS | Windows 11 with Smart App Control **on** (local), ubuntu-latest (GitHub runners) |
| Local Python | 3.12.10 from python.org (signed), via `%APPDATA%\uv\uv.toml` |
| Databricks | Free Edition (serverless), one workspace for dev/staging/prod |
| Databricks CLI | `<output of databricks -v>` |
| uv | `<output of uv --version>` |
| Tests | 181 passing (67 contract tests) |
| Release | v0.2.0 |

**Next:** Lab 7 — Day-ahead load and price forecast: features from gold, a baseline vs. a gradient-boosting model, MLflow tracking, and a forecast-vs-actual table, itself published with a data contract.
