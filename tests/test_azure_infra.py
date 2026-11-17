"""Azure bootstrap guard: infra/azure/ is pinned, secret-free, tagged and private.

Static checks on the .tf files (no Terraform, no Azure login; the ci.yml `terraform` job runs fmt,
validate and tflint on this root too). Checks:
- azurerm and azuread pinned to exact versions that .terraform.lock.hcl locks; Terraform version
  equal to terraform/ (and therefore CI and the dev container); no backend block (local state for
  this bootstrap root only);
- no secrets: no secret/password/key arguments, no resources that create secrets or credentials
  (app passwords, SP passwords, Key Vault secrets/keys), no credentials in the provider blocks;
- the resource group carries the project, owner and cost-center tags;
- the state storage account is private: no public blob access, no shared keys, TLS 1.2,
  versioning on, container `tfstate` private;
- Key Vault uses RBAC, soft delete and no purge protection;
- the GitHub federated credentials cover exactly pull_request and refs/heads/master, and the
  service principal has only container- and vault-scoped data roles;
- the dev container has the azure-cli feature with a major tag and a pinned CLI version.
"""

import json
import re
from pathlib import Path

import pytest
from test_devcontainer import FEATURE_TAG_RE, SEMVER_RE, feature_tag, load_jsonc
from test_terraform import blocks, required_terraform_version, strip_comments

REPO_ROOT = Path(__file__).resolve().parents[1]
AZ_DIR = REPO_ROOT / "infra" / "azure"
PROVIDERS = {"azurerm": "hashicorp/azurerm", "azuread": "hashicorp/azuread"}
REQUIRED_TAGS = {"project", "owner", "cost-center"}
SECRET_ARG_RE = re.compile(
    r"^\s*(client_secret|client_certificate\w*|password|access_key|sas_token|"
    r"primary_access_key|connection_string)\s*=",
    re.MULTILINE | re.IGNORECASE,
)
# Secret-looking literals: storage account keys (base64, 88 chars) and Entra client secrets.
SECRET_VALUE_RE = re.compile(r"[A-Za-z0-9+/]{86}==|[A-Za-z0-9_~.-]{3}8Q~[A-Za-z0-9_~.-]{31}")
FORBIDDEN_RESOURCES = {
    "azuread_application_password",
    "azuread_service_principal_password",
    "azuread_application_certificate",
    "azuread_service_principal_certificate",
    "azurerm_key_vault_secret",
    "azurerm_key_vault_key",
    "azurerm_key_vault_certificate",
    "azurerm_storage_account_sas",
}
GITHUB_REPO = "tutul032/energy-lakehouse-de"


def tf_files() -> list[Path]:
    return sorted(AZ_DIR.glob("*.tf"))


def tf_text() -> str:
    return "\n".join(p.read_text("utf-8") for p in tf_files())


def all_blocks() -> list[tuple[str, list[str], str]]:
    return [b for path in tf_files() for b in blocks(path.read_text("utf-8"))]


def resource(resource_type: str, name: str | None = None) -> str:
    """Body of the single resource of this type (and name)."""
    found = [
        body
        for kind, labels, body in all_blocks()
        if kind == "resource" and labels[0] == resource_type and name in (None, labels[1])
    ]
    assert len(found) == 1, f"expected one {resource_type} {name or ''}, found {len(found)}"
    return found[0]


def locals_text() -> str:
    return "\n".join(body for kind, _, body in all_blocks() if kind == "locals")


def attr(body: str, name: str) -> str | None:
    """Raw value of a top-level-looking `name = value` line in a block body."""
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", body, re.MULTILINE)
    return match.group(1) if match else None


def test_attr():
    body = 'a = "x"\n  nested {\n    b = true\n  }\n'
    assert attr(body, "a") == '"x"'
    assert attr(body, "b") == "true"
    assert attr(body, "c") is None


