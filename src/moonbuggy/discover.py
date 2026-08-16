"""Zero-config project layout discovery.

Criterion H2 and the low-floor principle from 6.2: bare `moonbuggy` in a pytest
project's root should just work, with configuration available but never
required.

Discovery is deliberately conservative and explains itself. Guessing wrong here
means mutating the wrong files, and a tool that silently mutates a project's
test suite or its vendored dependencies wastes a long run and produces nonsense.
When the layout cannot be identified, this raises with the flag to pass rather
than picking something plausible.
"""

import os
from collections.abc import Sequence
from pathlib import Path

# Directories never treated as source under test, regardless of layout.
EXCLUDED = {
    "tests",
    "test",
    "testing",
    "docs",
    "doc",
    "examples",
    "example",
    "build",
    "dist",
    "site-packages",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    ".tox",
    ".nox",
    "migrations",
    "scripts",
}


class LayoutError(RuntimeError):
    """Raised when the project layout cannot be identified with confidence."""


def find_source_dir(project_dir: str | os.PathLike[str]) -> Path:
    """The directory whose Python files should be mutated.

    Order matters: an explicit src/ layout is unambiguous, so it wins before any
    heuristic gets a chance to be clever.
    """
    project_dir = Path(project_dir).resolve()

    src = project_dir / "src"
    if src.is_dir():
        packages = _packages_in(src)
        if len(packages) == 1:
            return packages[0]
        if packages:
            return src
        raise LayoutError(
            f"{src} exists but contains no Python package. "
            "Pass --source to say which directory to mutate."
        )

    packages = _packages_in(project_dir)
    if len(packages) == 1:
        return packages[0]
    if len(packages) > 1:
        names = ", ".join(p.name for p in packages)
        raise LayoutError(
            f"Found several candidate packages ({names}). "
            "Pass --source to say which one to mutate."
        )

    if _loose_modules(project_dir):
        return project_dir

    raise LayoutError(
        f"No Python source found under {project_dir}. "
        "Run moonbuggy from your project root, or pass --source."
    )


def find_source_files(
    source_dir: str | os.PathLike[str],
    project_dir: str | os.PathLike[str],
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> list[str]:
    """Every mutable file under source_dir, as paths relative to project_dir."""
    source_dir = Path(source_dir).resolve()
    project_dir = Path(project_dir).resolve()

    files = []
    for path in sorted(source_dir.rglob("*.py")):
        relative_path = path.relative_to(project_dir)
        # Only the path RELATIVE to the project is checked against EXCLUDED.
        # Checking absolute parts would exclude every file in a project that
        # happens to live under a directory called `docs`, `build` or `tests`
        # anywhere above the root -- which is where this fixture itself lives.
        if any(part in EXCLUDED for part in relative_path.parts[:-1]):
            continue
        if path.name.startswith("test_") or path.name in {"conftest.py", "setup.py"}:
            continue
        relative = str(relative_path)
        if include and not any(fragment in relative for fragment in include):
            continue
        if exclude and any(fragment in relative for fragment in exclude):
            continue
        files.append(relative)
    return files


def looks_like_pytest_project(project_dir: str | os.PathLike[str]) -> bool:
    """Whether this directory plausibly hosts a pytest suite.

    Used to fail fast with a clear message rather than after a coverage pass
    that collects nothing (criterion H5).
    """
    project_dir = Path(project_dir)
    markers = ["pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "conftest.py"]
    if any((project_dir / marker).exists() for marker in markers):
        return True
    return any(project_dir.rglob("test_*.py"))


def _packages_in(directory: Path) -> list[Path]:
    return [
        child
        for child in sorted(directory.iterdir())
        if child.is_dir()
        and child.name not in EXCLUDED
        and not child.name.startswith(".")
        and (child / "__init__.py").exists()
    ]


def _loose_modules(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.glob("*.py")
        if not path.name.startswith("test_")
        and path.name not in {"conftest.py", "setup.py"}
    ]
