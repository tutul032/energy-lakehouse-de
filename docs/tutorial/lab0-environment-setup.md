# Lab 0 — Environment Setup for a Databricks Portfolio Project (Windows 11)

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.

## 1. Goal

By the end of this lab you will have:

- A free **Databricks Free Edition** workspace
- All local tools installed on Windows 11: Git, GitHub CLI, uv (Python manager), Python 3.12, Databricks CLI, VS Code
- The Databricks CLI authenticated to your workspace
- A GitHub repository with a working **Databricks Asset Bundle** (dev/staging/prod targets), tests, linting and a **GitHub Actions CI pipeline**
- A first job deployed and run on Databricks serverless, writing a smoke-test Delta table to Unity Catalog

**Time:** about 60–90 minutes.

## 2. Prerequisites

- Windows 11 with `winget` (preinstalled on current Windows 11)
- A GitHub account
- An email address for Databricks sign-up
- Optional: a Claude subscription that includes Claude Code (used in Step 9, Option A). Without it, use Option B.

No cloud account and no credit card are needed for this lab.

---

## 3. Steps

### Step 01 — Create a Databricks Free Edition workspace

**Action:** Search the web for "Databricks Free Edition" and sign up (Google, GitHub or email).

**Expected result:** A workspace opens at a URL like:

```
https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
```

Copy this URL **without** anything after `.com` (no `/?o=...`). You need it in Step 05.

**Check:** Open **SQL Editor** in the left sidebar and run:

```sql
SELECT current_user(), current_catalog(), current_schema();
```

You should see your email, catalog `workspace` and schema `default`.

📸 `lab0-step01-workspace-home.png`, `lab0-step01-sql-check.png`

---

### Step 02 — Install the local tools

Open **Windows Terminal** (PowerShell) and run:

```powershell
winget install --id Git.Git -e
winget install --id GitHub.cli -e
winget install --id astral-sh.uv -e
winget install --id Databricks.DatabricksCLI -e
winget install --id Microsoft.VisualStudioCode -e
```

**Important:** close Windows Terminal completely and open it again. winget updates your PATH, and an already-open terminal does not see the new commands.

**Check:**

```powershell
git --version
gh --version
uv --version
databricks -v
code --version
```

**Expected result:** each command prints a version number. Write the versions down for the "Tested on" section.

📸 `lab0-step02-versions.png`

---

### Step 03 — Configure Git (one-time)

```powershell
git config --global core.autocrlf input
git config --global init.defaultBranch main
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

`core.autocrlf input` prevents Windows line endings (CRLF) from causing noisy diffs and lint failures in CI.

**Check:**

```powershell
git config --global --list
```

**Expected result:** the four settings above appear in the list.

---

### Step 04 — Install Python 3.12 with uv

Databricks serverless uses Python 3.12, so local tests should match.

```powershell
uv python install 3.12
uv python list | Select-String "3.12"
```

**Expected result:** a line like `cpython-3.12.x-windows-x86_64-none` followed by a local path (not "download available").

---

### Step 05 — Authenticate the Databricks CLI

Replace the URL with your workspace URL from Step 01:

```powershell
databricks auth login --host https://dbc-xxxxxxxx-xxxx.cloud.databricks.com --profile free
```

A browser window opens. Approve the request, then return to the terminal.

**Check:**

```powershell
databricks current-user me -p free
databricks catalogs list -p free
```

**Expected result:**
- a JSON block with your `userName` (your email)
- a catalog list that includes `workspace` and `system`

The login is saved in `C:\Users\<you>\.databrickscfg` under the profile name `free`.

📸 `lab0-step05-cli-auth.png`

---

### Step 06 — Authenticate GitHub CLI and create the repository

Log in through the browser. **Do not** choose "Paste an authentication token". You don't need a token.

```powershell
gh auth login --web --git-protocol https
```

What happens:
1. The terminal shows a one-time code like `ABCD-1234`. Copy it and press **Enter**.
2. The browser opens `https://github.com/login/device`. Paste the code → **Continue** → **Authorize**.
3. If asked "Authenticate Git with your GitHub credentials?", answer **Yes**.

**Check:**

```powershell
gh auth status
```

**Expected result:** `✓ Logged in to github.com account <your-username>`

Now create and clone the repository in a short path. Windows path-length limits can break Python tooling in deep folders.

```powershell
mkdir C:\dev -Force
cd C:\dev
gh repo create energy-lakehouse-de --public --clone --add-readme --license mit
cd energy-lakehouse-de
git log --oneline
```

