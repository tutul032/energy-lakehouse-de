# Part II — Working with Claude

## Chapter 4 — Understanding data before you build

Most of the expensive problems in this project were **data problems**, not code problems: the 596-GWh "complete" day, the DST 25-hour day, the autumn shift that looked like 28 critical anomalies. They're found fastest **before** building, by looking at the data the right way.

### 4.1 The ten questions to answer about any dataset

| # | Question | Example answer (energy project) |
| --- | --- | --- |
| 1 | What does **one row** mean (grain)? | One series × one hour |
| 2 | What's the **key**? Is it unique? | `(series, timestamp)`; unique after dedupe |
| 3 | Which **time column**, in which **time zone**? | Epoch milliseconds, UTC |
| 4 | What **units**? | MWh per hour; EUR/MWh |
| 5 | How **fresh**, and how often updated? | Daily; newest hours published with a delay |
| 6 | Can old data **change** (late data, corrections)? | Yes, the newest weekly chunk fills up later |
| 7 | Where are **nulls**, and what do they mean? | Not-yet-published hours |
| 8 | What **ranges** are plausible? | Load 40,000–75,000 MWh/h; price −500…+1,000 |
| 9 | **Size**, now and per year? | ~9,000 hours/year × 8 series |
| 10 | **Licence** and **privacy**? | CC BY 4.0; no personal data |

If you can't answer one of these, find out before designing silver and gold.

### 4.2 Five ways to look at data, and when to use each

| Method | Shows | Misses | Use for |
| --- | --- | --- | --- |
| **First 200 rows** | Format, columns, start of history | Recent changes, the end of the file | First look |
| **Last 200 rows** | Newest data, **incomplete** records, nulls at the edge | Old quirks | Freshness, late data |
| **Random sample** (e.g. 500 rows) | Typical values across the whole range | Rare events | Distributions, typical values |
| **Stratified sample** (e.g. 50 per category / month) | Every group represented | Volume | Seasonality, categories |
| **Full profile** (computed, not read) | Counts, nulls, min/max, distinct, duplicates for *all* rows | Meaning | Facts about the whole dataset |

**Rule:** read **samples** yourself; let **code** compute the **profile** over all data. Never judge a dataset from the first rows alone: the first rows of SMARD files looked perfect, and the problems were at the **end** (nulls) and on **specific days** (DST).

### 4.3 Getting samples, with ready commands

**Local CSV or Parquet (DuckDB is fast and needs no setup):**

```powershell
uv add --dev duckdb
uv run python -c "import duckdb; print(duckdb.sql(\"SELECT * FROM 'data/sample.csv' LIMIT 200\").df())"
```

Or ask Claude Code to write a reusable script (prompt in 4.5).

```sql
-- in DuckDB: first, last, random, stratified
SELECT * FROM 'data/x.csv' LIMIT 200;
SELECT * FROM 'data/x.csv' ORDER BY event_time DESC LIMIT 200;
SELECT * FROM 'data/x.csv' USING SAMPLE 500 ROWS;
SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY category ORDER BY random()) AS rn FROM 'data/x.csv') WHERE rn <= 50;
SUMMARIZE SELECT * FROM 'data/x.csv';      -- min/max/nulls/distinct for every column
```

**On Databricks (data already in a table):**

```sql
SELECT * FROM my_table LIMIT 200;
SELECT * FROM my_table ORDER BY event_time DESC LIMIT 200;
SELECT * FROM my_table TABLESAMPLE (500 ROWS);
DESCRIBE TABLE EXTENDED my_table;
```

In a notebook, `dbutils.data.summarize(spark.table("my_table"))` gives a visual profile.

**An API:** fetch **one** response, save it unchanged, and inspect its structure first (as in Lab 1 for SMARD and Lab 3 for Open-Meteo).

### 4.4 What to give Claude, and what not to

| Give | Don't give |
| --- | --- |
| The **schema** (column names + types) | Personal data (names, emails, IBANs, health data) |
| **Small samples**: first 20, last 20, 20 random rows | Whole large files pasted into the chat |
| The **profile output** (counts, nulls, ranges) | Secrets, tokens, connection strings |
| The source **documentation** or a link | Data whose licence forbids sharing |

