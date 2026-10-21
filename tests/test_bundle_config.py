"""Bundle config guard: CI fails while databricks.yml or resources/*.yml hold unfilled placeholders.

Plain-text scan (no YAML parser dependency): flags any "REPLACE_WITH" and service-principal keys
(`*sp_app_id`, `service_principal_name`) whose value is empty, blank quotes or the all-zero UUID.
"""

import re
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
