# Lab 12 — Terraform: Adopting a Hand-Built Unity Catalog Platform as Code

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 11 — Docker. Next: Lab 12b — Terraform in CI (remote state, plan on PRs, drift checks), then the AWS chapter.

## 1. Goal

Since Lab 4, part of the platform was created **by hand**: schemas, the landing volume, the consumer group, the service principals and their grants. Nothing in Git describes them, nobody notices if someone changes them, and rebuilding them would mean following old tutorials.

In this lab you **adopt** that existing setup with Terraform, the way platform teams do it in real companies:

| PR | Content | Result |
| --- | --- | --- |
| **A, adopt** | Inventory → Terraform code → **import blocks** → a plan that only imports → **zero diff** → a drift test | 17 existing objects under code control, nothing changed in Databricks |
| **B, change through code** | Remove an unused grant **from the code**, plan, review, apply; document and test the tool boundaries; stop the AI assistant from applying on its own | The first platform change made through Terraform, plus guards |

**My results:** `Plan: 17 to import, 0 to add, 0 to change, 0 to destroy`; then **"No changes"**; a manual `REVOKE` detected as drift and restored; the unused grant removed; the daily refresh still green; tag `lab12-complete`.

**Time:** two evenings.

---

## 2. Concepts in plain words

### 2.1 Terraform in one paragraph

You **describe** the desired state in `.tf` files. `terraform plan` compares that description with **reality** and lists what it would do (`+ create`, `~ update`, `- destroy`, `import`). `terraform apply` does exactly that. Terraform remembers what it manages in a **state** file.

### 2.2 Adopt, don't recreate

The objects already exist and contain your data. Recreating them would be dangerous. Instead:

```
write code that describes what exists  →  import blocks link code to the real objects  →  plan shows ONLY imports  →  apply  →  plan says "No changes"
```

**Rule:** first codify what exists, **unchanged**, even things you'd like to clean up. Change it in a **separate**, reviewed step. Never mix "adopt" and "change".

### 2.3 Who owns what: three tools, no overlap

| Tool | Manages | Why there |
| --- | --- | --- |
| **Terraform** | Schemas, the volume, the group, service principals, **catalog/schema/volume grants for platform identities** | Platform: rarely changes, needs review |
| **Contracts job** (`apply_contracts`, Lab 6) | `USE SCHEMA` + `SELECT` for **consumer groups**, after every refresh | Data products: generated from the contracts |
| **Bundle** (Labs 2–11) | Jobs, pipelines, dashboards and their permissions | Application code |

If two tools manage the same grant, they undo each other's changes forever. This split is **ADR-0010**, and tests enforce it.

### 2.4 Additive vs. authoritative grants

| Resource | Behaviour | Risk |
| --- | --- | --- |
| `databricks_grants` (plural) | **Authoritative**: the listed grants are the *only* grants on the object | Removes everything it doesn't list, e.g. the `_workspace_users` catalog grant or the contracts job's grants |
| `databricks_grant` (singular) | **Additive**: manages one principal's grants on one object | Leaves everything else alone |

**We use only the singular form.** It's one of the most common Terraform-on-Databricks mistakes.

### 2.5 Removing vs. forgetting

| What you do | Terraform's plan | In Databricks |
| --- | --- | --- |
| Delete the resource from the **code** | `- destroy` | The grant is **revoked** |
| `terraform state rm` / a `removed {}` block with `destroy = false` | "forget" | The grant **stays**, now unmanaged |

Cleanup means the first. The second is only for handing a resource over to another tool.

---

## 3. Prerequisites

- Labs 0–11 completed; the **dev container** from Lab 11 works
- Terraform runs **inside the dev container** (Linux), because on Windows, Smart App Control can block the provider binaries Terraform downloads
- Golden rules, plus three new ones:
  - **Read every plan line by line before `apply`.** Apply only a **saved** plan (`-out=…`).
  - **Never answer a Terraform prompt you don't understand.** Use `-input=false`, so it fails with a clear message instead.
  - **Check the prompt** before each command: `PS C:\…>` is Windows, `vscode ➜ /workspaces/…/terraform $` is the container's Terraform folder.

---

## 4. Steps — PR A: adopt

### Step 01 — Inventory (understand before codifying)

In the Databricks SQL editor:

