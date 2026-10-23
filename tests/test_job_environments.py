"""Job environment guard: notebook tasks that import the ML modules need their libraries.

Parses resources/*.yml; for every notebook task whose notebook source imports
energy_lakehouse.forecast or energy_lakehouse.anomalies (regex), the task's `environment_key`
must point to a job environment whose dependencies list holidays, scikit-learn and mlflow.
Those packages must also be declared in pyproject.toml (dependencies or dependency groups). No
job environment may install a library the serverless runtime provides (conflicts break Spark
initialization in notebooks), and no notebook may install packages itself (`%pip`,
`restartPython`): job environments are the only mechanism.
"""

import re
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_DIR = REPO_ROOT / "resources"
NOTEBOOK_DIR = REPO_ROOT / "src" / "notebooks"
NOTEBOOK_INSTALL_MARKERS = ("%pip", "restartPython")
REQUIRED_PACKAGES = {"holidays", "scikit-learn", "mlflow"}
FORBIDDEN_PACKAGES = {
    "pyspark",
    "databricks-connect",
    "delta-spark",
    "databricks-sdk",
    "numpy",
    "pandas",
    "pyarrow",
}

FORECAST_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+energy_lakehouse\s+import\s+[^\n]*\b(?:forecast|anomalies)\b"
    r"|from\s+energy_lakehouse\.(?:forecast|anomalies)\s+import\b"
    r"|import\s+energy_lakehouse\.(?:forecast|anomalies)\b)",
    re.MULTILINE,
)
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def package_name(requirement: str) -> str:
    """Return the normalized package name of a requirement, e.g. `mlflow==3.1` -> `mlflow`."""
    match = REQUIREMENT_NAME_RE.match(requirement)
    assert match, f"unparseable requirement: {requirement!r}"
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def imports_forecast(source: str) -> bool:
    """True if notebook source imports energy_lakehouse.forecast or energy_lakehouse.anomalies."""
    return FORECAST_IMPORT_RE.search(source) is not None


def forecast_notebook_tasks() -> list[tuple[str, str, dict, dict]]:
    """Return (resource file, task_key, task, environments by key) for forecast notebook tasks."""
    found = []
    for path in sorted(RESOURCE_DIR.glob("*.yml")):
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job in (config.get("resources", {}).get("jobs") or {}).values():
            envs = {e["environment_key"]: e for e in job.get("environments") or []}
            for task in job.get("tasks") or []:
                notebook = task.get("notebook_task")
                if not notebook:
                    continue
                source = (path.parent / notebook["notebook_path"]).resolve()
                if imports_forecast(source.read_text(encoding="utf-8")):
                    found.append((path.name, task["task_key"], task, envs))
    return found


def job_environments() -> list[tuple[str, str, list[str]]]:
    """Return (resource file, environment_key, dependencies) for every job environment."""
    found = []
    for path in sorted(RESOURCE_DIR.glob("*.yml")):
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job in (config.get("resources", {}).get("jobs") or {}).values():
            for env in job.get("environments") or []:
                deps = env.get("spec", {}).get("dependencies") or []
                found.append((path.name, env["environment_key"], deps))
    return found


def pyproject_packages() -> set[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = list(data["project"].get("dependencies", []))
    for group in data.get("dependency-groups", {}).values():
        requirements.extend(r for r in group if isinstance(r, str))
    return {package_name(r) for r in requirements}


def test_package_name():
    assert package_name("scikit-learn==1.9.1") == "scikit-learn"
    assert package_name("PyYAML>=6") == "pyyaml"
    assert package_name("holidays") == "holidays"


def test_imports_forecast():
    assert imports_forecast("from energy_lakehouse import forecast\n")
    assert imports_forecast("from energy_lakehouse import contracts, forecast\n")
    assert imports_forecast("from energy_lakehouse.forecast import build_features\n")
    assert imports_forecast("import energy_lakehouse.forecast as fc\n")
    assert not imports_forecast("from energy_lakehouse import contracts\n")
    assert not imports_forecast("from energy_lakehouse.forecasting import x\n")
    assert imports_forecast("from energy_lakehouse import anomalies, forecast\n")
    assert imports_forecast("from energy_lakehouse.anomalies import completeness\n")
    assert not imports_forecast("from energy_lakehouse.anomaly import x\n")


def test_forecast_notebook_tasks_found():
    task_keys = {key for _, key, _, _ in forecast_notebook_tasks()}
    assert {
        "score_forecast",
        "forecast_accuracy",
        "train_load",
        "train_price",
        "detect_anomalies",
        "dq_summary_llm",
    } <= task_keys


def test_forecast_tasks_have_environment_with_required_packages():
    problems = []
    for file, key, task, envs in forecast_notebook_tasks():
        env_key = task.get("environment_key")
        if env_key not in envs:
            problems.append(f"{file}: task {key}: missing environment (environment_key={env_key})")
            continue
        deps = {package_name(d) for d in envs[env_key].get("spec", {}).get("dependencies") or []}
        missing = REQUIRED_PACKAGES - deps
        if missing:
            problems.append(f"{file}: task {key}: environment {env_key} lacks {sorted(missing)}")
    assert not problems, "\n".join(problems)


def test_job_environments_skip_runtime_provided_packages():
    problems = []
    for file, env_key, deps in job_environments():
        forbidden = FORBIDDEN_PACKAGES & {package_name(d) for d in deps}
        if forbidden:
            problems.append(f"{file}: environment {env_key} installs {sorted(forbidden)}")
    assert not problems, "\n".join(problems)


def test_notebooks_do_not_install_packages():
    problems = []
    for path in sorted(NOTEBOOK_DIR.glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for marker in NOTEBOOK_INSTALL_MARKERS:
                if marker in line:
                    problems.append(f"{path.name}: line {i}: {marker}")
    assert not problems, "\n".join(problems)


def task_environment_specs() -> dict[str, dict]:
    """Return {task_key: environment spec} for every job task that has an environment_key."""
    specs = {}
    for path in sorted(RESOURCE_DIR.glob("*.yml")):
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job in (config.get("resources", {}).get("jobs") or {}).values():
            envs = {e["environment_key"]: e.get("spec", {}) for e in job.get("environments") or []}
            for task in job.get("tasks") or []:
                if task.get("environment_key") in envs:
                    specs[task["task_key"]] = envs[task["environment_key"]]
    return specs


def test_training_and_scoring_share_environment():
    # The champion model is pickled at training; scoring must unpickle it with the same libraries.
    specs = task_environment_specs()
    tasks = ("train_load", "train_price", "score_forecast")
    missing = [key for key in tasks if key not in specs]
    assert not missing, f"tasks without environment: {missing}"
    for key in tasks[1:]:
        assert specs[key] == specs[tasks[0]], f"{key} environment differs from {tasks[0]}"


def test_required_packages_in_pyproject():
    missing = REQUIRED_PACKAGES - pyproject_packages()
    assert not missing, f"pyproject.toml lacks {sorted(missing)}"