**Expected result:** the folder `C:\dev\energy-lakehouse-de` exists, contains `README.md` and `LICENSE`, and `git log` shows one commit. The repo is also visible on github.com under your account.

📸 `lab0-step06-repo-created.png`

---

### Step 07 — Set up VS Code

```powershell
code .
```

In VS Code, open **Extensions** (Ctrl+Shift+X) and install:
- **Python** (Microsoft)
- **Databricks** (Databricks)
- **Claude Code** (Anthropic). Optional, needed for Step 09 Option A. Sign in after installing.

Open the integrated terminal with **Ctrl+`** (backtick, left of the 1 key). Run all following commands there.

📸 `lab0-step07-vscode-extensions.png`

---

### Step 08 — Check that you can create service principals

You won't create one yet. This check confirms that CI/CD deployment with GitHub OIDC will be possible later in the series.

**Click path:** profile icon (top right) → **Settings** → **Identity and access** → **Service principals** → **Manage** → **Add service principal**.

**Expected result:** a dialog "Add service principal to workspace" opens with the option "Add new service principal to account and workspace". Close the dialog with **X**.

📸 `lab0-step08-service-principal-dialog.png`

---

### Step 09 — Scaffold the project

You have two options.

- **Option A (with Claude Code):** generate the files with the prompt below. AI output is not identical every time. File contents may differ slightly from mine, but the structure and checkpoints will be the same.
- **Option B (exact copy, no AI needed):** copy the files from my repository at the tag `lab0-complete`:
  `https://github.com/<my-username>/energy-lakehouse-de/tree/lab0-complete`

#### Option A — Claude Code prompt

Open the Claude Code panel in VS Code and paste:

```text
Scaffold this repo for a Databricks portfolio project. Do not run deploy commands; I will run checkpoints myself.

Context:
- Project: "energy-lakehouse-de" — German energy data engineering on Databricks.
  Data later: SMARD.de (load, generation, prices) + Open-Meteo weather.
  Later components: Auto Loader, Lakeflow Spark Declarative Pipelines (bronze/silver/gold + expectations), Lakeflow Jobs, AI/BI dashboard + Genie.
- Environment: Databricks Free Edition (serverless only, no cluster definitions anywhere), single workspace.
  CLI profile name: "free". Unity Catalog catalog: "workspace".
- Dev machine: Windows 11, PowerShell, uv, Python 3.12.

Create:
1. CLAUDE.md — project rules for future sessions: the context above, folder layout,
   commands (uv sync, ruff, pytest, databricks bundle validate/deploy/run with -p free),
   conventions (serverless only, all tables in catalog `workspace`, schema per target,
   no hardcoded hosts/secrets, PowerShell-compatible commands, small testable functions in src/).
2. databricks.yml — bundle name energy-lakehouse-de; include resources/*.yml;
   variables `catalog` (default workspace) and `schema`;
   targets: dev (mode: development, default: true, schema energy_dev),
   staging (schema energy_staging), prod (mode: production, schema energy_prod,
   root_path under /Workspace/Shared/.bundle/${bundle.name}/${bundle.target}).
   Workspace host taken from the profile, not hardcoded.
3. resources/hello_job.yml — one serverless job "hello_job" running src/notebooks/hello.py,
   passing catalog and schema as parameters. The notebook creates the schema if missing
   and writes a tiny Delta table `_smoke_test` with the current timestamp, then prints a row count.
4. src/energy_lakehouse/__init__.py and src/energy_lakehouse/utils.py with one pure function
   `table_name(catalog, schema, table) -> str` returning a backtick-safe 3-part name.
5. tests/test_utils.py — pytest tests for table_name.
6. pyproject.toml — uv-managed, Python 3.12, dev deps: pytest, ruff; ruff config (line length 100).
7. .gitignore — Python, .venv, .databricks/, .env, .ruff_cache, __pycache__.
8. .github/workflows/ci.yml — on push/PR: checkout, setup uv, uv sync, ruff check, ruff format --check, pytest.
   No Databricks deploy yet (we add OIDC deploy later).
9. docs/ folders: docs/img/, docs/adr/ with 0001-record-architecture-decisions.md,
   docs/tutorial/lab0-setup.md and lab1-*.md placeholder following this template:
   Goal, Prerequisites, Steps (action → command → expected result → screenshot labN-stepNN-name.png),
   Checkpoint, Troubleshooting, What you learned, Tested on.

At the end, list the files created and the exact checkpoint commands for me to run.
```

