"""The energy_lakehouse wheel ships contracts/*.yaml and loads them from package resources.

Builds the wheel once with `uv build --wheel` (as databricks.yml does), checks that every
contracts/*.yaml is in it as energy_lakehouse/contracts_data/<name>, then extracts it (a pure
wheel: extracting = installing) and runs load_contracts() in a subprocess that imports
energy_lakehouse from the extracted copy, where the repo's contracts/ is not reachable.
"""

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FILES = sorted(p.name for p in (REPO_ROOT / "contracts").glob("*.yaml"))

LOAD_SCRIPT = """
import json
from pathlib import Path

from energy_lakehouse import contracts

directory = contracts.default_contracts_dir()
print(json.dumps({
    "module": contracts.__file__,
    "directory": str(directory),
    "source_dir_exists": contracts.CONTRACTS_DIR.is_dir(),
    "tables": [c.table for c in contracts.load_contracts()],
}))
"""


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH (CI always has it)")
    out_dir = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out_dir), str(REPO_ROOT)],
        check=True,
        capture_output=True,
    )
    (path,) = out_dir.glob("*.whl")
    return path


def test_wheel_contains_every_contract(wheel):
    assert CONTRACT_FILES, "no contracts/*.yaml in the repo"
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [f for f in CONTRACT_FILES if f"energy_lakehouse/contracts_data/{f}" not in names]
    assert not missing, f"wheel lacks contracts: {missing}"


def test_load_contracts_from_installed_wheel(wheel, tmp_path):
    site = tmp_path / "site"
    zipfile.ZipFile(wheel).extractall(site)
    # PYTHONPATH precedes site-packages, so the extracted copy shadows the editable install.
    env = {**os.environ, "PYTHONPATH": str(site)}
    result = subprocess.run(
        [sys.executable, "-c", LOAD_SCRIPT],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = json.loads(result.stdout)
    assert Path(loaded["module"]).is_relative_to(site)
    assert not loaded["source_dir_exists"]
    assert Path(loaded["directory"]) == site / "energy_lakehouse" / "contracts_data"
    assert sorted(f"{t}.yaml" for t in loaded["tables"]) == CONTRACT_FILES
