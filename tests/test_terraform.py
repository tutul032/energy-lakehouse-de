"""Terraform guard: terraform/ adopts the platform objects without owning more than it should.

Static checks on the .tf files (no Terraform needed; the ci.yml `terraform` job runs fmt, validate
and tflint). Checks:
- grants only via databricks_grant (singular, one principal); never databricks_grants or
  databricks_permissions; never the _workspace_users catalog grant;
- no databricks_grant targets the consumer group energy-consumers: its USE SCHEMA and table
  grants come from apply_contracts (ADR 0010);
- every databricks_schema / databricks_volume has `prevent_destroy = true`;
- no secrets: no token/password/client_secret arguments, no OAuth secret resources, no `profile`
  or hardcoded host in the provider block;
- provider pinned to an exact version that matches .terraform.lock.hcl; Terraform and tflint
  versions equal in versions.tf, ci.yml and the dev container;
- every managed resource has an import block; .gitignore keeps state and tfvars out of git.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TF_DIR = REPO_ROOT / "terraform"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
BLOCK_RE = re.compile(
    r"^(resource|import|provider|terraform|locals|variable)\b([^{\n]*)\{", re.MULTILINE
)
SECRET_ARG_RE = re.compile(
    r"^\s*(token|password|client_secret|azure_client_secret|google_credentials)\s*=",
    re.MULTILINE | re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(r"dapi[0-9a-f]{20,}|dose[0-9a-f]{20,}", re.IGNORECASE)
CONSUMERS_GROUP = "energy-consumers"
# Every way a .tf file can name the consumer group.
CONSUMER_REFS = (f'"{CONSUMERS_GROUP}"', "var.consumers_group", "databricks_group.consumers")
FORBIDDEN_RESOURCES = {
    "databricks_grants",
    "databricks_permissions",
    "databricks_service_principal_secret",
    "databricks_token",
    "databricks_obo_token",
}


def strip_comments(text: str) -> str:
    """Drop `#` and `//` line comments (no string in these files contains them)."""
    return "\n".join(re.sub(r"\s*(#|//).*$", "", line) for line in text.splitlines())


def blocks(text: str) -> list[tuple[str, list[str], str]]:
    """Top-level blocks as (keyword, labels, body), matching braces."""
    text = strip_comments(text)
    found = []
    for match in BLOCK_RE.finditer(text):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        labels = re.findall(r'"([^"]*)"', match.group(2))
        found.append((match.group(1), labels, text[match.end() : i - 1]))
    return found


def all_blocks() -> list[tuple[str, list[str], str]]:
    return [b for path in sorted(TF_DIR.glob("*.tf")) for b in blocks(path.read_text("utf-8"))]


def tf_text() -> str:
    return "\n".join(p.read_text("utf-8") for p in sorted(TF_DIR.glob("*.tf")))


def required_terraform_version() -> str:
    match = re.search(r'required_version\s*=\s*"~>\s*(\d+\.\d+\.\d+)"', tf_text())
    assert match, 'versions.tf needs required_version = "~> X.Y.Z"'
    return match.group(1)


def ci_terraform_job() -> dict:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    return workflow["jobs"]["terraform"]


def devcontainer_terraform_feature() -> dict:
    text = (REPO_ROOT / ".devcontainer/devcontainer.json").read_text("utf-8")
    config = json.loads(
        "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))
    )
    (options,) = [o for ref, o in config["features"].items() if "/features/terraform:" in ref]
    return options


def grant_principal_sources(tf_blocks: list[tuple[str, list[str], str]]) -> dict[str, str]:
    """databricks_grant name -> its body, plus the locals bodies if it references `local.`."""
    local_bodies = "\n".join(body for kind, _, body in tf_blocks if kind == "locals")
    return {
        labels[1]: body + ("\n" + local_bodies if "local." in body else "")
        for kind, labels, body in tf_blocks
        if kind == "resource" and labels[0] == "databricks_grant"
    }


def test_blocks_parses_nested_braces():
    text = (
        'resource "a" "b" {\n  x = { y = 1 }\n  lifecycle {\n    z = 2\n  }\n}\n'
        '# resource "c" "d" {\n'
    )
    ((keyword, labels, body),) = blocks(text)
    assert (keyword, labels) == ("resource", ["a", "b"])
    assert "lifecycle" in body and "z = 2" in body


def test_strip_comments():
    assert strip_comments('x = 1 # note\n// all\ny = "a"') == 'x = 1\n\ny = "a"'


def test_only_singular_grants():
    types = {labels[0] for kind, labels, _ in all_blocks() if kind == "resource"}
    assert "databricks_grant" in types
    assert not types & FORBIDDEN_RESOURCES, sorted(types & FORBIDDEN_RESOURCES)


def test_grants_have_one_principal_and_skip_workspace_users():
    for kind, labels, body in all_blocks():
        if kind == "resource" and labels[0] == "databricks_grant":
            assert len(re.findall(r"^\s*principal\s*=", body, re.MULTILINE)) == 1, labels
    assert "_workspace_users" not in strip_comments(tf_text())


def test_grant_principal_sources_include_referenced_locals():
    text = (
        'locals {\n  sps = { a = "x" }\n}\n'
        'resource "databricks_grant" "via_local" {\n  principal = local.sps["a"]\n}\n'
        'resource "databricks_grant" "direct" {\n  principal = "y"\n}\n'
    )
    sources = grant_principal_sources(blocks(text))
    assert 'a = "x"' in sources["via_local"]
    assert 'a = "x"' not in sources["direct"]


def test_consumers_group_is_not_granted_by_terraform():
    """USE SCHEMA/table grants of energy-consumers belong to apply_contracts (ADR 0010)."""
    tf_blocks = all_blocks()
    (variable,) = [
        body
        for kind, labels, body in tf_blocks
        if kind == "variable" and labels == ["consumers_group"]
    ]
    assert f'"{CONSUMERS_GROUP}"' in variable, "var.consumers_group must default to the group"
    for name, source in grant_principal_sources(tf_blocks).items():
        hits = [ref for ref in CONSUMER_REFS if ref in source]
        assert not hits, f"databricks_grant.{name} targets {CONSUMERS_GROUP} via {hits}"


@pytest.mark.parametrize("resource_type", ["databricks_schema", "databricks_volume"])
def test_prevent_destroy(resource_type):
    found = [
        (labels, body)
        for kind, labels, body in all_blocks()
        if kind == "resource" and labels[0] == resource_type
    ]
    assert found, f"no {resource_type} resource"
    for labels, body in found:
        assert re.search(r"prevent_destroy\s*=\s*true", body), f"{labels}: no prevent_destroy"


def test_no_secrets_or_tokens():
    text = strip_comments(tf_text())
    for path in [*TF_DIR.glob("*.tf"), *TF_DIR.glob("*.tfvars.example")]:
        assert not SECRET_VALUE_RE.search(path.read_text("utf-8")), path.name
    assert not SECRET_ARG_RE.search(text), SECRET_ARG_RE.search(text).group(0)


def test_provider_auth_from_environment():
    (provider,) = [
        body
        for kind, labels, body in all_blocks()
        if kind == "provider" and labels == ["databricks"]
    ]
    assert re.search(r"^\s*host\s*=\s*var\.\w+\s*$", provider, re.MULTILINE), provider
    assert not re.search(r"^\s*(profile|config_file|client_id)\s*=", provider, re.MULTILINE)


def test_provider_pinned_and_locked():
    match = re.search(
        r'databricks\s*=\s*\{[^}]*source\s*=\s*"databricks/databricks"[^}]*version\s*=\s*"([^"]+)"',
        tf_text(),
    )
    assert match and SEMVER_RE.match(match.group(1)), "provider version must be exact X.Y.Z"
    lock = (TF_DIR / ".terraform.lock.hcl").read_text("utf-8")
    assert re.search(
        rf'"registry.terraform.io/databricks/databricks"\s*\{{\s*version\s*=\s*"{match.group(1)}"',
        lock,
    ), "terraform/.terraform.lock.hcl does not lock the pinned provider version"


def test_terraform_and_tflint_versions_match_ci_and_devcontainer():
    version = required_terraform_version()
    job = ci_terraform_job()
    feature = devcontainer_terraform_feature()
    assert job["env"]["TERRAFORM_VERSION"] == version == feature["version"]
    assert job["env"]["TFLINT_VERSION"] == f"v{feature['tflint']}"
    assert SEMVER_RE.match(feature["tflint"])


def test_ci_job_has_no_credentials():
    job = ci_terraform_job()
    assert "DATABRICKS" not in json.dumps(job), "terraform job must not get Databricks credentials"
    runs = [s["run"] for s in job["steps"] if "run" in s]
    assert any("fmt -check" in r for r in runs)
    assert any("init -backend=false" in r for r in runs)
    assert any(r.strip() == "terraform validate" for r in runs)
    assert not any(re.search(r"\b(plan|apply|import)\b", r) for r in runs)


def test_every_resource_is_imported():
    resources = {
        f"{labels[0]}.{labels[1]}" for kind, labels, _ in all_blocks() if kind == "resource"
    }
    imported = {
        re.sub(r"\[.*\]$", "", m)
        for kind, _, body in all_blocks()
        if kind == "import"
        for m in re.findall(r"^\s*to\s*=\s*(\S+)", body, re.MULTILINE)
    }
    assert resources == imported, f"missing imports: {sorted(resources - imported)}"


def test_gitignore_keeps_state_and_tfvars_out():
    lines = set((REPO_ROOT / ".gitignore").read_text("utf-8").splitlines())
    assert {".terraform/", "*.tfstate", "*.tfstate.*", "*.tfvars", "!*.tfvars.example"} <= lines
    assert (TF_DIR / "terraform.tfvars.example").is_file()