def test_root_has_files():
    names = {p.name for p in tf_files()}
    assert {"versions.tf", "providers.tf", "variables.tf", "outputs.tf"} <= names
    assert (AZ_DIR / "terraform.tfvars.example").is_file()
    assert (AZ_DIR / "README.md").is_file()


@pytest.mark.parametrize(("name", "source"), sorted(PROVIDERS.items()))
def test_provider_pinned_and_locked(name, source):
    match = re.search(
        rf'{name}\s*=\s*\{{[^}}]*source\s*=\s*"{source}"[^}}]*version\s*=\s*"([^"]+)"', tf_text()
    )
    assert match and SEMVER_RE.match(match.group(1)), f"{name}: version must be exact X.Y.Z"
    lock = (AZ_DIR / ".terraform.lock.hcl").read_text("utf-8")
    assert re.search(
        rf'"registry.terraform.io/{source}"\s*\{{\s*version\s*=\s*"{match.group(1)}"', lock
    ), f"infra/azure/.terraform.lock.hcl does not lock {source} {match.group(1)}"


def test_lock_has_linux_hashes():
    """CI and the dev container run on Linux: a Windows-only lock fails `-lockfile=readonly`."""
    lock = (AZ_DIR / ".terraform.lock.hcl").read_text("utf-8")
    for source in PROVIDERS.values():
        block = lock.split(f'"registry.terraform.io/{source}"', 1)[1].split("\n}", 1)[0]
        assert len(re.findall(r'"h1:', block)) >= 2, f"{source}: add -platform=linux_amd64 hashes"


def test_terraform_version_matches_databricks_root():
    match = re.search(r'required_version\s*=\s*"~>\s*(\d+\.\d+\.\d+)"', tf_text())
    assert match, 'versions.tf needs required_version = "~> X.Y.Z"'
    assert match.group(1) == required_terraform_version()


def test_local_state_only():
    """Bootstrap root: no backend block (it creates the state account itself)."""
    assert not re.search(r"^\s*(backend|cloud)\s", strip_comments(tf_text()), re.MULTILINE)


def test_no_secret_resources():
    types = {labels[0] for kind, labels, _ in all_blocks() if kind in ("resource", "data")}
    assert not types & FORBIDDEN_RESOURCES, sorted(types & FORBIDDEN_RESOURCES)


def test_no_secrets_or_credentials():
    text = strip_comments(tf_text())
    assert not SECRET_ARG_RE.search(text), SECRET_ARG_RE.search(text).group(0)
    for path in [*tf_files(), AZ_DIR / "terraform.tfvars.example"]:
        assert not SECRET_VALUE_RE.search(path.read_text("utf-8")), path.name
    for kind, labels, body in all_blocks():
        if kind == "provider":
            assert not re.search(
                r"^\s*(client_id|client_secret|client_certificate\w*|use_msi|oidc_token\w*)\s*=",
                body,
                re.MULTILINE,
            ), f"provider {labels}: auth must come from the environment (az login / OIDC)"


def test_no_secret_outputs():
    for kind, labels, body in all_blocks():
        if kind == "output":
            assert not re.search(r"sensitive\s*=\s*true", body), f"output {labels}: no secrets"
            assert not re.search(r"secret|password|key\b|access_key", body, re.IGNORECASE), labels


def test_example_tfvars_has_no_real_values():
    example = (AZ_DIR / "terraform.tfvars.example").read_text("utf-8")
    assert '"00000000-0000-0000-0000-000000000000"' in example
    assert "@example.com" in example


def test_resource_group_tagged():
    rg = resource("azurerm_resource_group")
    assert attr(rg, "name") == '"rg-energy-platform"'
    assert attr(rg, "tags") == "local.tags"
    keys = {k.strip('"') for k in re.findall(r'^\s*("?[\w-]+"?)\s*=', locals_text(), re.M)}
    assert REQUIRED_TAGS <= keys, sorted(REQUIRED_TAGS - keys)
    assert re.search(r'project\s*=\s*"energy-lakehouse-de"', locals_text())
    assert re.search(r'"cost-center"\s*=\s*"portfolio"', locals_text())


