"""Extractor image guard: static checks on Dockerfile and .dockerignore (no Docker needed).

The image build, smoke run, hadolint and Trivy run in the ci.yml `docker` job. These checks
keep the basics from regressing: a non-root final USER, no remote ADD, pinned base images
(no :latest, no untagged FROM), no repo-wide COPY or secrets, and a build context that never
contains .git, .venv or .databricks.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
ROOT_USERS = {"root", "0", "0:0", "root:root"}
REQUIRED_IGNORES = {".git", ".venv", ".databricks"}
TAG_RE = re.compile(r":[A-Za-z0-9][A-Za-z0-9._-]*$")


def instructions(text: str) -> list[tuple[str, str]]:
    """Return (INSTRUCTION, arguments) pairs; comments dropped, `\\` continuations joined."""
    joined = re.sub(r"\\\n", " ", text)
    result = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keyword, _, args = line.partition(" ")
        result.append((keyword.upper(), args.strip()))
    return result


def build_args(parsed: list[tuple[str, str]]) -> dict[str, str]:
    """Default values of ARG instructions, e.g. `ARG X=1` -> {"X": "1"}."""
    found = {}
    for keyword, args in parsed:
        if keyword == "ARG" and "=" in args:
            name, _, value = args.partition("=")
            found[name.strip()] = value.strip()
    return found


def base_images(parsed: list[tuple[str, str]]) -> list[str]:
    """Image of every FROM (ARG defaults resolved), skipping FROM <earlier stage>."""
    args = build_args(parsed)
    stages: set[str] = set()
    images = []
    for keyword, rest in parsed:
        if keyword != "FROM":
            continue
        words = [w for w in rest.split() if not w.startswith("--")]
        image = re.sub(r"\$\{?(\w+)\}?", lambda m: args.get(m.group(1), m.group(0)), words[0])
        if len(words) >= 3 and words[1].upper() == "AS":
            stages.add(words[2].lower())
        if image.lower() not in stages:
            images.append(image)
    return images


def final_user(parsed: list[tuple[str, str]]) -> str | None:
    """USER of the last stage (None if the last stage sets none)."""
    user = None
    for keyword, args in parsed:
        if keyword == "FROM":
            user = None
        elif keyword == "USER":
            user = args
    return user


def is_pinned(image: str) -> bool:
    """True if an image has a digest or a tag other than latest."""
    if "@sha256:" in image:
        return True
    name = image.rsplit("/", 1)[-1]
    match = TAG_RE.search(name)
    return match is not None and match.group(0) != ":latest"


def dockerignore_patterns() -> set[str]:
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return {p.strip().rstrip("/") for p in lines if p.strip() and not p.startswith("#")}


@pytest.fixture(scope="module")
def parsed() -> list[tuple[str, str]]:
    return instructions(DOCKERFILE.read_text(encoding="utf-8"))


def test_instructions_parse_continuations_and_comments():
    text = "# c\nFROM a:1 AS b\nRUN x \\\n    && y\n\nUSER app\n"
    assert instructions(text) == [("FROM", "a:1 AS b"), ("RUN", "x      && y"), ("USER", "app")]


def test_base_images_resolves_args_and_skips_stages():
    parsed = instructions("ARG IMG=python:3.12.1-slim\nFROM ${IMG} AS b\nFROM uv:1 AS u\nFROM b\n")
    assert base_images(parsed) == ["python:3.12.1-slim", "uv:1"]


@pytest.mark.parametrize(
    ("image", "pinned"),
    [
        ("python:3.12.16-slim-trixie", True),
        ("ghcr.io/astral-sh/uv:0.13.1", True),
        ("localhost:5000/python:3.12", True),
        ("python@sha256:" + "a" * 64, True),
        ("python", False),
        ("python:latest", False),
        ("localhost:5000/python", False),
    ],
)
def test_is_pinned(image, pinned):
    assert is_pinned(image) is pinned


def test_final_user():
    assert final_user(instructions("FROM a:1\nUSER app\nFROM b:1\n")) is None
    assert final_user(instructions("FROM a:1\nUSER root\nFROM b:1\nUSER 10001:10001\n")) == (
        "10001:10001"
    )


def test_final_user_is_not_root(parsed):
    user = final_user(parsed)
    assert user, "final stage has no USER (runs as root)"
    assert user.lower() not in ROOT_USERS, f"final USER is root: {user}"


def test_base_images_are_pinned(parsed):
    images = base_images(parsed)
    assert images, "no FROM in Dockerfile"
    unpinned = [image for image in images if not is_pinned(image)]
    assert not unpinned, f"unpinned base images: {unpinned}"


def test_python_base_pins_patch_version(parsed):
    python = [i for i in base_images(parsed) if i.split(":")[0].endswith("python")]
    assert python, "no python base image"
    for image in python:
        assert re.search(r":3\.12\.\d+-slim", image), (
            f"python base not a 3.12.<patch>-slim: {image}"
        )


def test_no_remote_add(parsed):
    remote = [
        args for keyword, args in parsed if keyword == "ADD" and re.search(r"https?://", args)
    ]
    assert not remote, f"ADD from URL: {remote}"


def test_no_repo_wide_copy_or_secrets(parsed):
    problems = []
    for keyword, args in parsed:
        if keyword != "COPY":
            continue
        sources = [w for w in args.split() if not w.startswith("--")][:-1]
        if any(s in {".", "./", "*"} for s in sources):
            problems.append(f"repo-wide COPY {args}")
        if any(".env" in s or ".databricks" in s for s in sources):
            problems.append(f"COPY of secrets {args}")
    for keyword, args in parsed:
        if keyword in {"ENV", "ARG"} and re.search(r"DATABRICKS|TOKEN|SECRET", args, re.I):
            problems.append(f"{keyword} {args}")
    assert not problems, "\n".join(problems)


def test_entrypoint_is_extractor_cli(parsed):
    entrypoints = [args for keyword, args in parsed if keyword == "ENTRYPOINT"]
    assert entrypoints and "energy_lakehouse.extract_local" in entrypoints[-1]
    assert ("VOLUME", '["/data"]') in parsed
    assert ("WORKDIR", "/app") in parsed


def test_dockerignore_excludes_required_paths():
    patterns = dockerignore_patterns()
    missing = REQUIRED_IGNORES - patterns
    assert not missing, f".dockerignore lacks {sorted(missing)}"
    # README.md is project.readme, which the wheel build needs.
    assert "*.md" in patterns and "!README.md" in patterns
