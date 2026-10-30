"""Dev container guard: .devcontainer/ is pinned, non-root and carries no secrets.

Static checks (no Docker needed); the ci.yml `devcontainer` job builds it and runs the tests
inside. Checks:
- the base image has a version tag (major `1-` or exact `x.y.z-`, never latest) whose Python
  minor equals .python-version and the CI python-version;
- every feature reference has a major or exact semver tag (local `./features/...` excepted), tool
  versions in feature options are pinned (never latest), and the local Databricks CLI feature
  matches the CLI version CI installs (databricks/setup-cli@vX.Y.Z) when CI pins one;
- postCreateCommand installs a pinned uv (same as the extractor Dockerfile), then `uv sync` and
  `uv run pre-commit install`;
- remoteUser is set and not root; no mounts of credential files; no env/option names or values
  that look like tokens/secrets.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVCONTAINER_DIR = REPO_ROOT / ".devcontainer"
CONFIG = DEVCONTAINER_DIR / "devcontainer.json"
POST_CREATE = DEVCONTAINER_DIR / "post-create.sh"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
FEATURE_TAG_RE = re.compile(r"^\d+(\.\d+\.\d+)?$")
IMAGE_RE = re.compile(
    r"^mcr\.microsoft\.com/devcontainers/python:\d+(?:\.\d+\.\d+)?-(\d+\.\d+)-\w+$"
)
SECRET_RE = re.compile(r"TOKEN|SECRET|PASSWORD|CLIENT_ID|API_KEY", re.IGNORECASE)
CREDENTIAL_PATHS = (".databrickscfg", ".databricks", ".ssh", ".git-credentials", ".config/gh")
REQUIRED_EXTENSIONS = {
    "ms-python.python",
    "charliermarsh.ruff",
    "databricks.databricks",
    "redhat.vscode-yaml",
    "github.vscode-github-actions",
}


def load_jsonc(text: str) -> dict:
    """Parse devcontainer.json: JSON plus whole-line `//` comments."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("//")]
    return json.loads("\n".join(lines))


def feature_tag(reference: str) -> str | None:
    """Tag of an OCI feature reference (`ghcr.io/a/b:1.2.3` -> `1.2.3`), None if untagged."""
    name = reference.rsplit("/", 1)[-1]
    return name.split(":", 1)[1] if ":" in name else None