For anything large or sensitive, let **Claude Code run the profiling locally or on Databricks** and look at the **summary**, not the raw rows. If you must show examples of personal data, **mask** them first (Claude Code can write the masking script).

### 4.5 Prompts for understanding data

**Profile a file (Claude Code, local):**

```text
Profile data/transactions.csv without loading it fully into memory (use duckdb).
Write scripts/profile_data.py that prints and saves docs/data-profile.md with:
row count; per column: type, null %, distinct count, min/max (or top-5 values for text);
duplicates on the candidate key (step, nameOrig); first 20 and last 20 rows ordered by time;
20 random rows; per-day row counts (to spot gaps); anything surprising (negative amounts, future dates, constant columns).
Don't change the data. Then list 5 questions I should answer before designing silver and gold.
```

**Interpret a sample (Claude chat, with masked data):**

```text
Here are the schema, the profile and 20 first / 20 last / 20 random rows of a dataset (masked).
1) What does one row most likely represent (grain)? 2) Candidate primary key? 3) Time zone and time column?
4) Which columns look derived or redundant? 5) Which values look suspicious? 6) What would a bronze/silver/gold
design look like, and which data-quality rules would you start with?
```

**Check plausibility against domain knowledge:**

```text
For German electricity data: what are plausible ranges for hourly load (MWh), day-ahead price (EUR/MWh),
daily load (GWh), and solar output at night? Give sources I can check.
```

(Always verify domain claims yourself. Here, the realistic range "1,100–1,500 GWh per day" is what exposed the 596-GWh bug.)

### 4.6 Worked example: PaySim (payment transactions)

1. **Profile:** ~6.3 M rows; columns `step, type, amount, nameOrig, oldbalanceOrg, newbalanceOrig, nameDest, …, isFraud, isFlaggedFraud`.
2. **Grain:** one transaction. **Time:** `step` = hour of simulation (no calendar time zone).
3. **Surprises a profile would show:** fraud only in certain `type`s; `isFraud` ≈ 0.1 % (heavy imbalance); balances that don't add up for some rows (a synthetic-data artefact, which is itself a quality rule candidate).
4. **First 200 vs. last 200 rows:** the time range of `step`, and whether fraud clusters at the end.
5. **Decisions it drives:** silver key `(step, nameOrig, nameDest, amount)`; contract rule "amount ≥ 0"; ML metric **precision/recall**, not accuracy.

### Exam questions — Chapter 4

1. **Why are the first 200 rows not enough?**
   *Answer:* problems often sit at the end (incomplete, late data) or on rare days (DST, holidays). The start of a file is usually the cleanest part.
2. **Why should code compute the profile rather than you reading rows?**
   *Answer:* only a computation over all rows gives true null rates, duplicates and ranges.
3. **What would you never paste into a chat?**
   *Answer:* personal data, secrets, and data you're not licensed to share.
4. **Which of the ten questions found the 596-GWh bug?**
   *Answer:* #5/#6/#7: freshness, late data, and the meaning of nulls (unpublished hours), checked with #8 (plausible ranges).

**Exercise 4.1:** pick any public CSV. Produce `docs/data-profile.md` with the prompt in 4.5, then answer the ten questions yourself.

---

## Chapter 5 — Writing prompts for Claude Code

### 5.1 The five-part prompt pattern

Every working prompt in this project had the same five parts:

```text
1. CONTEXT       Read CLAUDE.md first. What exists, what the input looks like (paste a sample or API shape).
2. CONSTRAINTS   Platform limits, "do not deploy", naming, time zones, "change nothing else".
3. DELIVERABLES  Numbered list of files, each with its exact responsibility.
4. QUALITY       Pure functions + offline tests, null-safe rules, idempotency, fail loudly, guard tests.
5. HANDBACK      "List changed files and checkpoint commands."
```

**Short example (adding a source):**