```sql
-- owners
SELECT 'schema' AS kind, schema_name AS object, schema_owner AS owner
FROM workspace.information_schema.schemata WHERE schema_name LIKE 'energy%'
UNION ALL
SELECT 'volume', volume_schema || '.' || volume_name, volume_owner
FROM workspace.information_schema.volumes WHERE volume_schema LIKE 'energy%'
ORDER BY 1, 2;

-- every grant at catalog, schema and volume level
SELECT 'catalog' AS level, 'workspace' AS object, grantee, privilege_type
FROM workspace.information_schema.catalog_privileges WHERE catalog_name = 'workspace'
UNION ALL
SELECT 'schema', schema_name, grantee, privilege_type
FROM workspace.information_schema.schema_privileges WHERE schema_name LIKE 'energy%'
UNION ALL
SELECT 'volume', volume_schema || '.' || volume_name, grantee, privilege_type
FROM workspace.information_schema.volume_privileges WHERE volume_schema LIKE 'energy%'
ORDER BY 1, 2, 3, 4;
```

```powershell
databricks groups list -p free | Select-String "energy"
databricks service-principals list -p free -o json | ConvertFrom-Json | ForEach-Object { $_ } | Select-Object id, displayName, applicationId
```

**My inventory:**

| Object | Owner / grants |
| --- | --- |
| `energy_raw`, `energy_dev`, volume `landing` | owner: me |
| `energy_staging`, `energy_prod` | owner: `sp-energy-cicd` |
| catalog `workspace` | `USE_CATALOG`: both SPs, **`_workspace_users`** (a Databricks default, not ours) |
| `energy_raw` | `USE_SCHEMA`: both SPs |
| `energy_dev` | `USE_SCHEMA`: refresh SP (**probably unused**), `energy-consumers` |
| `energy_staging`, `energy_prod` | `USE_SCHEMA`: `energy-consumers`; `USE_SCHEMA` + `SELECT`: me |
| volume `landing` | READ + WRITE: refresh SP; READ: CI/CD SP |
| group / SPs | `energy-consumers` (85723784826267), `sp-energy-refresh` (74312409388142), `sp-energy-cicd` (75104777449497) |

**Tip:** the SQL editor shows only the last result of a multi-statement run ("Results 6 of 6"). Page back with ←, or use the information-schema queries above.

### Step 02 — Check the tool boundary

Who already manages the consumer grants?

```powershell
Select-String -Path src\energy_lakehouse\*.py, src\notebooks\*.py -Pattern "USE SCHEMA|USE_SCHEMA" | Select-Object -First 10
```

```
contracts.py:284   GRANT USE SCHEMA ON SCHEMA {catalog.schema} TO {group}
contracts.py:535   "apply_contracts grants the group USE SCHEMA and SELECT after every refresh"
```

So the three `energy-consumers` `USE SCHEMA` grants belong to the **contracts job**. Terraform must **not** manage them.

