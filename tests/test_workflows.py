"""Workflow guard: extractor image release (deploy-prod.yml) and use (daily-refresh.yml).

Parses .github/workflows/*.yml and checks:
- deploy-prod runs publish-image -> deploy (production approval) -> promote-image, publishes
  :<tag> and :sha-<short sha> after a Trivy scan, and promotes by digest (imagetools, no build);
- `packages: write` appears only on publish-image and promote-image, never workflow-wide;
- no `uses:` reference (job or step) points at @main or @master, no checkout@v4 /
  upload-artifact@v4 (Node 20), no `ubuntu-latest` runner;
- daily-refresh has contents/packages read only, extracts with the :prod image via docker run
  (runner user, /data mount, same extractor arguments) instead of bare Python, and never hands
  DATABRICKS_* variables or env files to the container.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
PACKAGE_WRITERS = {("deploy-prod.yml", "publish-image"), ("deploy-prod.yml", "promote-image")}
DOCKER_RUN_RE = re.compile(r"\bdocker\s+run\b")
CONTAINER_ENV_FLAG_RE = re.compile(r"(?:^|\s)(?:-e|--env|--env-file)(?:[\s=]|$)")
BRANCH_REFS = ("@main", "@master")
OUTDATED_ACTION_RE = re.compile(r"^actions/(?:checkout|upload-artifact)@v4(?:\.|$)")


def load_workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))


def workflow_files() -> list[str]:
    return sorted(p.name for p in WORKFLOW_DIR.glob("*.yml"))


def needs(job: dict) -> list[str]:
    """`needs` of a job as a list (a single string is allowed in workflow YAML)."""
    value = job.get("needs") or []
    return [value] if isinstance(value, str) else list(value)


def steps_running(job: dict, pattern: re.Pattern) -> list[dict]:
    """Steps whose `run` script matches pattern."""
    return [s for s in job.get("steps") or [] if pattern.search(s.get("run") or "")]


def docker_run_commands(script: str) -> list[str]:
    """Each `docker run ...` command of a shell script, `\\` continuations joined."""
    joined = re.sub(r"\\\n", " ", script)
    return [line for line in joined.splitlines() if DOCKER_RUN_RE.search(line)]


def passes_env_to_container(command: str) -> bool:
    """True if a docker run command passes environment variables (-e, --env, --env-file)."""
    return CONTAINER_ENV_FLAG_RE.search(command) is not None


def job_uses_references(job: dict) -> list[str]:
    """`uses:` of a job (reusable workflow) and of its steps."""
    refs = [job.get("uses")] + [s.get("uses") for s in job.get("steps") or []]
    return [r for r in refs if r]


def write_permissions(permissions) -> set[str]:
    """Scopes with write access (`write-all` counts as packages)."""
    if permissions == "write-all":
        return {"packages"}
    if not isinstance(permissions, dict):
        return set()
    return {scope for scope, level in permissions.items() if level == "write"}


@pytest.fixture(scope="module")
def deploy_prod() -> dict:
    return load_workflow("deploy-prod.yml")


@pytest.fixture(scope="module")
def daily_refresh() -> dict:
    return load_workflow("daily-refresh.yml")


def test_needs():
    assert needs({"needs": "a"}) == ["a"]
    assert needs({"needs": ["a", "b"]}) == ["a", "b"]
    assert needs({}) == []


def test_docker_run_commands_join_continuations():
    script = 'echo hi\ndocker run --rm \\\n  -v "x:/data" img --out /data\ndocker pull img\n'
    assert docker_run_commands(script) == ['docker run --rm    -v "x:/data" img --out /data']


@pytest.mark.parametrize(
    ("command", "passes"),
    [
        ("docker run --rm -e DATABRICKS_HOST img", True),
        ("docker run --rm --env=X=1 img", True),
        ("docker run --rm --env-file .env img", True),
        ('docker run --rm --user "$(id -u):$(id -g)" -v "${LOCAL_DIR}:/data" img', False),
        ("docker run --rm img --source all --past-days 7", False),
    ],
)
def test_passes_env_to_container(command, passes):
    assert passes_env_to_container(command) is passes


def test_write_permissions():
    assert write_permissions({"contents": "read", "packages": "write"}) == {"packages"}
    assert write_permissions("write-all") == {"packages"}
    assert write_permissions("read-all") == set()
    assert write_permissions(None) == set()


def test_deploy_prod_job_order(deploy_prod):
    jobs = deploy_prod["jobs"]
    assert {"publish-image", "deploy", "promote-image"} <= set(jobs)
    assert needs(jobs["publish-image"]) == []
    assert "publish-image" in needs(jobs["deploy"])
    assert {"publish-image", "deploy"} <= set(needs(jobs["promote-image"]))
    # The approval gate is on deploy; the image is built and scanned before anyone approves.
    assert jobs["deploy"].get("environment") == "production"
    assert "environment" not in jobs["publish-image"]


def test_deploy_prod_keeps_dispatch_inputs(deploy_prod):
    # PyYAML reads the `on` key as True.
    triggers = deploy_prod.get("on") or deploy_prod.get(True)
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"tag", "force"}
    publish = deploy_prod["jobs"]["publish-image"]
    assert "inputs.tag" in publish["env"]["TAG"]
    checkout = next(s for s in publish["steps"] if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "refs/tags/${{ env.TAG }}"


def test_publish_image_scans_then_pushes_both_tags(deploy_prod):
    steps = deploy_prod["jobs"]["publish-image"]["steps"]
    scripts = [s.get("run") or "" for s in steps]
    scan = next(i for i, r in enumerate(scripts) if "trivy" in r.lower() or "TRIVY" in r)
    push = next(i for i, r in enumerate(scripts) if "docker push" in r)
    assert scan < push, "Trivy must run before the push"
    assert all(flag in scripts[scan] for flag in ("--severity CRITICAL", "--ignore-unfixed"))
    assert "--exit-code 1" in scripts[scan]
    assert '"${IMAGE}:${TAG}"' in scripts[push] and '"${IMAGE}:sha-${SHORT_SHA}"' in scripts[push]
    build = next(r for r in scripts if re.search(r"\bdocker build\s", r))
    assert 'SOURCE="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}"' in build


def test_promote_image_retags_digest_without_rebuild(deploy_prod):
    job = deploy_prod["jobs"]["promote-image"]
    script = "\n".join(s.get("run") or "" for s in job["steps"])
    assert "docker buildx imagetools create" in script
    assert '"${IMAGE}:prod" "${IMAGE}@${DIGEST}"' in script
    assert not re.search(r"\bdocker build\s", script) and "docker push" not in script
    assert job["env"]["DIGEST"] == "${{ needs.publish-image.outputs.digest }}"


def test_packages_write_only_on_publish_and_promote():
    problems = []
    for name in workflow_files():
        workflow = load_workflow(name)
        if "packages" in write_permissions(workflow.get("permissions")):
            problems.append(f"{name}: workflow-level packages: write")
        for key, job in (workflow.get("jobs") or {}).items():
            has_write = "packages" in write_permissions(job.get("permissions"))
            if has_write != ((name, key) in PACKAGE_WRITERS):
                problems.append(f"{name}: job {key}: packages write = {has_write}")
    assert not problems, "\n".join(problems)


def test_job_uses_references():
    job = {"uses": "./ci.yml", "steps": [{"uses": "a/b@v1"}, {"run": "echo"}]}
    assert job_uses_references(job) == ["./ci.yml", "a/b@v1"]
    assert job_uses_references({}) == []


def test_no_uses_reference_on_main_or_master():
    problems = []
    for name in workflow_files():
        for key, job in (load_workflow(name).get("jobs") or {}).items():
            refs = job_uses_references(job)
            problems += [f"{name}: job {key}: {r}" for r in refs if r.endswith(BRANCH_REFS)]
    assert not problems, "\n".join(problems)


def test_no_node20_artifact_or_checkout_actions():
    problems = []
    for name in workflow_files():
        for key, job in (load_workflow(name).get("jobs") or {}).items():
            refs = job_uses_references(job)
            problems += [f"{name}: job {key}: {r}" for r in refs if OUTDATED_ACTION_RE.match(r)]
    assert not problems, "\n".join(problems)


def test_no_ubuntu_latest_runner():
    problems = []
    for name in workflow_files():
        for key, job in (load_workflow(name).get("jobs") or {}).items():
            if "ubuntu-latest" in str(job.get("runs-on", "")):
                problems.append(f"{name}: job {key}: runs-on {job['runs-on']}")
    assert not problems, "\n".join(problems)


def test_daily_refresh_permissions(daily_refresh):
    assert daily_refresh["permissions"] == {"contents": "read", "packages": "read"}
    for key, job in daily_refresh["jobs"].items():
        assert "permissions" not in job, f"job {key} overrides permissions"


def test_daily_refresh_extracts_with_prod_image(daily_refresh):
    text = (WORKFLOW_DIR / "daily-refresh.yml").read_text(encoding="utf-8")
    assert "energy-extractor:prod" in text
    assert "extract_local" not in text and "uv sync" not in text and "setup-uv" not in text
    job = daily_refresh["jobs"]["refresh"]
    (step,) = steps_running(job, DOCKER_RUN_RE)
    (command,) = docker_run_commands(step["run"])
    assert '--user "$(id -u):$(id -g)"' in command
    assert '-v "${LOCAL_DIR}:/data"' in command
    assert '"${EXTRACTOR_REF}"' in command
    for argument in ("--source all", "--out /data", "--weeks", "--past-days"):
        assert argument in command, f"docker run lacks {argument}"
    # EXTRACTOR_REF is the digest pulled (and logged with its version label) beforehand.
    pull = next(s for s in job["steps"] if "docker pull" in (s.get("run") or ""))
    assert "RepoDigests" in pull["run"] and "org.opencontainers.image.version" in pull["run"]
    assert "EXTRACTOR_REF=" in pull["run"]
    upload = next(s for s in job["steps"] if "sync_landing.sh" in (s.get("run") or ""))
    assert upload["env"]["SKIP_EXTRACT"] == "1"


def test_daily_refresh_gives_container_no_databricks_env(daily_refresh):
    job = daily_refresh["jobs"]["refresh"]
    # Job-level env reaches every step; Databricks variables belong only to the steps using them.
    leaked = [k for k in job.get("env") or {} if k.startswith("DATABRICKS")]
    assert not leaked, f"job-level env {leaked}"
    for step in steps_running(job, DOCKER_RUN_RE):
        step_env = [k for k in step.get("env") or {} if k.startswith("DATABRICKS")]
        assert not step_env, f"docker run step has env {step_env}"
        for command in docker_run_commands(step["run"]):
            assert not passes_env_to_container(command), command
            assert "DATABRICKS" not in command, command
            assert "secrets." not in command, command
