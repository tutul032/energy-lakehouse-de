"""Job environment guard: notebooks and the pipeline get energy_lakehouse from the bundle wheel.

databricks.yml builds the package as a wheel (`artifacts`, `uv build --wheel`). Parses
resources/*.yml and checks:
- every notebook task whose notebook imports energy_lakehouse (regex) has an `environment_key`
  whose dependencies install that wheel (`../dist/*.whl`, relative to the resource file), and
  tasks importing energy_lakehouse.forecast / .anomalies also pin holidays, scikit-learn and
  mlflow exactly (the wheel only declares ranges; models are pickled at training and unpickled
  at scoring with the same versions), equal to uv.lock;
- the pipeline installs the same wheel in its serverless environment;
- no notebook or pipeline file manipulates sys.path, installs packages itself (`%pip`,
  `restartPython`), and no job environment or wheel dependency is a library the serverless
  runtime provides (conflicts break Spark initialization).
"""

import re
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_DIR = REPO_ROOT / "resources"
NOTEBOOK_DIR = REPO_ROOT / "src" / "notebooks"
PIPELINE_DIR = REPO_ROOT / "src" / "pipelines"
WHEEL_GLOB = REPO_ROOT / "dist" / "*.whl"
NOTEBOOK_INSTALL_MARKERS = ("%pip", "restartPython")
SYS_PATH_MARKERS = ("sys.path", "os.path.abspath(", "bundle.sourcePath")
REQUIRED_PACKAGES = {"holidays", "scikit-learn", "mlflow"}
WHEEL_RUNTIME_PACKAGES = REQUIRED_PACKAGES | {"requests", "pydantic", "pyyaml"}
FORBIDDEN_PACKAGES = {
    "pyspark",
    "databricks-connect",
    "delta-spark",
    "databricks-sdk",
    "numpy",
    "pandas",
    "pyarrow",
}

PACKAGE_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+energy_lakehouse(?:\.\w+)*\s+import\b|import\s+energy_lakehouse\b)",
    re.MULTILINE,
)
FORECAST_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+energy_lakehouse\s+import\s+[^\n]*\b(?:forecast|anomalies)\b"
    r"|from\s+energy_lakehouse\.(?:forecast|anomalies)\s+import\b"
    r"|import\s+energy_lakehouse\.(?:forecast|anomalies)\b)",
    re.MULTILINE,
)
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
PIN_RE = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*==\s*([^\s;]+)\s*$")


def package_name(requirement: str) -> str:
    """Return the normalized package name of a requirement, e.g. `mlflow==3.1` -> `mlflow`."""
    match = REQUIREMENT_NAME_RE.match(requirement)
    assert match, f"unparseable requirement: {requirement!r}"
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def is_wheel(dependency: str, resource_file: Path) -> bool:
    """True if a dependency is the bundle wheel (`../dist/*.whl` relative to the resource file)."""
    return (resource_file.parent / dependency).resolve() == WHEEL_GLOB.resolve()


def imports_package(source: str) -> bool:
    """True if notebook source imports anything from energy_lakehouse."""
    return PACKAGE_IMPORT_RE.search(source) is not None


def imports_forecast(source: str) -> bool:
    """True if notebook source imports energy_lakehouse.forecast or energy_lakehouse.anomalies."""
    return FORECAST_IMPORT_RE.search(source) is not None