def walk_strings(value, path: str = ""):
    """Yield (path, key-or-value string) for every key and string value of nested JSON."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield f"{path}/{key}", key
            yield from walk_strings(item, f"{path}/{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def ci_python_versions() -> set[str]:
    versions = set()
    for path in WORKFLOW_DIR.glob("*.yml"):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (workflow.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                version = (step.get("with") or {}).get("python-version")
                if version:
                    versions.add(str(version))
    return versions


def ci_databricks_cli_versions() -> set[str]:
    text = "\n".join(p.read_text(encoding="utf-8") for p in WORKFLOW_DIR.glob("*.yml"))
    return set(re.findall(r"databricks/setup-cli@v(\d+\.\d+\.\d+)\b", text))


@pytest.fixture(scope="module")
def config() -> dict:
    return load_jsonc(CONFIG.read_text(encoding="utf-8"))


def test_load_jsonc_strips_line_comments():
    assert load_jsonc('{\n  // comment\n  "a": "http://x"\n}') == {"a": "http://x"}


@pytest.mark.parametrize(
    ("reference", "tag"),
    [
        ("ghcr.io/devcontainers/features/github-cli:1.1.3", "1.1.3"),
        ("ghcr.io/devcontainers/features/github-cli", None),
        ("localhost:5000/features/x:1", "1"),
    ],
)
def test_feature_tag(reference, tag):
    assert feature_tag(reference) == tag


@pytest.mark.parametrize(
    ("image", "ok"),
    [
        ("mcr.microsoft.com/devcontainers/python:1-3.12-bookworm", True),
        ("mcr.microsoft.com/devcontainers/python:3.3.2-3.12-trixie", True),
        ("mcr.microsoft.com/devcontainers/python:latest", False),
        ("mcr.microsoft.com/devcontainers/python:3.12", False),
    ],
)
def test_image_re(image, ok):
    assert bool(IMAGE_RE.match(image)) is ok


@pytest.mark.parametrize(
    ("tag", "ok"), [("1", True), ("1.1.3", True), ("latest", False), ("1.1", False)]
)
def test_feature_tag_re(tag, ok):
    assert bool(FEATURE_TAG_RE.match(tag)) is ok


def test_walk_strings():
    found = list(walk_strings({"a": {"b": ["c"]}}))
    assert found == [("/a", "a"), ("/a/b", "b"), ("/a/b[0]", "c")]


def test_image_pinned_with_project_python_minor(config):
    match = IMAGE_RE.match(config.get("image", ""))
    assert match, f"image not a versioned devcontainers/python tag: {config.get('image')}"
    python_minor = match.group(1)
    assert python_minor == (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    ci = ci_python_versions()
    assert ci == {python_minor}, f"CI python-version {sorted(ci)} != image Python {python_minor}"


def test_features_pinned(config):
    problems = []
    for reference, options in config.get("features", {}).items():
        if reference.startswith("./"):
            assert (DEVCONTAINER_DIR / reference / "install.sh").is_file(), reference
        elif not FEATURE_TAG_RE.match(feature_tag(reference) or ""):
            problems.append(f"{reference}: not a major or exact x.y.z tag")
        version = (options or {}).get("version")
        if version is not None and not SEMVER_RE.match(str(version)):
            problems.append(f"{reference}: tool version {version!r} not pinned")
    assert not problems, "\n".join(problems)


def test_local_databricks_feature_matches_ci(config):
    feature = json.loads(
        (DEVCONTAINER_DIR / "features/databricks-cli/devcontainer-feature.json").read_text(
            encoding="utf-8"
        )
    )
    default = feature["options"]["version"]["default"]
    configured = config["features"]["./features/databricks-cli"]["version"]
    assert SEMVER_RE.match(default) and configured == default
    for ci_version in ci_databricks_cli_versions():
        assert configured == ci_version, f"dev container CLI {configured} != CI {ci_version}"


def test_post_create_pins_uv_and_sets_up_project(config):
    assert config["postCreateCommand"] == "bash .devcontainer/post-create.sh"
    script = POST_CREATE.read_text(encoding="utf-8")
    match = re.search(r'^UV_VERSION="(\d+\.\d+\.\d+)"', script, re.MULTILINE)
    assert match, "post-create.sh does not pin UV_VERSION"
    assert 'pipx install "uv==${UV_VERSION}"' in script
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"ghcr.io/astral-sh/uv:{match.group(1)}" in dockerfile, "uv differs from Dockerfile"
    order = [script.index(s) for s in ("pipx install", "uv sync", "uv run pre-commit install")]
    assert order == sorted(order)


def test_shell_scripts_use_lf():
    for path in (POST_CREATE, DEVCONTAINER_DIR / "features/databricks-cli/install.sh"):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF line endings"
    assert "*.sh text eol=lf" in (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_remote_user_not_root(config):
    user = config.get("remoteUser")
    assert user and user not in {"root", "0"}, f"remoteUser {user!r}"
    assert config.get("containerUser") in {None, user}


def test_no_secret_env_or_mounts(config):
    problems = [
        f"{path}: {text}"
        for path, text in walk_strings(config)
        if SECRET_RE.search(text) and "/customizations/" not in path
    ]
    for mount in config.get("mounts") or []:
        source = mount if isinstance(mount, str) else json.dumps(mount)
        if any(p in source for p in CREDENTIAL_PATHS):
            problems.append(f"mount of credentials: {source}")
    assert not problems, "\n".join(problems)


def test_required_extensions(config):
    extensions = set(config["customizations"]["vscode"]["extensions"])
    assert REQUIRED_EXTENSIONS <= extensions, sorted(REQUIRED_EXTENSIONS - extensions)


def test_ci_builds_devcontainer_and_runs_tests():
    workflow = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["devcontainer"]["steps"]
    (step,) = [s for s in steps if s.get("uses", "").startswith("devcontainers/ci@")]
    assert re.fullmatch(r"devcontainers/ci@v\d+\.\d+\.\d+", step["uses"]), step["uses"]
    assert step["with"]["push"] == "never"
    assert "uv run pytest" in step["with"]["runCmd"]
