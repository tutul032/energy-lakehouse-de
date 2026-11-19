# Lab 13 — Azure Foundation: Remote Terraform State, GitHub OIDC, Key Vault and a Weekly Drift Alarm

> Part of the series **"Databricks Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 12 — Terraform (adopt the Unity Catalog setup). Next: Project 3 on Azure (AI Search, Foundry), reusing this foundation.

## 1. Goal

After Lab 12, Terraform managed the platform, with three weak points:

- The **state** was a file on one laptop. It wasn't shared or locked, and it would be lost with the laptop.
- **Drift** showed up only when someone happened to run `terraform plan`.
- Terraform ran with **my personal login**. No pipeline could run it.

This lab fixes all three, the way platform teams do it on Azure:

| PR | Content | Result |
| --- | --- | --- |
| **A, Azure foundation** (`infra/azure/`, #25) | Resource group, state storage (no account keys), Key Vault (RBAC), an Entra app that GitHub can use **without a secret** (OIDC), a budget | 13 Azure resources from code, all checked from the CLI |
| **B, use it** (`terraform/`, #26) | Move the Databricks state into Azure; a dedicated Terraform service principal whose secret exists **only in Key Vault**; a plan on every PR; a **weekly drift check** that fails and emails | State in Azure with locking; plan on PRs; a real drift test detected and repaired |

**My results:** `Plan: 13 to add` → applied → second plan "No changes". State migrated (16 resources) and the plan read from Azure showed "No changes". The CI plan logged in through OIDC + Key Vault and showed "No changes". A manual `REVOKE` turned the drift run **red**, applying from code fixed it, and the next run was **green**. Logins survive container rebuilds. Tag `lab13-complete`.

**Time:** two evenings, plus one evening of troubleshooting (see section 7).

---

## 2. Concepts in plain words

### 2.1 Remote state

Terraform's state lists every object it manages. If the state lives on a laptop, nobody else (no colleague, no CI job) can use it, two runs can overwrite each other, and losing the laptop means losing the record. Remote state puts the file in shared storage:

| Feature | How Azure Storage provides it |
| --- | --- |
| Shared | One blob: `stenergytf0cd70240/tfstate/energy-lakehouse/databricks.tfstate` |
| Locked | Terraform takes a **blob lease** during `plan`/`apply`: "Acquiring state lock…" |
| History | Blob versioning plus 7-day soft delete keep older versions |
| Access control | Entra ID roles; **account keys are disabled**, so there's no master password |

### 2.2 The bootstrap problem

The state storage is itself created by Terraform, so where does *that* state live? Answer: in a small **bootstrap root** (`infra/azure/`) whose state stays **local** (gitignored and backed up). Every other root uses the remote backend. That's the standard pattern: one tiny bootstrap, everything else remote.

### 2.3 OIDC: logging in without a secret

Before, CI used a **client secret** stored in GitHub secrets: long-lived, copyable, and it needs rotation. With **OIDC (workload identity federation)**:

```
GitHub Actions job ──"I am repo X, event pull_request" (signed token)──▶ Entra ID
Entra ID checks: does an app trust exactly this subject? ──yes──▶ short-lived Azure token (1 h)
```

No secret exists anywhere. The trust is a **federated credential** on the Entra app, with the issuer `token.actions.githubusercontent.com` and an exact **subject**, for example:

```
repo:tutul032@32595971/energy-lakehouse-de@1421373042:pull_request
repo:tutul032@32595971/energy-lakehouse-de@1421373042:ref:refs/heads/master
```

The numbers are the owner ID and repository ID. GitHub uses this **immutable** format, so a renamed or re-created repository with the same name can't inherit the trust (problem P7).

### 2.4 Key Vault for the one secret that's left

Databricks Free Edition has no account-level APIs, so GitHub can't log in to Databricks with OIDC. Terraform needs an OAuth secret for `sp-energy-terraform`. That secret lives **only in Key Vault**. The workflow logs in to Azure with OIDC, reads the secret, masks it and passes it only to the plan step. It's never in GitHub secrets, never in Terraform state and never in a file.

### 2.5 Least privilege, data plane only

| Identity | Role | Scope |
| --- | --- | --- |
| GitHub app `gh-energy-lakehouse` | Storage Blob Data Contributor | only the `tfstate` container |
| GitHub app | Key Vault Secrets User (read) | only the vault |
| Me (operator) | Storage Blob Data Contributor, Key Vault Secrets **Officer** (write) | container, vault |

The GitHub app has **no subscription role**: it can't create, delete or even list resources. Note that being subscription **Owner** isn't enough to read blobs when account keys are off. That needs a *data-plane* role (problem P4).

### 2.6 Plan on PRs, drift check on a schedule: same job, different meaning

`terraform plan -detailed-exitcode` returns **0** = no changes, **2** = changes, **1** = error.

| Event | Exit 2 means | Job result |
| --- | --- | --- |
| Pull request | "This PR changes the platform": that's the review | ✅ passes; the plan goes into the job summary |
| Schedule (Mon 05:41 UTC) / manual | `master` and reality differ: **drift** | ❌ fails, so GitHub emails you |

**CI never applies.** A person applies a saved plan in the dev container, as in Lab 12.

### 2.7 A budget is an alarm, not a limit

An Azure budget emails at 50/80/100 % and **never stops spending**. On the free account, the spending limit stops it. After an upgrade, nothing does.

---

## 3. Prerequisites

- Lab 12 done: `terraform/` manages the platform and `plan` shows "No changes"
- Dev container from Lab 11 with the **azure-cli** feature (Azure CLI 2.92.0)
- An Azure account where you own the subscription. I used the **free account** (USD 200 credit for 30 days, spending limit on). Note the end date; mine is around 2026-12-15.
- Golden rules from Lab 12, plus:
  - **Secrets go only into Key Vault** (`az keyvault secret set --file …`) or `gh secret set` prompts. Never into chat, files or Terraform.
  - **IDs are fine to share** (tenant, subscription, client IDs). They aren't secrets.
  - **Check the prompt:** `PS C:\dev\…>` = Windows (git, `gh`), `vscode ➜ /workspaces/energy-lakehouse-de` = container (Terraform, `az`, `databricks`).

---

## 4. Steps — PR A: the Azure foundation (`infra/azure/`)

### Step 01 — Branch and Claude Code prompt (plan mode)

```powershell
git switch master; git pull
git switch -c feat/lab13-azure-foundation
```

Prompt (Claude Code, plan mode first):

```
Create a new Terraform root infra/azure/ (own local state, gitignored) for the Azure foundation:
- azurerm + azuread providers, pinned, lock file with hashes for linux/windows/darwin
- resource group rg-energy-platform (swedencentral) with tags project/owner/cost-center
- storage account for Terraform state: shared keys OFF, TLS1.2, no public blobs, versioning,
  7-day soft delete; private container "tfstate"
- Key Vault with RBAC, soft delete 7 days, purge protection off (easy teardown); Terraform never writes secrets
- Entra app + SP gh-energy-lakehouse, NO client secret; federated credentials for GitHub OIDC
  (pull_request and refs/heads/master of this repo)
- role assignments: SP = Storage Blob Data Contributor on the container + Key Vault Secrets User;
  operator (current az login) = Blob Data Contributor + Key Vault Secrets Officer
- subscription budget 10 USD/month, email at 50/80/100 %
- names deterministic (suffix = first 8 hex of sha1(subscription_id)), no random provider
- README with bootstrap, checks, state backup, teardown, exit plan; CI: fmt, validate, tflint only
Never run apply. Show me the plan of files first.
```

**Expected:** about 11 `.tf` files, `terraform.tfvars.example`, a README, and a `ci.yml` matrix entry for `infra/azure`.

### Step 02 — Log in to Azure inside the container

```bash
az login --tenant <your-tenant-id>
az account show --query "{sub:name, tenant:tenantId, user:user.name}" -o table
```

**Expected:** your subscription ("Azure subscription 1") and your user.

> ⚠️ `az login --use-device-code` failed with **AADSTS530035** (problem P1). Use the browser login with `--tenant`.

> 📷 *Screenshot: `az account show` table.*

### Step 03 — Fill the variables and run the bootstrap plan

```bash
cd infra/azure
cp terraform.tfvars.example terraform.tfvars    # subscription_id, owner, alert_email (gitignored)
for ns in Microsoft.Storage Microsoft.KeyVault Microsoft.Consumption Microsoft.CostManagement; do
  az provider register --namespace "$ns" --wait
done
terraform init -lockfile=readonly
terraform fmt -check && terraform validate
terraform plan -input=false -out bootstrap.tfplan
```

**Expected:** `Plan: 13 to add, 0 to change, 0 to destroy.` Read every resource. There must be **no client secret** and no subscription-wide role for the app.

### Step 04 — Apply the saved plan and check the second plan (you, not Claude)

```bash
terraform apply bootstrap.tfplan
terraform output
terraform plan -input=false        # must say: No changes
```

**Expected:** outputs `client_id`, `tenant_id`, `subscription_id`, `storage_account_name` (`stenergytf0cd70240`), `key_vault_name` (`kv-energy-0cd70240`) and others. None of them is a secret.

> 📷 *Screenshot: the resource group in the portal with storage account and Key Vault.*

### Step 05 — Verify the security settings from the CLI

```bash
SA=$(terraform output -raw storage_account_name)
az storage account show -n "$SA" --query "{tls:minimumTlsVersion, sharedKey:allowSharedKeyAccess}" -o table
az keyvault show -n "$(terraform output -raw key_vault_name)" --query "properties.enableRbacAuthorization"
az ad app federated-credential list --id "$(terraform output -raw client_id)" --query "[].subject" -o tsv
az consumption budget list --query "[].{name:name, amount:amount}" -o table
```

**Expected:** `TLS1_2 / False`, `true`, the two subjects, and the budget of 10.
Role assignments can take a few minutes to propagate. `AuthorizationPermissionMismatch` right after apply means wait and retry.

### Step 06 — Back up the bootstrap state

The local `infra/azure/terraform.tfstate` is the only record of this root:

```bash
az storage blob upload --auth-mode login --account-name "$SA" -c tfstate \
  -n bootstrap/infra-azure.tfstate -f terraform.tfstate --overwrite
```

### Step 07 — Repository variables (Windows)

These are **variables**, not secrets, because none of the values is secret:

```powershell
gh variable set AZURE_CLIENT_ID       --body <client_id output>
gh variable set AZURE_TENANT_ID       --body <tenant_id output>
gh variable set AZURE_SUBSCRIPTION_ID --body <subscription_id output>
gh variable list
```

### Step 08 — Commit from Windows, PR, merge

```powershell
git add -A; git status        # no *.tfstate, *.tfvars, *.tfplan in the list!
git commit -m "feat: Azure foundation (state storage, Key Vault, GitHub OIDC app, budget) via Terraform"
git push -u origin feat/lab13-azure-foundation
gh pr create --fill
```

**Expected:** CI green (fmt, validate, tflint for both roots). Merged as **PR #25**.

---

## 5. Steps — PR B: remote state, Terraform identity, plan on PRs, drift check

### Step 09 — Claude Code prompt

```
PR B for terraform/ (ADR 0011):
1. azurerm backend, partial config: terraform/backend.tf = backend "azurerm" {}, and
   terraform/backend.azure.hcl (identifiers only, use_azuread_auth = true, key energy-lakehouse/databricks.tfstate)
2. databricks_service_principal "sp-energy-terraform" (prevent_destroy) + databricks_group_member
   into the built-in admins group (group read via data source, never managed). No secret in Terraform.
3. .github/workflows/terraform-plan.yml: PR (terraform/**, infra/azure/**), Mondays 05:41 UTC, dispatch;
   permissions id-token: write; azure/login OIDC with vars.AZURE_*; read databricks-terraform-client-id/-secret
   from Key Vault and mask them; init -backend-config=backend.azure.hcl; plan -detailed-exitcode -lock=false;
   exit 2 passes on PRs and fails as DRIFT on schedule/dispatch; never apply. Plan in the job summary.
4. tests/test_terraform_plan_workflow.py: no apply, no secrets.*, pinned actions
5. ADR 0011, runbook sections "Terraform state and drift" + "Terraform drift alert" + Terraform SP secret rotation
6. dev container: persist az and databricks logins in named volumes
Do NOT migrate the state and do NOT apply — I do that. List every file you changed at the end.
```

> ⚠️ My first result was **incomplete**: the workflow and backend were there, but the service principal and group membership were missing (P6). Compare the file list with the prompt before you continue.

### Step 10 — Create the Terraform SP (still on local state)

Container, from the repo root:

```bash
databricks current-user me -p free            # warm-up / login check
terraform -chdir=terraform plan -input=false -out=sp.tfplan
```

**Expected:** `Plan: 2 to add` (the SP and its admins membership). Nothing else.

```bash
terraform -chdir=terraform apply sp.tfplan
```

### Step 11 — Put the SP's secret straight into Key Vault

The value never appears on screen or on a command line:

```bash
sp_id="$(databricks service-principals list -p free -o json \
  | jq -r '.[] | select(.displayName == "sp-energy-terraform") | .id')"
app_id="$(databricks service-principals list -p free -o json \
  | jq -r '.[] | select(.displayName == "sp-energy-terraform") | .applicationId')"

az keyvault secret set --vault-name kv-energy-0cd70240 --name databricks-terraform-client-id \
  --value "$app_id" --output none          # an ID, not a secret

secret="$(databricks service-principal-secrets-proxy create "${sp_id}" --lifetime 31536000s \
  -p free -o json | jq -r .secret)"
az keyvault secret set --vault-name kv-energy-0cd70240 --name databricks-terraform-client-secret \
  --file <(printf '%s' "${secret}") --output none
unset secret
az keyvault secret list --vault-name kv-energy-0cd70240 --query "[].name" -o tsv
```

**Expected:** the two secret names. Note the expiry (365 days) in the runbook's secret table.

> 📷 *Screenshot: Key Vault → Secrets with the two names (values hidden).*

### Step 12 — Migrate the state to Azure

```bash
terraform -chdir=terraform plan -input=false          # 1. start clean: No changes
cp terraform/terraform.tfstate terraform/terraform.tfstate.pre-migration-backup
cp terraform/terraform.tfstate ~/tfstate-backup-$(date +%Y%m%d).json    # 2. backup outside the repo
terraform -chdir=terraform init -backend-config=backend.azure.hcl -migrate-state
```

Terraform asks:

```
Do you want to copy existing state to the new backend?
  …
  Only 'yes' will be accepted to approve.
  Enter a value:
```

Type **`yes`** (P3).

```bash
terraform -chdir=terraform plan -input=false
```

**Expected:** `Acquiring state lock…`, 16 resources refreshed, **No changes**, `Releasing state lock…`. The lock lines prove the state is now remote.

> 📷 *Screenshot: Portal → storage account → `tfstate` → `energy-lakehouse/databricks.tfstate`.*

### Step 13 — Commit, PR: the first plan through OIDC

```powershell
git add -A; git status        # backend.tf, backend.azure.hcl, workflow, test, ADR, runbook — no state files
git commit -m "feat: remote Terraform state in Azure, Terraform SP, plan on PRs and weekly drift check"
git push -u origin <branch>
gh pr create --fill
gh pr checks --watch
```

**Expected:** job **terraform-plan** green. The job summary shows exit code `0` and "No changes".

> ⚠️ My first run failed in `azure/login` with **AADSTS700213** (P7): the federated credentials used the old subject format. Fixed in `infra/azure/identity.tf`, applied from the container, then **rerun**.

Merge, then: **PR #26**.

### Step 14 — Run the drift check on master

```powershell
gh workflow run terraform-plan.yml --ref master
gh run watch (gh run list --workflow terraform-plan.yml --limit 1 --json databaseId -q '.[0].databaseId')
```

**Expected:** ✅, with the annotation `No changes: terraform/ matches the workspace.`

### Step 15 — Test the alarm: create drift by hand

In the Databricks SQL editor (as yourself), remove a grant that Terraform manages:

```sql
REVOKE USE SCHEMA ON SCHEMA workspace.energy_raw FROM `7d0f1e32-4a86-42e2-b352-461dae1b3634`;  -- sp-energy-cicd
```

```powershell
gh workflow run terraform-plan.yml --ref master
```

**Expected:** ❌ with the annotation **`Terraform DRIFT`**, and the job summary shows `+ create databricks_grant.raw_schema_use["cicd"]`. My run was 44410355754. GitHub also sends an email.

> 📷 *Screenshot: the red run with the DRIFT annotation and the plan in the summary.*

Repair it **from the code**, not by hand (container):

```bash
terraform -chdir=terraform plan -input=false -out=restore.tfplan     # Plan: 1 to add (the grant)
terraform -chdir=terraform apply restore.tfplan
```

Then run the check again:

```powershell
gh workflow run terraform-plan.yml --ref master
```

**Expected:** ✅ "No changes" (my run: 44412079300).

> 📷 *Screenshot: `gh run list` — green, red (drift), green.*

### Step 16 — Clean up and prove the rebuild works

```bash
rm -f terraform/terraform.tfstate terraform/terraform.tfstate.pre-migration-backup terraform/terraform.tfstate.backup
terraform -chdir=terraform plan -input=false       # still: lock + No changes (from Azure)
```

Then **Dev Containers: Rebuild Container**, and in the new shell:

```bash
az account show --query name -o tsv
databricks current-user me --profile free | head -5
terraform -chdir=terraform init -backend-config=backend.azure.hcl -input=false >/dev/null && \
  terraform -chdir=terraform plan -input=false | tail -3
```

**Expected:** all three work **without logging in again**.

Tag it (Windows):

```powershell
git switch master; git pull
git tag lab13-complete
git push origin lab13-complete
```

---

## 6. Checkpoint

| Check | How | Expected |
| --- | --- | --- |
| Bootstrap clean | `terraform -chdir=infra/azure plan` | No changes |
| No account keys | `az storage account show … allowSharedKeyAccess` | `False` |
| State is remote | `terraform -chdir=terraform plan` | "Acquiring state lock", No changes |
| No local state | `ls terraform/*.tfstate*` | nothing |
| OIDC works | latest `terraform-plan` run | ✅ |
| Drift alarm works | REVOKE → dispatch | ❌ `Terraform DRIFT`, then ✅ after the apply |
| No secrets in GitHub for Terraform | `gh secret list` | no Terraform secret; only the 3 `AZURE_*` **variables** |
| Logins persist | rebuild the container | `az` and `databricks` work without login |
| Budget | `az consumption budget list` | 10 USD |

---

## 7. Troubleshooting — problems I actually hit

### P1 — `AADSTS530035` at `az login --use-device-code`

The device-code login was **blocked by security defaults**. New Azure tenants enable them, and they treat device-code flow as risky.

**Fix:** use the browser flow and name the tenant: `az login --tenant <tenant-id>`, then complete MFA. Inside the dev container, the browser opens on Windows and the callback works.

### P2 — Terraform asked for variables / `No value for required variable`

`infra/azure` needs `subscription_id`, `owner` and `alert_email`. With `-input=false` the plan fails clearly instead of prompting.

**Fix:** `cp terraform.tfvars.example terraform.tfvars` and fill it in. It's gitignored, so check `git status` before every commit.

### P3 — Typed `1` at the migration prompt

`-migrate-state` asks "Do you want to copy existing state…? Only 'yes' will be accepted". `1` isn't "yes", so Terraform asked again. **Type `yes`.** Lab 12's rule still applies: never answer a prompt you don't understand. Here the answer *was* understood. Back up the state first (Step 12).

### P4 — `AuthorizationPermissionMismatch` on the state blob

I'm subscription Owner, but **account keys are off**, so blob access needs a **data-plane** role (Storage Blob Data Contributor). The bootstrap assigns it, but propagation takes a few minutes. **Wait and retry**; don't turn shared keys back on.

### P5 — Command in the wrong shell or folder

`terraform` in PowerShell → "not recognized" (it lives in the container). `gh` in the container → no login (by design, GitHub CLI only on Windows). `terraform plan` from the repo root → `No configuration files`.

**Fix:** read the prompt before every command. Use `terraform -chdir=terraform …` from the repo root, so the folder never matters.

| Prompt | Where | Use for |
| --- | --- | --- |
| `PS C:\dev\energy-lakehouse-de>` | Windows PowerShell | git, `gh`, PRs, tags |
| `vscode ➜ /workspaces/energy-lakehouse-de (branch) $` | container bash | `terraform`, `az`, `databricks` |

### P6 — Claude Code delivered only part of PR B

The workflow and backend were there, but `sp-energy-terraform` and its admins membership were missing, so the CI plan would have had no identity to log in with.

**Fix:** a follow-up prompt naming the missing parts. **Lesson:** end every prompt with "list every file you changed", and compare that list with the requirements. A summary isn't proof (the same lesson as PR #23, whose title promised tutorials it didn't contain).

### P7 — `AADSTS700213: No matching federated identity record found for presented assertion subject`

The federated credentials used `repo:tutul032/energy-lakehouse-de:pull_request`, but GitHub sent the **immutable** format `repo:tutul032@32595971/energy-lakehouse-de@1421373042:pull_request`.

**Fix:** build the subject from four variables in `identity.tf`, then plan and apply `infra/azure` and rerun the job. Look up the IDs:

```powershell
gh api repos/tutul032/energy-lakehouse-de --jq '.owner.id, .id'
```

**Tip:** the exact subject GitHub sent is in the error message. Copy it from there.

### P8 — Logins lost after every container rebuild

`~/.azure` and `~/.databricks` live in the container's home folder, which a rebuild resets.

**Fix:** named Docker volumes in `devcontainer.json` (`mounts`), plus `DATABRICKS_CONFIG_FILE=/home/vscode/.databricks/databrickscfg` and a `chown` in `post-create.sh`. After that change I needed **one** fresh `databricks auth login` (the config file had moved), and logins have survived every rebuild since. Never bind-mount host credential folders into a container.

### P9 — Daily refresh failed during the lab (not caused by it)

Email: "daily-refresh: All jobs have failed". The log showed **Open-Meteo timeouts** for 3 of 7 weather locations, an external API outage. `gh run rerun <id> --failed` was green.

**Lesson:** check the log before assuming your change broke something. Backlog: graceful degradation (upload what succeeded, fail at the end), a 60 s timeout with backoff.

### P10 — ruff reformatted code blocks in the Markdown tutorials

ruff 0.17 also formats Python code blocks inside `.md` files, and it "fixed" the illustrative snippets. **Fix:** `pyproject.toml` → `[tool.ruff] extend-exclude = ["docs"]`.

### Other things worth knowing

| Situation | Explanation |
| --- | --- |
| The CI plan uses `-lock=false` | A plan never writes state, and it must never hold a lease that blocks a local apply |
| `Error acquiring the state lock` locally | A crashed run left a lease. If nobody else is running: `terraform force-unlock <id>` |
| `workflow_dispatch` from a feature branch fails in `azure/login` | Only `pull_request` and `refs/heads/master` are trusted. That's intended |
| A job with `environment:` | Sends the subject `…:environment:<name>`, which needs its own federated credential |
| Secret expiry | `sp-energy-terraform` secret: 365 days. Rotation steps are in the runbook ("Terraform SP secret (Key Vault)") |
| Free credit ends (~2026-12-15) | Decide by 12-08: upgrade (costs cents/month), **or** migrate state back to local, then `terraform destroy` `infra/azure` (runbook + `infra/azure/README.md`) |

---

## 8. What you learned

- **Bootstrap vs. remote state:** a tiny local-state root creates the storage; everything else uses the `azurerm` backend with locking and versioning
- **State migration** with a clean start, a backup and `-migrate-state`, verified by the lock lines and "No changes"
- **GitHub → Azure with OIDC:** federated credentials, the immutable subject format, no client secret anywhere
- **Key Vault** as the single home for the one unavoidable secret; reading and masking it in CI; writing it without it ever appearing on screen
- **Least privilege on the data plane:** container- and vault-scoped roles, no subscription role for CI, shared keys disabled
- **Plan on PRs vs. drift on a schedule** with `-detailed-exitcode`; CI never applies
- **A tested alarm:** create drift, see it fail, repair from code, see it pass
- **Cost governance:** budgets alert, they don't cap; an exit plan with a date
- **Supervising an AI assistant:** compare its file list with the requirements

**Interview-ready summary:** *"I moved our Terraform state from a laptop into Azure Storage with Entra-ID-only access, blob-lease locking and versioning, bootstrapped from a separate small root. GitHub Actions logs in to Azure through OIDC, so there's no client secret, and reads the one Databricks credential it needs from Key Vault. Every PR shows its plan, and a weekly job fails and emails when master and the workspace drift apart. I tested it by revoking a grant by hand: the job went red, I applied from code, and it went green. CI only plans; applies are human-reviewed saved plans."*

---

## 9. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-11-17 to 2026-11-19 |
| OS | Windows 11 (Docker Desktop 4.97.0, WSL 2) + dev container |
| Terraform | v1.16.6; providers `azurerm` 5.12.0, `azuread` 3.10.0, `databricks/databricks` (lock files committed) |
| Azure CLI | 2.92.0 (dev container feature) |
| Azure | Free account (USD 200 credit, spending limit on), region `swedencentral`, security defaults on |
| GitHub Actions | `azure/login@v3.1.0`, `hashicorp/setup-terraform@v4.0.1`, runner `ubuntu-24.04` |
| Databricks | Free Edition, Unity Catalog `workspace` |
| Result | PR #25 (13 resources), PR #26 (state migrated, 16 resources), drift test red → green (runs 44410355754, 44412079300), tag `lab13-complete` |

**Next:** Project 3 on Azure (Azure AI Search + Foundry). It reuses this foundation: OIDC for CI, Key Vault for keys, and a new state key in the same `tfstate` container.