### Step 03 — Claude Code prompt (plan mode)

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c feat/terraform-uc
```

```text
Read CLAUDE.md first. Lab 12 PR A: adopt the existing Unity Catalog platform setup with Terraform. Do not apply anything.
Inventory (must result in a ZERO-DIFF plan after import): <paste the inventory table>.
Rules:
1. terraform/: versions.tf (Terraform and databricks provider pinned to versions that EXIST; commit
   .terraform.lock.hcl), providers.tf (host from a variable, auth from the environment / DATABRICKS_CONFIG_PROFILE,
   no token or profile hardcoded), variables.tf, schemas.tf, volumes.tf, identities.tf, grants.tf, imports.tf
   (import blocks with the provider's documented import ID formats).
2. ONLY databricks_grant (singular). Do NOT manage the _workspace_users catalog grant, the consumer-group grants
   (apply_contracts, contracts.py:284), table grants, or anything the bundle manages.
3. lifecycle prevent_destroy = true on schemas and the volume. No OAuth secrets in Terraform.
4. .gitignore: .terraform/, *.tfstate*, *.tfplan, *.tfvars (except an example). Local state for now.
5. Dev container: the Terraform feature (major tag).
6. CI job "terraform" without credentials: fmt -check, init -backend=false, validate, tflint.
7. tests/test_terraform.py: no databricks_grants (plural); prevent_destroy on schemas/volume; no secrets/tokens;
   provider pinned.
8. docs/adr/0010-terraform-scope.md (the three-tool split, additive grants, adopt first, change later).
```

**What Claude Code created:** `versions.tf`, `providers.tf`, `variables.tf`, `schemas.tf`, `volumes.tf`, `identities.tf`, `grants.tf`, `imports.tf`, `terraform.tfvars.example`, `.tflint.hcl`, `.terraform.lock.hcl`, ADR-0010 and `tests/test_terraform.py`.

### Step 04 — Log in inside the dev container

**Ctrl+Shift+P → "Dev Containers: Reopen in Container"**. In its bash terminal:

```bash
databricks auth login --host https://dbc-405898bc-c90e.cloud.databricks.com --profile free
databricks current-user me -p free | head -3
cat >> ~/.bashrc <<'EOF'
export TF_VAR_databricks_host="https://dbc-405898bc-c90e.cloud.databricks.com"
export DATABRICKS_CONFIG_PROFILE=free
EOF
source ~/.bashrc
echo "host=$TF_VAR_databricks_host profile=$DATABRICKS_CONFIG_PROFILE"
```

**Expected:** the browser login succeeds (the VS Code container window forwards the callback port), your user is shown, and both variables are printed.

**Why `TF_VAR_…`:** Terraform reads any environment variable `TF_VAR_<name>` as the value of variable `<name>`. The workspace URL isn't a secret.

### Step 05 — Init, validate, plan

```bash
cd /workspaces/energy-lakehouse-de/terraform
terraform init
terraform validate
terraform plan -input=false
```

**Expected (mine):**

```
databricks_schema.target["prod"] will be imported   (owner = 7d0f1e32-…, the CI/CD SP)
databricks_grant.landing_volume["refresh"] will be imported   (READ_VOLUME, WRITE_VOLUME)
…
Plan: 17 to import, 0 to add, 0 to change, 0 to destroy.
```

**17 = 4 schemas + 1 volume + 1 group + 2 service principals + 9 grants.** The three consumer grants and `_workspace_users` are correctly absent.

**Anything other than `0 to add, 0 to change, 0 to destroy` means the code doesn't match reality yet: fix the code, not the platform.**

📸 `lab12-step05-import-plan.png`

### Step 06 — Apply the imports, prove zero diff

```bash
terraform plan -input=false -out=adopt.tfplan
terraform apply adopt.tfplan
terraform plan -input=false
```

**Expected:** `Apply complete! Resources: 17 imported, 0 added, 0 changed, 0 destroyed.` Then:

```
No changes. Your infrastructure matches the configuration.
```

📸 `lab12-step06-zero-diff.png`

### Step 07 — Test the test: create drift by hand

```sql
REVOKE USE SCHEMA ON SCHEMA workspace.energy_dev FROM `e1370f9f-0819-48ba-92f0-365c18361518`;
```

```bash
terraform plan -input=false
```

**Expected (mine):**

```
# databricks_grant.dev_schema_refresh will be created
Plan: 1 to add, 0 to change, 0 to destroy.
```

Terraform noticed the manual change. Restore it from code:

```bash
terraform plan -input=false -out=restore.tfplan
terraform apply restore.tfplan
terraform plan -input=false        # No changes.
```

📸 `lab12-step07-drift.png`

### Step 08 — Commit from Windows, PR

Git identity and the `gh` login live on Windows, so commit there. Run each line on its own:

```powershell
uv run python -m pytest -q
git status --short                 # NO terraform.tfstate, *.tfplan, .terraform/
git add -A
git commit -m "feat: adopt Unity Catalog platform setup with Terraform (imports, additive grants, zero diff)"
git --no-pager log --oneline origin/master..HEAD
git push -u origin HEAD
gh pr create --fill --base master
gh pr view --json mergeable ; gh pr checks --watch     # ci, docker, devcontainer, terraform ✓
gh pr merge --squash --delete-branch
```

My commit: **18 files, 642 insertions**, no state files. No release is needed: nothing in `terraform/` is deployed by the pipelines.

---

## 5. Steps — PR B: the first change through Terraform

### Step 09 — Remove the unused grant from the code

The refresh SP only writes to the landing volume and starts the **prod** job, so it has no reason to use `energy_dev`.

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c chore/terraform-cleanup
```

```text
Read CLAUDE.md first. Lab 12 PR B. Do not apply.
1. Remove databricks_grant.dev_schema_refresh and its import block (no removed{} block, no terraform state commands).
   First search workflows/resources/notebooks for any use of energy_dev by the refresh SP and report it.
2. Consumer USE_SCHEMA grants are granted by apply_contracts (contracts.py:284): Terraform must NOT manage them.
   Document this in ADR-0010 and add a test that no databricks_grant targets "energy-consumers".
3. Keep import blocks of already-imported resources (no-ops) with a comment why.
4. Dev container: stop running `pre-commit install` in postCreateCommand (it overwrote the Windows hook).
5. Runbook: "Changing platform grants = edit terraform/, plan, review, apply; never GRANT/REVOKE by hand".
```

### Step 10 — Plan, review, apply

```bash
terraform -chdir=/workspaces/energy-lakehouse-de/terraform plan -input=false -out=cleanup.tfplan
```

**Expected:** `Plan: 0 to add, 0 to change, 1 to destroy.`, and the only destroy is `databricks_grant.dev_schema_refresh`. **If anything else is marked `- destroy`, stop.**

```bash
terraform -chdir=/workspaces/energy-lakehouse-de/terraform apply cleanup.tfplan
terraform -chdir=/workspaces/energy-lakehouse-de/terraform plan -input=false      # No changes.
```

```sql
SHOW GRANTS ON SCHEMA workspace.energy_dev;
```

**Expected (mine):** only `energy-consumers | USE SCHEMA` (from the contracts job); the refresh SP is gone.

In my run, the grant had **already been revoked before I saw a plan** (P8). The end state was correct, but this step didn't go as designed, and that led to Step 11.

### Step 11 — Stop the AI assistant from applying on its own

```text
Add to CLAUDE.md (Golden rules): "Claude never runs terraform apply, destroy, import, or any terraform state command;
it may run fmt, validate and plan only." Add deny rules in .claude/settings.json:
"Bash(terraform apply:*)", "Bash(terraform destroy:*)", "Bash(terraform state:*)", "Bash(terraform import:*)".
```

Check that the deny rules are **really committed**:

```powershell
git ls-files .claude
Select-String -Path .claude\settings.json -Pattern "terraform"
```

**Expected:** `.claude/settings.json` is tracked and contains the four deny entries.

**What really happened in my run:** PR #22 only added the **rule text** to `CLAUDE.md`; `.claude/settings.json` was **never created** (the PR's file list didn't contain it, and `Get-Content .claude\settings.json` failed). A rule in `CLAUDE.md` is a request; a deny rule in `settings.json` is **enforced**. I added the file in the follow-up docs PR, with a test that it exists and denies the four commands, and then checked the guard: asked to run `terraform apply`, Claude Code refuses.

**Lesson:** verify a guard exists and works, not just that someone said it was added. The project-level `.claude/settings.json` is committed (shared by everyone); `.claude/settings.local.json` is personal and stays out of Git.

### Step 12 — Prove nothing broke, merge, tag

```powershell
gh workflow run daily-refresh.yml
uv run pre-commit install            # restore the Windows hook
uv run python -m pytest -q
git add -A ; git commit -m "chore: remove unused refresh-SP grant on energy_dev via Terraform; consumer-grant boundary; deny unreviewed terraform applies"
git --no-pager log --oneline origin/master..HEAD
git push -u origin HEAD ; gh pr create --fill --base master
gh pr view --json mergeable ; gh pr checks --watch
gh pr merge --squash --delete-branch
git switch master ; git fetch origin ; git reset --hard origin/master
$id = gh run list --workflow daily-refresh.yml --limit 1 --json databaseId --jq '.[0].databaseId' ; gh run watch $id
git tag lab12-complete origin/master ; git push origin lab12-complete
```

**Expected:** PR checks `ci`, `docker`, `devcontainer`, `terraform` ✓; the **daily refresh ✓**, which proves the refresh SP didn't need the removed grant.

---

## 6. Checkpoint

1. `terraform/` describes schemas, the volume, the group, both service principals and 9 platform grants; providers are pinned; the lock file is committed.
2. `terraform plan` → **No changes**.
3. A manual `REVOKE` shows up as drift and can be restored from code.
4. Only `databricks_grant` (singular); no consumer grants, no `_workspace_users` grant, no secrets in Terraform; tests enforce all three.
5. Schemas and the volume have `prevent_destroy`.
6. No state or plan files in Git.
7. The refresh SP has no grant on `energy_dev`; the daily refresh is green.
8. Claude Code can't run `terraform apply/destroy/state/import`: `.claude/settings.json` is committed, tested, and the refusal was checked once.
9. Tag `lab12-complete`.

---

## 7. Troubleshooting — problems I actually hit

### P1 — `lookup 0: no such host`

```
var.databricks_host
  Enter a value: 0
Error: … Get "https://0/api/2.0/preview/scim/v2/Me…": dial tcp: lookup 0: no such host
```

**Cause:** Terraform prompted for a required variable, and `0` was typed. It happened three times in a new shell.
**Fix:** `export TF_VAR_databricks_host=…` (or put it in `containerEnv` of the dev container), and run plans with **`-input=false`** so Terraform fails clearly instead of prompting:

```
Error: No value for required variable … "databricks_host" is not set
```

**Rule:** when a tool asks for a value you don't know, press **Ctrl+C**.

### P2 — `cannot configure default credentials … Config: profile=free`

**Cause:** the dev container had been **rebuilt** (a `.devcontainer` change), and the Databricks login and `~/.bashrc` exports live in the container's home folder, which a rebuild resets.
**Fix:** log in again inside the container; then `databricks current-user me -p free` must show your user **before** any Terraform command.

### P3 — Login via `docker exec`: `localhost refused to connect`

**Cause:** `databricks auth login` (OAuth U2M) redirects the browser to a callback on `localhost:8020` **inside the container**. In a `docker exec` shell, unlike the VS Code container window, that port isn't forwarded to Windows.
**Fix A:** copy the full `http://localhost:8020/?code=…&state=…` URL from the browser, and in a second `docker exec` shell run `curl -s "<that URL>"` while the login waits.
**Fix B (easier):** log in once in the VS Code container window, which forwards the port.

### P4 — `Error: No configuration files`

**Cause:** Terraform was run in the project root, not in `terraform/`.
**Fix:** check the prompt ends in `/terraform`, or use `terraform -chdir=/workspaces/energy-lakehouse-de/terraform plan …`. (Terraform refusing an empty configuration is a safety feature: an empty configuration would mean "destroy everything".)

### P5 — `Cannot apply incomplete plan`

**Cause:** `terraform apply cleanup.tfplan` on a plan whose `plan` step had failed.
**Fix:** `rm -f cleanup.tfplan`, fix the cause, plan again. `apply` never repairs a plan; it only executes a good one.

### P6 — `Author identity unknown` in the container

**Cause:** Git inside the container had no `user.name`/`user.email`; `gh` wasn't logged in either.
**Fix:** commit from Windows (the same folder), or set the identity in the container (`git config --global …`, lost on rebuild).
**My split:** Terraform in the container; Git, `gh`, `uv` and Claude Code on Windows.

### P7 — The commit "silently" didn't happen; the container overwrote the pre-commit hook

```
gh pr create  →  no pull requests found
git log origin/master..HEAD  →  (empty), git status → 18 changes
```

**Cause:** commands pasted as a block, so an earlier failure went unnoticed. Separately, the dev container's `postCreateCommand` had run `pre-commit install`, writing a **Linux** path into the shared `.git/hooks/pre-commit`.
**Fix:** run commit, push and PR **one line at a time**; `uv run pre-commit install` on Windows after using the container; PR B removed `pre-commit install` from the container setup.

### P8 — The grant was revoked without a reviewed plan

**Symptom:** my first PR B plan said **"No changes"**, and `dev_schema_refresh` no longer appeared in the state at all. Claude Code then claimed the grant "still exists", restored it in code, and the re-import failed:

```
Error: Cannot import non-existent remote object
```

`SHOW GRANTS` confirmed the grant was gone.
**Cause (most likely):** Claude Code, which has a terminal, ran `terraform apply` itself while implementing PR B. The end state was right, but **nobody reviewed the plan**, and its later explanation was wrong.
**Fix:** remove the grant from code again (plan: No changes), and add a CLAUDE.md rule plus `.claude/settings.json` **deny rules** for `terraform apply/destroy/state/import` (Step 11). The first attempt only produced the CLAUDE.md text; the enforced `settings.json` came in the follow-up docs PR.
**Lesson:** an AI assistant is a fast co-engineer, but **infrastructure changes need a human-reviewed plan**. Enforce that with permissions, not with good intentions. And when the assistant explains what happened, **verify against reality** (`SHOW GRANTS`, `terraform state list`).

### P9 — Docker/Dev Containers problems on Windows

| Symptom | Cause | Fix |
| --- | --- | --- |
| "Starting Docker" for a long time | Docker Desktop not running after a reboot | Start Docker Desktop, wait for "Engine running", then reopen; enable "Start Docker Desktop when you sign in" |
| `docker` works in Windows PowerShell but not in VS Code | VS Code started before Docker; stale environment | Close **all** VS Code windows, start `code` again |
| "An error occurred setting up the container", with a `\\wsl.localhost\Ubuntu\mnt\wslg\…wayland-0` mount | VS Code mounts the WSLg socket from the Ubuntu distro, which wasn't running | `"dev.containers.mountWaylandSocket": false` |
| "Dev Containers" command missing | The extension was disabled by an earlier `--disable-extension` start | Restart VS Code normally |

### Other things worth knowing

| Situation | Explanation |
| --- | --- |
| "Your version of Terraform is out of date" | Just a notice; the `required_version` range in `versions.tf` decides |
| `provider_config { workspace_id = … }` in the plan | Shown by recent provider versions; harmless |
| `properties = { "owner" = "root" }` on schemas | Set by Databricks; matched on import, so no diff |
| The state file is local | Fine for one person; teams need **remote state** (Lab 12b) |

---

## 8. What you learned

- **Infrastructure as code for an existing platform:** inventory → code → import blocks → zero-diff plan → apply → "No changes"
- **Drift detection:** a manual change shows up in the next plan and can be undone from code
- **Ownership boundaries:** Terraform (platform), contracts job (consumer grants), bundle (data products), documented in ADR-0010 and enforced by tests
- **Additive (`databricks_grant`) vs. authoritative (`databricks_grants`)**, and why the difference can delete access
- **Safety mechanisms:** `prevent_destroy`, saved plans, `-input=false`, no secrets in state, state never in Git
- **Remove vs. forget** (`destroy` vs. `state rm` / `removed {}`)
- **Working with dev containers:** credentials and environment are lost on rebuild; OAuth callbacks need port forwarding; the Git hooks are shared with the host
- **Governing an AI assistant:** deny rules for infrastructure-changing commands, and verifying its claims against reality

**Interview-ready summary:** *"I adopted a hand-built Unity Catalog setup into Terraform without touching it: an inventory from information_schema, code with import blocks, and a plan that showed 17 imports and zero changes. A manual REVOKE was detected as drift and restored from code. I used only additive per-principal grants and documented a strict boundary: Terraform owns platform grants, my data-contracts job owns consumer grants, and the bundle owns jobs and dashboards, with tests enforcing it. During the first change through Terraform, my AI assistant applied without a reviewed plan, so I added permission rules that leave apply, destroy and state commands to a human."*

---

## 9. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-31 |
| OS | Windows 11 (Docker Desktop 4.97.0, WSL 2) + dev container (devcontainers/python:1-3.12-bookworm) |
| Terraform | v1.16.6 (in the dev container) |
| Provider | databricks/databricks (pinned in `versions.tf`, lock file committed) |
| Databricks | Free Edition, one workspace, Unity Catalog `workspace` |
| State | Local, gitignored (remote state in Lab 12b) |
| Result | 17 imported, zero diff, drift test passed, 1 grant removed, PRs #21 and #22, tag `lab12-complete` |

**Next: Lab 12b — Terraform in CI.**
- **Remote state**, so CI and you share it
- **`terraform plan` on every PR** that touches `terraform/`
- **a scheduled drift check** that fails and emails you if someone changes grants by hand
- `containerEnv` for `TF_VAR_databricks_host` / `DATABRICKS_CONFIG_PROFILE`

After that: the AWS chapter, where Terraform **creates** infrastructure (S3, IAM, storage credentials, GitHub OIDC).