def load_resources() -> list[tuple[Path, dict]]:
    return [
        (path, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        for path in sorted(RESOURCE_DIR.glob("*.yml"))
    ]


def notebook_tasks() -> list[tuple[Path, str, dict, dict, str]]:
    """Return (resource file, task_key, task, environments by key, notebook source)."""
    found = []
    for path, config in load_resources():
        for job in (config.get("resources", {}).get("jobs") or {}).values():
            envs = {e["environment_key"]: e for e in job.get("environments") or []}
            for task in job.get("tasks") or []:
                notebook = task.get("notebook_task")
                if not notebook:
                    continue
                source = (path.parent / notebook["notebook_path"]).resolve()
                found.append(
                    (path, task["task_key"], task, envs, source.read_text(encoding="utf-8"))
                )
    return found


def task_dependencies(task: dict, envs: dict) -> list[str] | None:
    """Dependencies of the task's job environment, or None if it has no (known) environment."""
    env = envs.get(task.get("environment_key"))
    return None if env is None else env.get("spec", {}).get("dependencies") or []


def job_environments() -> list[tuple[str, str, list[str]]]:
    """Return (resource file, environment_key, dependencies) for every job environment."""
    found = []
    for path, config in load_resources():
        for job in (config.get("resources", {}).get("jobs") or {}).values():
            for env in job.get("environments") or []:
                deps = env.get("spec", {}).get("dependencies") or []
                found.append((path.name, env["environment_key"], deps))
    return found


def pipelines() -> list[tuple[Path, str, dict]]:
    """Return (resource file, pipeline key, pipeline config) for every pipeline."""
    return [
        (path, key, pipeline)
        for path, config in load_resources()
        for key, pipeline in (config.get("resources", {}).get("pipelines") or {}).items()
    ]


def pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def locked_versions() -> dict[str, str]:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {package_name(p["name"]): p["version"] for p in lock.get("package", [])}


def test_package_name():
    assert package_name("scikit-learn==1.9.1") == "scikit-learn"
    assert package_name("PyYAML>=6") == "pyyaml"
    assert package_name("holidays") == "holidays"


def test_is_wheel():
    resource = RESOURCE_DIR / "daily_refresh_job.yml"
    assert is_wheel("../dist/*.whl", resource)
    assert not is_wheel("dist/*.whl", resource)
    assert not is_wheel("mlflow==3.17.2", resource)


def test_imports_package():
    assert imports_package("from energy_lakehouse import contracts\n")
    assert imports_package("from energy_lakehouse.utils import table_name\n")
    assert imports_package("import energy_lakehouse.forecast as fc\n")
    assert not imports_package("from energy_lakehouse_other import x\n")
    assert not imports_package("# from energy_lakehouse import x\n")


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


def test_package_notebook_tasks_found():
    task_keys = {key for _, key, _, _, source in notebook_tasks() if imports_package(source)}
    assert {
        "hello",
        "extract_smard",
        "quality_checks",
        "apply_contracts",
        "verify_contracts",
        "score_forecast",
        "forecast_accuracy",
        "train_load",
        "train_price",
        "detect_anomalies",
        "dq_summary_llm",
    } <= task_keys


def test_package_notebook_tasks_install_wheel():
    problems = []
    for path, key, task, envs, source in notebook_tasks():
        if not imports_package(source):
            continue
        deps = task_dependencies(task, envs)
        if deps is None:
            problems.append(f"{path.name}: task {key}: no environment (imports energy_lakehouse)")
        elif not any(is_wheel(d, path) for d in deps):
            problems.append(f"{path.name}: task {key}: environment lacks ../dist/*.whl")
    assert not problems, "\n".join(problems)


def test_forecast_tasks_pin_required_packages_to_lock():
    locked = locked_versions()
    problems = []
    for path, key, task, envs, source in notebook_tasks():
        if not imports_forecast(source):
            continue
        pins = {}
        for d in task_dependencies(task, envs) or []:
            match = PIN_RE.match(d)
            if match:
                pins[package_name(d)] = match.group(1)
        for package in sorted(REQUIRED_PACKAGES):
            if package not in pins:
                problems.append(f"{path.name}: task {key}: {package} not pinned with ==")
            elif pins[package] != locked.get(package):
                problems.append(
                    f"{path.name}: task {key}: {package}=={pins[package]} but uv.lock has "
                    f"{locked.get(package)}"
                )
    assert not problems, "\n".join(problems)


def test_pipelines_install_wheel():
    found = pipelines()
    assert found, "no pipelines in resources/"
    for path, key, pipeline in found:
        deps = (pipeline.get("environment") or {}).get("dependencies") or []
        assert any(is_wheel(d, path) for d in deps), f"{path.name}: {key} lacks ../dist/*.whl"
        # root_path is on sys.path: ../src would shadow the wheel with the synced source files.
        root = pipeline.get("root_path")
        if root is not None:
            assert not (path.parent / root / "energy_lakehouse").exists(), (
                f"{path.name}: {key}: root_path {root} contains energy_lakehouse"
            )


def test_bundle_builds_wheel():
    config = yaml.safe_load((REPO_ROOT / "databricks.yml").read_text(encoding="utf-8"))
    wheels = [a for a in (config.get("artifacts") or {}).values() if a.get("type") == "whl"]
    assert len(wheels) == 1, "databricks.yml needs exactly one whl artifact"
    artifact = wheels[0]
    assert artifact.get("path") == "."
    assert artifact.get("build", "").startswith("uv build --wheel")
    assert [f["source"] for f in artifact.get("files") or []] == ["dist/*.whl"]


def test_job_environments_skip_runtime_provided_packages():
    problems = []
    for file, env_key, deps in job_environments():
        forbidden = FORBIDDEN_PACKAGES & {package_name(d) for d in deps if not d.endswith(".whl")}
        if forbidden:
            problems.append(f"{file}: environment {env_key} installs {sorted(forbidden)}")
    assert not problems, "\n".join(problems)


def test_wheel_dependencies():
    data = pyproject()
    assert data["build-system"]["build-backend"]
    runtime = {package_name(r) for r in data["project"].get("dependencies", [])}
    assert WHEEL_RUNTIME_PACKAGES <= runtime, f"missing {sorted(WHEEL_RUNTIME_PACKAGES - runtime)}"
    forbidden = FORBIDDEN_PACKAGES & runtime
    assert not forbidden, f"runtime-provided packages in [project] dependencies: {forbidden}"


def test_notebooks_do_not_install_packages():
    problems = []
    for path in sorted(NOTEBOOK_DIR.glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for marker in NOTEBOOK_INSTALL_MARKERS:
                if marker in line:
                    problems.append(f"{path.name}: line {i}: {marker}")
    assert not problems, "\n".join(problems)


def test_no_sys_path_manipulation():
    problems = []
    for path in sorted(NOTEBOOK_DIR.glob("*.py")) + sorted(PIPELINE_DIR.glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for marker in SYS_PATH_MARKERS:
                if marker in line:
                    problems.append(f"{path.relative_to(REPO_ROOT)}: line {i}: {marker}")
    assert not problems, "\n".join(problems)


def task_environment_specs() -> dict[str, dict]:
    """Return {task_key: environment spec} for every job task that has an environment_key."""
    specs = {}
    for _, config in load_resources():
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