If you used a different repo name or CLI profile name, change them in the prompt.

**Expected result:** the repo now contains:

```
energy-lakehouse-de/
├── .github/workflows/ci.yml
├── docs/
│   ├── adr/0001-record-architecture-decisions.md
│   ├── img/
│   └── tutorial/
├── resources/hello_job.yml
├── src/
│   ├── energy_lakehouse/
│   │   ├── __init__.py
│   │   └── utils.py
│   └── notebooks/hello.py
├── tests/test_utils.py
├── .gitignore
├── CLAUDE.md
├── databricks.yml
├── LICENSE
├── pyproject.toml
└── README.md
```

📸 `lab0-step09-scaffold-tree.png`

---

### Step 10 — Verify locally and on Databricks

Run these one by one in the VS Code terminal:

```powershell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run hello_job -t dev -p free
```

**Expected results:**

| Command | Expected output |
| --- | --- |
| `uv sync` | creates `.venv`, no errors |
| `ruff check` | `All checks passed!` |
| `ruff format --check` | `N files already formatted` |
| `pytest` | all tests passed |
| `bundle validate` | `Validation OK!` |
| `bundle deploy` | ends with `Deployment complete!` |
| `bundle run` | prints a run URL and ends with `TERMINATED SUCCESS` |

📸 `lab0-step10-bundle-run.png`

---

### Step 11 — Commit, push and check CI

```powershell
git add .
git commit -m "chore: scaffold bundle, CI, docs structure"
git push
```

Open your repository on github.com → **Actions** tab.

**Expected result:** the CI workflow run is green ✅.

Optional, so others can copy your exact state:

```powershell
git tag lab0-complete
git push --tags
```

📸 `lab0-step11-ci-green.png`

---

## 4. Checkpoint

All of the following must be true:

1. In the Databricks workspace: **Catalog** → `workspace` → `energy_dev` → table `_smoke_test` exists. In SQL Editor:
   ```sql
   SELECT * FROM workspace.energy_dev._smoke_test;
   ```
   returns one row with a timestamp.
2. **Jobs & Pipelines** shows a job named like `[dev <your-name>] hello_job` with a successful run.
3. GitHub **Actions** shows a green CI run on `main`.

---

## 5. Troubleshooting

| Problem | Cause | Fix |
| --- | --- | --- |
| `databricks : The term 'databricks' is not recognized` | Terminal opened before install; PATH not refreshed | Close all terminals and VS Code, reopen. If it still fails, sign out of Windows and back in. Check with `winget list Databricks`. |
| `gh auth login` asks for an authentication token | "Paste an authentication token" was selected | Press Ctrl+C, run `gh auth login --web --git-protocol https` |
| Browser doesn't open during `gh auth login` | Default browser not set | Open `https://github.com/login/device` manually and enter the code |
| `databricks auth login` fails with host error | URL copied with `/?o=...` or a trailing path | Use only `https://dbc-xxxxxxxx-xxxx.cloud.databricks.com` |
| CI fails on `ruff format --check` | Files not formatted | Run `uv run ruff format .`, commit, push |
| Many changed lines with no visible change | CRLF line endings | Set `git config --global core.autocrlf input` (Step 03) |
| Schema or job name has a `dev_<name>` / `[dev <name>]` prefix | `mode: development` prefixes resources so each developer gets their own copy | Expected behavior, no fix needed |

---

## 6. What you learned

- Setting up a reproducible Python toolchain on Windows with **winget** and **uv**
- Authenticating the **Databricks CLI** with OAuth (browser login, named profile)
- Authenticating **GitHub CLI** without tokens
- The structure of a **Databricks Asset Bundle**: `databricks.yml`, targets (dev/staging/prod), variables, resources
- Deploying and running a **serverless job** from your laptop
- Where data lands in **Unity Catalog**: `catalog.schema.table`
- A minimal **CI pipeline** (lint + test) with GitHub Actions
- Using **CLAUDE.md** to give an AI coding assistant persistent project rules

---

## 7. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-16 |
| OS | Windows 11 |
| Databricks | Free Edition (serverless) |
| Databricks CLI | `<output of databricks -v>` |
| uv | `<output of uv --version>` |
| Python | 3.12.x |
| Git | `<output of git --version>` |
| GitHub CLI | `<output of gh --version>` |
| VS Code | `<output of code --version, first line>` |

**Next:** Lab 1 — Ingesting SMARD energy data with Auto Loader.
