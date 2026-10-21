"""Bundle config guard: CI fails while databricks.yml or resources/*.yml hold unfilled placeholders.

Plain-text scan (no YAML parser dependency): flags any "REPLACE_WITH" and service-principal keys
(`*sp_app_id`, `service_principal_name`) whose value is empty, blank quotes or the all-zero UUID.
Also: SP ids in databricks.yml must be UUIDs, and no tracked file may contain a Databricks OAuth
secret (`dose` + 32 hex). Failures report file and line only, never the matched value.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_FILES = [REPO_ROOT / "databricks.yml", *sorted((REPO_ROOT / "resources").glob("*.yml"))]

PLACEHOLDER = "REPLACE_WITH"
SP_KEY_RE = re.compile(
    r"^(?P<indent>\s*)(?:-\s+)?(?P<key>\w*sp_app_id|service_principal_name)\s*:"
    r"\s*(?P<value>[^#]*?)\s*(?:#.*)?$"
)
EMPTY_SP_VALUES = {"", '""', "''", "null", "~", "00000000-0000-0000-0000-000000000000"}
SP_ID_VALUE_RE = re.compile(r"^\s*(?:cicd_sp_app_id|refresh_sp_app_id)\s*:\s*(?P<value>[^#\s]+)")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
OAUTH_SECRET_RE = re.compile(r"\bdose[0-9a-f]{32}\b")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def find_problems(text: str) -> list[str]:
    """Return `line N: ...` messages for placeholders and empty SP ids in YAML text."""
    lines = text.splitlines()
    problems = []
    for i, line in enumerate(lines):
        if PLACEHOLDER in line:
            problems.append(f"line {i + 1}: placeholder: {line.strip()}")
        match = SP_KEY_RE.match(line)
        if not match or match["value"].strip("\"'").strip().lower() not in EMPTY_SP_VALUES:
            continue
        if match["value"] == "":
            # `key:` followed by a more-indented block is a mapping (e.g. a variable definition).
            following = next((nxt for nxt in lines[i + 1 :] if nxt.strip()), "")
            if following and _indent(following) > _indent(line):
                continue
        problems.append(f"line {i + 1}: empty service principal id: {line.strip()}")
    return problems


@pytest.mark.parametrize("path", BUNDLE_FILES, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_bundle_file_has_no_unfilled_placeholders(path):
    problems = find_problems(path.read_text(encoding="utf-8"))
    assert not problems, f"{path.relative_to(REPO_ROOT)}:\n" + "\n".join(problems)


def test_bundle_files_found():
    assert (REPO_ROOT / "databricks.yml") in BUNDLE_FILES
    assert len(BUNDLE_FILES) > 1


def test_detects_placeholder():
    assert find_problems("  alert_email: REPLACE_WITH_ALERT_EMAIL\n") == [
        "line 1: placeholder: alert_email: REPLACE_WITH_ALERT_EMAIL"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "      refresh_sp_app_id:",
        '      refresh_sp_app_id: ""',
        "      cicd_sp_app_id: ''",
        "      cicd_sp_app_id: null",
        "      cicd_sp_app_id: 00000000-0000-0000-0000-000000000000",
        "      service_principal_name: ''  # fill me",
        "        - service_principal_name:",
    ],
)
def test_detects_empty_sp_id(line):
    assert len(find_problems(line + "\n")) == 1


@pytest.mark.parametrize(
    "text",
    [
        "  cicd_sp_app_id:\n    description: CI/CD SP\n",
        "      refresh_sp_app_id: 3f2a9c1e-1234-4abc-9def-0123456789ab\n",
        "      service_principal_name: ${var.cicd_sp_app_id}\n",
        "        - service_principal_name: ${var.refresh_sp_app_id}\n          level: X\n",
    ],
)
def test_accepts_filled_or_mapping(text):
    assert find_problems(text) == []


def non_uuid_sp_id_lines(text: str) -> list[int]:
    """Line numbers where cicd_sp_app_id / refresh_sp_app_id has a value that is not a UUID."""
    bad = []
    for i, line in enumerate(text.splitlines(), start=1):
        match = SP_ID_VALUE_RE.match(line)
        if match and not UUID_RE.match(match["value"].strip("\"'")):
            bad.append(i)
    return bad


def secret_lines(text: str) -> list[int]:
    """Line numbers containing a Databricks OAuth secret (`dose` + 32 hex chars)."""
    return [i for i, line in enumerate(text.splitlines(), start=1) if OAUTH_SECRET_RE.search(line)]


def test_sp_ids_in_databricks_yml_are_uuids():
    bad = non_uuid_sp_id_lines((REPO_ROOT / "databricks.yml").read_text(encoding="utf-8"))
    # Report line numbers only: a wrong value may be a pasted secret.
    assert not bad, f"databricks.yml: SP id is not a UUID on line(s) {bad}"


def test_no_oauth_secret_in_tracked_files():
    if shutil.which("git") is None:
        pytest.skip("git not available")
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout.decode()
    hits = []
    for name in filter(None, tracked.split("\0")):
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        hits += [f"{name}:{n}" for n in secret_lines(text)]
    # Report file:line only, never the matched value.
    assert not hits, "Databricks OAuth secret found (rotate it): " + ", ".join(hits)


# A fake secret built at runtime, so this file never contains one literally.
FAKE_SECRET = "dose" + "0123456789abcdef" * 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("      cicd_sp_app_id: 7d0f1e32-4a86-42e2-b352-461dae1b3634\n", []),
        ('      refresh_sp_app_id: "e1370f9f-0819-48ba-92f0-365c18361518"\n', []),
        ("  cicd_sp_app_id:\n    description: CI/CD SP\n", []),
        ("      cicd_sp_app_id: ${var.other}\n", [1]),
        (f"x: 1\n      cicd_sp_app_id: {FAKE_SECRET}\n", [2]),
        ("      refresh_sp_app_id: 7D0F1E32-4A86-42E2-B352-461DAE1B3634\n", [1]),
    ],
)
def test_non_uuid_sp_id_lines(text, expected):
    assert non_uuid_sp_id_lines(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"a\nsecret = {FAKE_SECRET}\n", [2]),
        (f"x{FAKE_SECRET}\n", []),
        (f"{FAKE_SECRET}00\n", []),
        ("dose" + "0" * 31 + "\n", []),
        ("7d0f1e32-4a86-42e2-b352-461dae1b3634\n", []),
    ],
)
def test_secret_lines(text, expected):
    assert secret_lines(text) == expected
