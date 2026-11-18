"""Guard for .github/workflows/terraform-plan.yml (ADR 0011): plan only, OIDC, Key Vault secrets.

Checks:
- triggers: pull_request on terraform/** and infra/azure/**, weekly schedule, workflow_dispatch;
- permissions exactly contents: read + id-token: write, and no other workflow gets id-token: write;
- never apply: no terraform apply/destroy/import/state, no -auto-approve; init with the
  backend config, plan with -input=false -lock=false -detailed-exitcode;
- exit code 2 passes on pull_request and fails (drift) on schedule/dispatch;
- every `uses:` pinned to an exact vX.Y.Z tag; azure/login via OIDC (repo variables, no creds);
- no `secrets.` anywhere: the Databricks credentials come only from the Key Vault step's outputs
  (masked before they are written), and only the plan step gets them;
- Terraform version = versions.tf, setup-terraform without the wrapper (real exit codes).
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOW_DIR / "terraform-plan.yml"
KEY_VAULT = "kv-energy-0cd70240"
KV_SECRETS = ("databricks-terraform-client-id", "databricks-terraform-client-secret")
EXACT_TAG_RE = re.compile(r"^[\w.-]+/[\w.-]+@v\d+\.\d+\.\d+$")
FORBIDDEN_COMMAND_RE = re.compile(
    r"\bterraform\s+(?:.*\s)?(apply|destroy|import|state)\b|-auto-approve"
)
KV_OUTPUT_RE = re.compile(r"^\$\{\{\s*steps\.kv\.outputs\.\w+\s*\}\}$")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def job(workflow) -> dict:
    (plan_job,) = workflow["jobs"].values()
    return plan_job


def triggers(workflow: dict) -> dict:
    # PyYAML reads the `on` key as True.
    return workflow.get("on") or workflow.get(True)


def run_scripts(job: dict) -> list[str]:
    return [s["run"] for s in job["steps"] if "run" in s]


def plan_step(job: dict) -> dict:
    (step,) = [s for s in job["steps"] if "terraform plan" in (s.get("run") or "")]
    return step


def has_id_token_write(permissions) -> bool:
    if permissions == "write-all":
        return True
    return isinstance(permissions, dict) and permissions.get("id-token") == "write"


def test_has_id_token_write():
    assert has_id_token_write({"id-token": "write"})
    assert has_id_token_write("write-all")
    assert not has_id_token_write({"id-token": "read", "contents": "read"})
    assert not has_id_token_write(None)


@pytest.mark.parametrize(
    ("command", "forbidden"),
    [
        ("terraform apply tfplan", True),
        ("terraform -chdir=terraform apply", True),
        ("terraform plan -auto-approve", True),
        ("terraform state rm x", True),
        ("terraform plan -input=false -lock=false -detailed-exitcode", False),
        ("echo 'apply locally after merge'", False),
    ],
)
def test_forbidden_command_re(command, forbidden):
    assert bool(FORBIDDEN_COMMAND_RE.search(command)) is forbidden


def test_triggers(workflow):
    on = triggers(workflow)
    assert {"terraform/**", "infra/azure/**"} <= set(on["pull_request"]["paths"])
    assert on["schedule"] == [{"cron": "41 5 * * 1"}]
    assert "workflow_dispatch" in on
    assert "push" not in on and "pull_request_target" not in on


def test_permissions(workflow):
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    for key, j in workflow["jobs"].items():
        assert "permissions" not in j, f"job {key} overrides permissions"


def test_id_token_write_only_in_terraform_plan():
    problems = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        if path == WORKFLOW:
            continue
        other = yaml.safe_load(path.read_text(encoding="utf-8"))
        if has_id_token_write(other.get("permissions")):
            problems.append(f"{path.name}: workflow-level id-token: write")
        for key, j in (other.get("jobs") or {}).items():
            if has_id_token_write(j.get("permissions")):
                problems.append(f"{path.name}: job {key}: id-token: write")
    assert not problems, "\n".join(problems)


def test_never_applies(job):
    text = WORKFLOW.read_text(encoding="utf-8")
    assert not FORBIDDEN_COMMAND_RE.search(text), FORBIDDEN_COMMAND_RE.search(text).group(0)
    scripts = run_scripts(job)
    (init,) = [r for r in scripts if "terraform init" in r]
    assert "-backend-config=backend.azure.hcl" in init and "-input=false" in init
    assert "-backend=false" not in init
    run = plan_step(job)["run"]
    for flag in ("-input=false", "-lock=false", "-detailed-exitcode"):
        assert flag in run, f"terraform plan lacks {flag}"
    assert job["defaults"]["run"]["working-directory"] == "terraform"


def test_exit_code_2_is_drift_outside_pull_requests(job):
    run = plan_step(job)["run"]
    assert "set +e" in run and "code=$?" in run, "the plan exit code must be captured"
    assert "GITHUB_STEP_SUMMARY" in run
    start_2 = re.search(r"^\s*2\)", run, re.MULTILINE).start()
    start_other = re.search(r"^\s*\*\)", run, re.MULTILINE).start()
    branch_2 = run[start_2:start_other]
    assert '"${GITHUB_EVENT_NAME}" == "pull_request"' in branch_2
    assert "DRIFT" in branch_2 and "exit 1" in branch_2
    other = run[start_other:]
    assert 'exit "${code}"' in other


def test_actions_pinned_to_exact_tags(job):
    refs = [s["uses"] for s in job["steps"] if "uses" in s]
    assert refs
    unpinned = [r for r in refs if not EXACT_TAG_RE.match(r)]
    assert not unpinned, unpinned


def test_azure_login_uses_oidc_variables(job):
    (login,) = [s for s in job["steps"] if s.get("uses", "").startswith("azure/login@")]
    assert login["with"]["client-id"] == "${{ vars.AZURE_CLIENT_ID }}"
    assert login["with"]["tenant-id"] == "${{ vars.AZURE_TENANT_ID }}"
    assert login["with"]["subscription-id"] == "${{ vars.AZURE_SUBSCRIPTION_ID }}"
    assert "creds" not in login["with"]
    env = job["env"]
    assert env["ARM_USE_OIDC"] == "true"
    for name in ("CLIENT_ID", "TENANT_ID", "SUBSCRIPTION_ID"):
        assert env[f"ARM_{name}"] == f"${{{{ vars.AZURE_{name} }}}}"


def test_no_github_secrets():
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")


def test_databricks_credentials_only_from_key_vault(workflow, job):
    assert not [k for k in workflow.get("env") or {} if k.startswith("DATABRICKS")]
    assert not [k for k in job.get("env") or {} if k.startswith("DATABRICKS")]
    plan = plan_step(job)
    for step in job["steps"]:
        env = step.get("env") or {}
        names = [k for k in env if k.startswith("DATABRICKS")]
        if step is not plan:
            assert not names, f"step {step.get('name')} gets {names}"
    for name in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"):
        assert KV_OUTPUT_RE.match(plan["env"][name]), plan["env"][name]


def test_key_vault_step_masks_before_output(job):
    (kv,) = [s for s in job["steps"] if s.get("id") == "kv"]
    run = kv["run"]
    assert job["env"]["KEY_VAULT"] == KEY_VAULT and '--vault-name "${KEY_VAULT}"' in run
    assert "databricks-terraform-${name}" in run and "client-id client-secret" in run
    assert all(s.removeprefix("databricks-terraform-") in run for s in KV_SECRETS)
    assert run.index("::add-mask::") < run.index("GITHUB_OUTPUT"), "mask before writing"
    assert "set -euo pipefail" in run


def test_terraform_version_matches_versions_tf(job):
    versions = (REPO_ROOT / "terraform" / "versions.tf").read_text(encoding="utf-8")
    match = re.search(r'required_version\s*=\s*"~>\s*(\d+\.\d+\.\d+)"', versions)
    assert match and job["env"]["TERRAFORM_VERSION"] == match.group(1)
    (setup,) = [
        s for s in job["steps"] if s.get("uses", "").startswith("hashicorp/setup-terraform")
    ]
    assert setup["with"]["terraform_wrapper"] is False
    assert setup["with"]["terraform_version"] == "${{ env.TERRAFORM_VERSION }}"


def test_runner_pinned(job):
    assert job["runs-on"] == "ubuntu-24.04"