@pytest.mark.parametrize("resource_type", ["azurerm_storage_account", "azurerm_key_vault"])
def test_resources_tagged(resource_type):
    assert attr(resource(resource_type), "tags") == "local.tags"


def test_storage_account_not_public():
    sa = resource("azurerm_storage_account")
    expected = {
        "account_tier": '"Standard"',
        "account_replication_type": '"LRS"',
        "min_tls_version": '"TLS1_2"',
        "https_traffic_only_enabled": "true",
        "allow_nested_items_to_be_public": "false",
        "shared_access_key_enabled": "false",
        "versioning_enabled": "true",
    }
    actual = {key: attr(sa, key) for key in expected}
    assert actual == expected
    container = resource("azurerm_storage_container")
    assert attr(container, "name") == '"tfstate"'
    assert attr(container, "container_access_type") == '"private"'


def test_key_vault_rbac_soft_delete_no_purge_protection():
    kv = resource("azurerm_key_vault")
    assert attr(kv, "rbac_authorization_enabled") == "true"
    assert attr(kv, "purge_protection_enabled") == "false"
    assert 7 <= int(attr(kv, "soft_delete_retention_days")) <= 90
    assert "access_policy" not in kv, "RBAC vault: no access policies"


def test_github_federated_subjects():
    fic = resource("azuread_application_federated_identity_credential")
    assert attr(fic, "issuer") == '"https://token.actions.githubusercontent.com"'
    assert attr(fic, "audiences") == '["api://AzureADTokenExchange"]'
    subjects = set(re.findall(r'=\s*"(repo:[^"]+)"', locals_text()))
    assert subjects == {
        "repo:${var.github_repository}:pull_request",
        "repo:${var.github_repository}:ref:refs/heads/${var.github_branch}",
    }
    variables = {labels[0]: body for kind, labels, body in all_blocks() if kind == "variable"}
    assert attr(variables["github_repository"], "default") == f'"{GITHUB_REPO}"'
    assert attr(variables["github_branch"], "default") == '"master"'
    app = resource("azuread_application")
    assert attr(app, "display_name") == '"gh-energy-lakehouse"'


def test_github_sp_roles_are_scoped():
    roles = {}
    for kind, labels, body in all_blocks():
        if kind == "resource" and labels[0] == "azurerm_role_assignment":
            if "azuread_service_principal.github" in body:
                roles[attr(body, "role_definition_name")] = attr(body, "scope")
    assert roles == {
        '"Storage Blob Data Contributor"': "azurerm_storage_container.tfstate.id",
        '"Key Vault Secrets User"': "azurerm_key_vault.platform.id",
    }


def test_budget_alerts():
    budget = resource("azurerm_consumption_budget_subscription")
    assert re.search(r"toset\(\[50,\s*80,\s*100\]\)", budget)
    assert attr(budget, "contact_emails") == "[var.alert_email]"


def test_devcontainer_has_azure_cli():
    config = load_jsonc((REPO_ROOT / ".devcontainer/devcontainer.json").read_text("utf-8"))
    (ref,) = [r for r in config["features"] if "/features/azure-cli" in r]
    assert FEATURE_TAG_RE.fullmatch(feature_tag(ref) or "") and "." not in feature_tag(ref)
    assert SEMVER_RE.match(config["features"][ref]["version"])
    lock = json.loads((REPO_ROOT / ".devcontainer/devcontainer-lock.json").read_text("utf-8"))
    assert ref in lock["features"], "devcontainer-lock.json is missing the azure-cli feature"
    readme = (REPO_ROOT / ".devcontainer/README.md").read_text("utf-8")
    assert "az login --use-device-code" in readme


def test_gitignore_keeps_state_and_tfvars_out():
    lines = set((REPO_ROOT / ".gitignore").read_text("utf-8").splitlines())
    assert {".terraform/", "*.tfstate", "*.tfstate.*", "*.tfvars", "!*.tfvars.example"} <= lines