```text
Read CLAUDE.md first. Add an extractor for the Open-Meteo forecast API (sample response below). Do not deploy.
Constraints: no outbound internet on Databricks, so extraction runs locally/GitHub Actions; files are never overwritten.
Deliverables: 1) src/energy_lakehouse/weather.py (build_url, fetch, output_path, extract) 2) tests/test_weather.py
(offline, fixture JSON) 3) CLAUDE.md update.
Quality: idempotent, 3 retries with backoff, summary {written, skipped, failed}.
Handback: list changed files and the commands to verify.
<paste 20 lines of a sample response>
```

### 5.2 Three prompt types you need

**A. Build prompt** (new feature): the five-part pattern above.

**B. Fix prompt** (after a failure):

```text
<task> fails with: <exact error line(s)>. Cause (confirmed): <one sentence>.
Fix: <what should change>. Add a test that fails before the fix and passes after.
Change nothing else.
```

*"Change nothing else"* keeps fixes small. *"Cause (confirmed)"* means you've investigated first (Chapter 7), and you're not asking Claude to guess.

**C. Investigation prompt** (you don't know the cause yet):

```text
<task> fails with <error>. Don't change code yet. Investigate: read the relevant files and logs, list 3 possible
causes ranked by likelihood with the evidence for each, and the command or query that would confirm each one.
```

### 5.3 Prompting rules learned the hard way

| Rule | Because of |
| --- | --- |
| Give **exact** names, types and units | The assistant invented `readme-*.png` filenames (Lab 10) |
| Say what must **not** happen ("no `%pip`", "no machine-specific settings in pyproject") | Lab 7, Lab 6 |
| Ask for **tests that can fail**, and prove one fails | Lab 6 ("test the test") |
| For data rules: **null-safe**, one thing per rule | Lab 2 |
| For times: always say **UTC vs. Europe/Berlin** | Labs 7, 8, 9 |
| For generated SQL: "**unqualified table names**" | Lab 9 |
| Never put secrets in prompts or files | Lab 4, Lab 5 |
| Small steps: one prompt per stage, then verify | All labs |

### 5.4 Keeping Claude Code consistent: `CLAUDE.md`

`CLAUDE.md` in the repo root is read by Claude Code at the start of each session. It holds the rules you'd otherwise repeat. Keep it current:

```markdown
# CLAUDE.md
## Project
energy-lakehouse-de: German power market + weather lakehouse on Databricks Free Edition (serverless).
## Golden rules
- Never commit on master. Never deploy staging/prod from the laptop.
- Job dependencies only via the wheel / job environments. No %pip, no restartPython in notebooks.
- Times: *_utc columns are UTC; local dates are Europe/Berlin (DST: 23/24/25 hours).
- Dashboard dataset SQL uses unqualified table names.
- Every bug fix comes with a test.
## Commands
uv run ruff check . ; uv run pytest -q ; databricks bundle deploy -t dev -p free ; …
## Layout
src/energy_lakehouse (package), src/pipelines, src/notebooks, resources/, contracts/, tests/
```

### Exam questions — Chapter 5

1. **Name the five parts of a build prompt.**
   *Answer:* context, constraints, deliverables, quality, handback.
2. **Why add "change nothing else" to fix prompts?**
   *Answer:* it keeps the change small and reviewable, and avoids side effects in unrelated code.
3. **When do you use an investigation prompt instead of a fix prompt?**
   *Answer:* when the cause isn't confirmed. Guessed fixes caused two wrong turns in Lab 7.
4. **What belongs in `CLAUDE.md`?**
   *Answer:* project context, golden rules, commands, layout: anything you'd otherwise repeat in every prompt.

**Exercise 5.1:** rewrite this vague prompt with the five-part pattern: *"add weather data and make it work"*.

---

## Chapter 6 — Maximum automation with Claude Code

You did most of this project by copying commands into PowerShell. That's the right way to *learn*. Once you know what each command does, let Claude Code run the loop, while you stay the **decision-maker and reviewer**.

> Claude Code's features evolve quickly. The concepts below are stable; check the current Claude Code documentation for exact file names and syntax.

### 6.1 What can be automated, and what shouldn't be

| Automate (Claude Code / CI) | Keep human |
| --- | --- |
| Running lint, tests, validate, deploy **dev**, run jobs in dev | Approving **prod** releases |
| Reading logs and finding the first failed task | Deciding whether a finding is real or an error |
| Plausibility queries and a written test report | Accepting a model / changing thresholds |
| Writing tests and docs | Anything with secrets |
| Opening PRs, watching CI | Merging changes you haven't read |

### 6.2 Building blocks

| Feature | What it does | How you use it here |
| --- | --- | --- |
| **`CLAUDE.md`** | Project memory loaded every session | Golden rules + commands (5.4) |
| **Plan mode** | Claude proposes a plan before changing files | Every new stage: approve the plan first |
| **Custom slash commands / skills** (`.claude/commands/` or `.claude/skills/`) | Reusable instructions you trigger with `/name` | `/ship`, `/verify-dev`, `/diagnose-run` (below) |
| **Subagents** (`.claude/agents/`) | Specialised helpers with their own instructions | A "QA tester" that only verifies and reports |
| **Hooks** (`.claude/settings.json`) | Commands that run automatically, e.g. after edits | Run `ruff format` after every file edit |
| **Permissions** (allow-list) | Which commands Claude may run without asking | Allow `uv run …`, `databricks bundle validate/deploy -t dev`, `gh pr …`; **never** prod deploys |
| **Headless mode** (`claude -p "…"`) | Run a prompt non-interactively | Scripts, scheduled checks |
| **GitHub integration** (Claude Code GitHub Action) | Claude reviews PRs or answers in PR comments | An AI review step next to CI |

### 6.3 Three commands that replace most copy-pasting

**`/ship`: the DELIVER half of the lifecycle**

`.claude/commands/ship.md` (or a skill with the same content):

```markdown
Ship the current work through the lifecycle (Chapter 3). Stop at the first failure and report.
1. Refuse if the current branch is master. Show `git status --short`.
2. Run: uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
3. Commit all changes with a conventional message I confirm. Verify with `git log --oneline -1`
   and `git status --short` (must be empty). If the commit failed, stop.
4. Push and open a PR (`gh pr create --fill --base master`). Wait for `gh pr checks --watch`.
5. Report the PR URL and the CI result. Do NOT merge, do NOT release.
```

**`/verify-dev`: deploy dev, run, and test like a human QA tester**

```markdown
Verify the dev environment end to end and write a QA report. Never touch staging or prod.
1. databricks bundle validate -t dev -p free ; databricks bundle deploy -t dev -p free
2. Run daily_refresh_job in dev; wait; list every task with its result; for the first failed task fetch
   `databricks jobs get-run-output` and show the error.
3. Run `uv run python scripts/smoke_checks.py --target dev` (deterministic checks, see below).
4. Act as a sceptical human tester: for each gold table, query 10 recent rows and judge plausibility against
   CLAUDE.md's domain ranges; look for empty outputs, future timestamps, duplicated hours, NULL spikes.
5. Write docs/qa/<date>-dev.md: PASS/FAIL per check, evidence (query + result), suspicious findings,
   and what a human must decide. Do not fix anything.
```

**`/diagnose-run <run-id>`: the error-finding playbook (Chapter 7) as a command**

```markdown
Diagnose a failed Databricks job run. Don't change code.
1. databricks jobs get-run <run-id> -p free -o json → table of task_key, result_state, state_message.
2. Find the FIRST task with result FAILED (not UPSTREAM_FAILED). Fetch its error and the last trace lines.
3. Classify the error (environment/dependency, data, permissions, config/schema, code) and give 2–3 ranked causes
   with the evidence and one confirming command or query each.
```

### 6.4 Deterministic checks first, AI judgement second

An AI tester is good at *noticing* things; it's not a substitute for **repeatable** checks. Put the checks that must always hold into code (`scripts/smoke_checks.py`), run them in CI and after every deploy, and use Claude for the exploratory review on top.

**Prompt to create the smoke checks:**

```text
Create scripts/smoke_checks.py --target {dev,staging,prod}. It runs SQL via the Databricks Statement Execution API
(databricks-sdk, warehouse from the bundle variable warehouse_id) and fails with a clear list if any check fails:
- every gold table has rows; newest hour_utc of gold_energy_hourly with load is < 72 h old
- latest complete day in gold_energy_daily: total load between 900 and 1,800 GWh
- gold_forecast_hourly latest issue_date has 23–25 rows per target
- no future timestamps in silver tables (except day-ahead prices)
- no duplicated (target_hour_utc, target, issue_date)
- 0 critical rows in gold_dq_anomalies for the latest detection run
Print a PASS/FAIL table. Add it as a step after deploy in deploy-staging.yml, and as a manual step in the runbook for prod.
```

This is the "human quality tester" made reliable: a machine runs the same plausibility checks every time, and Claude writes the report and points out what needs a human decision.

### 6.5 A fully automated loop (what a session can look like)

```
You:     "Lab 11: containerise the extractor. Plan first."           ← plan mode, you approve
Claude:  implements + tests                                           ← build
You:     /verify-dev                                                  ← Claude deploys dev, runs, writes QA report
You:     read docs/qa/…-dev.md, decide                                ← human judgement
You:     /ship                                                        ← Claude commits (verified), opens PR, watches CI
You:     review the PR diff, merge, release, approve prod             ← human gates
You:     claude -p "/verify-prod-readonly"  (smoke checks only)       ← automated prod check
```

You type four short commands instead of forty, but you still **decide** at every gate.

### 6.6 Safety rules for automation

- **Allow-list** only safe commands. Prod deploys and secret commands always need you.
- Claude must **stop at the first failure** and report (written into every command).
- Never let automation run `git reset --hard`, `--force` deploys, or `DROP` on prod without explicit confirmation.
- Review every PR diff yourself before merging.

### Exam questions — Chapter 6

1. **Why keep deterministic smoke checks in code if Claude can test?**
   *Answer:* code checks are repeatable, run in CI and give the same answer every time. AI review adds exploration on top.
2. **Which actions stay human?**
   *Answer:* prod approval, merging, interpreting findings, threshold/model decisions, secrets.
3. **What does `/ship` stop on?**
   *Answer:* master branch, failing checks, a failed commit (unverified), failing CI.
4. **What's the role of `CLAUDE.md` in automation?**
   *Answer:* every automated session starts with the same rules, so behaviour is consistent.

**Exercise 6.1:** create `/verify-dev` in your repo, run it, and compare its report with your own manual checks.

---

## Chapter 7 — Fixed commands and the error-finding playbook

### 7.1 Commands to know by heart

**Git safety**

```powershell
git status --short | Measure-Object                # 0 = clean
git branch --show-current
git log --oneline -1                               # verify a commit happened
git --no-pager log --oneline origin/master..HEAD   # what's not on master yet
git switch -c <type>/<name>                        # new branch, takes changes along
git stash ; git stash pop                          # park/restore changes safely
```

**Local quality**

```powershell
uv sync
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
```

**Databricks (dev)**

```powershell
databricks current-user me -p free
databricks auth login --host <url> --profile free          # when the token expired
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run <resource> -t dev -p free [--params k=v] [--full-refresh-all]
databricks bundle open <resource> -t dev -p free
databricks jobs list -p free | Select-String "<name>"
```

**GitHub**

```powershell
gh pr create --fill --base master ; gh pr checks --watch ; gh pr merge --squash --delete-branch
gh pr view <n> --json state,mergedAt
gh run list --workflow <file>.yml --limit 3
gh run view <id> --log-failed
gh workflow run <file>.yml [--ref master] [-f key=value]
gh release create vX.Y.Z --target master --generate-notes --title "…"
gh secret set NAME ; gh variable set NAME --body "…"
```

### 7.2 The error-finding playbook

```
1. READ the exact error      →  never guess from "it failed"
2. FIND the first failure    →  first FAILED task, not UPSTREAM_FAILED
3. CLASSIFY                  →  environment · data · permission · config/schema · code · process
4. COMPARE                   →  a working artifact vs. the failing one (notebook, environment, config)
5. CONFIRM the cause         →  one query or command that proves it
6. FIX the cause only        →  small, "change nothing else"
7. GUARD                     →  a test/hook/check so it can't happen again
8. DOCUMENT                  →  tutorial troubleshooting entry, runbook if operational
```

**Finding the first failed task of a job run:**

```powershell
$run = databricks jobs get-run <run-id> -p free -o json | ConvertFrom-Json
$run.tasks | Select-Object task_key, @{n="result";e={$_.state.result_state}}, @{n="message";e={$_.state.state_message}} | Format-Table -AutoSize -Wrap
$taskRunId = ($run.tasks | Where-Object { $_.state.result_state -eq "FAILED" } | Select-Object -First 1).run_id
$out = databricks jobs get-run-output $taskRunId -p free -o json | ConvertFrom-Json
$out.error ; $out.error_trace | Select-Object -Last 1
```

**Reading a failed GitHub Actions run:**

```powershell
$id = gh run list --workflow <file>.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $id ; gh run view $id --log-failed
```

### 7.3 Error classes seen in this project

| Class | Typical message | Real example | Typical fix |
| --- | --- | --- | --- |
| **Environment / dependency** | `ModuleNotFoundError`, `spark should be initialized…`, `PCG64 is not a known BitGenerator` | Labs 7, 10 | One dependency mechanism; identical train/score environments |
| **Data** | Implausible numbers, empty results | 596 GWh (Lab 9), 28 critical anomalies (Lab 8) | Better definitions (completeness), severity design |
| **Permissions / identity** | `PERMISSION_DENIED`, `invalid_client`, `does not exist` for a principal | Labs 4, 5 | Grants, correct Application ID, host instead of profile |
| **Config / schema** | `TABLE_OR_VIEW_NOT_FOUND`, `DELTA_FEATURES…`, `ASSIGNMENT_ARITY_MISMATCH` | Labs 2, 6, 9 | Schema-qualified config, table features, the right rule type |
| **Code** | Exceptions in your functions | Various | Unit test + fix |
| **Process** | Empty PR, wrong branch, release before merge, lost changes | Labs 5, 8, 10 | Verify commits, pre-commit hook, release checklist |
| **Guards working correctly** | `LeakageError`, quality-check failures | Labs 7, 8 | Understand, then decide; don't weaken guards |

### 7.4 Plausibility SQL templates

```sql
-- counts per layer
SELECT 'bronze' l, count(*) FROM <bronze> UNION ALL SELECT 'silver', count(*) FROM <silver> UNION ALL SELECT 'gold', count(*) FROM <gold>;
-- freshness
SELECT max(<time_col>) FROM <table> WHERE <value_col> IS NOT NULL;
-- duplicates on the key
SELECT <key>, count(*) FROM <table> GROUP BY <key> HAVING count(*) > 1 LIMIT 10;
-- completeness per day (expected vs present)
SELECT to_date(<local_time>) d, count(*) rows, count(<value>) with_value FROM <table> GROUP BY 1 ORDER BY 1 DESC LIMIT 14;
-- ranges
SELECT min(<v>), percentile(<v>, 0.5), max(<v>) FROM <table>;
-- aggregate = sum of parts
SELECT d.day, d.total, sum(h.value) FROM <daily> d JOIN <hourly> h ON to_date(h.local_time) = d.day GROUP BY 1, 2;
```

### Exam questions — Chapter 7

1. **What's the first thing to do when a job fails?**
   *Answer:* find the first FAILED task and read its exact error.
2. **Why compare a working and a failing artifact?**
   *Answer:* it isolates the difference. In Lab 7 that found `%pip` + `restartPython` after two wrong guesses.
3. **The leakage guard blocked a run. Is that a bug?**
   *Answer:* not necessarily. Read the message: it was a correct block on the DST night (issue window), documented instead of weakened.
4. **Which command proves a commit really happened?**
   *Answer:* `git log --oneline -1`, plus `git status --short` showing 0 changes.

**Exercise 7.1:** take any failed run from your history and walk through the 8-step playbook in writing.
