"""Sphinx configuration (milestone M3.1).

Two decisions worth stating rather than leaving to be inferred:

- **`-W` is on in the Makefile, not here.** Warnings-as-errors is a property of
  how the build is invoked, and keeping it in the Makefile means a contributor
  running `sphinx-build` by hand to look at one page is not fighting it, while
  the build that matters still fails on a broken cross-reference (M3.1.2).
- **MyST, not reStructuredText.** The project's existing documents are Markdown
  and are checked into the same directory. Converting them would have been a
  large diff whose only benefit was uniformity with a format nobody here writes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from moonbuggy import __version__

project = "moonbuggy"
author = "Jennifer Hamon"
copyright = "2026, Jennifer Hamon"
release = __version__
version = __version__

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

# M3.2.3: Google style, and only Google style. NumPy support is off so that a
# NumPy-style docstring renders wrongly and gets noticed, rather than rendering
# correctly and quietly establishing a second convention.
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_use_param = True
napoleon_use_rtype = True

autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
# M3.2.5: the design rationale lives in module docstrings, and it is the most
# valuable prose in the codebase. Rendering it into the API pages is the whole
# point of generating them rather than hand-writing signatures.
autodoc_preserve_defaults = True

# `linkify` is deliberately NOT enabled. It turns anything that looks like a
# URI into a link, and "file:line" -- which appears in several docstrings
# describing mutant ids -- looks exactly like one. linkcheck then reports a
# broken `file:` link, and the build fails over a phrase in prose.
myst_enable_extensions = ["deflist", "colon_fence"]
myst_heading_anchors = 3
# YAML frontmatter in blog posts (date, author). MyST reads these fields so
# they are available to the page context without a separate plugin.
myst_frontmatter_enabled = True

templates_path = ["_templates"]
# M5.3: `docs/superpowers` holds this project's own development record -- the
# design spec and implementation plan for building moonbuggy itself, not
# documentation about moonbuggy. They're checked in as a record, not written
# for or linked from any page a user would reach, so they have no toctree
# entry -- which `-W` turns into a build error unless excluded here. Beyond
# quieting the warning, this keeps them out of `docs/_build/html`, which
# Task 12 publishes to GitHub Pages: internal planning documents should not
# ship to users.
#
# `docs/development` is the same kind of material and is excluded for the same
# reason: milestone lists, criterion-by-criterion status, spike write-ups and
# the performance-hypothesis ledger. They are addressed to whoever was building
# moonbuggy, not to whoever is using it. They stay in the tree -- source
# comments cite them by path -- but nine sidebar entries of build history is
# not what a newcomer to mutation testing should have to read past.
#
# `docs/contracts` is the versioned cross-bot interface (D1/D2) -- the harness
# feed, the verdict vocabulary. It is a build/CI contract, not user docs: the
# changelog, dashboard, and release receipts are derived from it, but readers
# of moonbuggy do not. It has no toctree entry and is excluded for the same
# reason as `development` (a `-W` build errors on a page with no toctree home).
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "README.md",
    "superpowers",
    "development",
    "contracts",
    # Internal competitive-intelligence mirror (see the header note in the
    # file itself). It exists for repo access only, is never linked from any
    # toctree, and must not ship to users -- same exclusion rationale as
    # `development` and `contracts`.
    "competitive-intel.md",
]

html_theme = "furo"
html_title = f"moonbuggy {release}"
html_static_path = ["_static"]
html_css_files = ["custom.css"]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pytest": ("https://docs.pytest.org/en/stable", None),
}
# M3.1.3 runs linkcheck for INTERNAL links. External links are deliberately not
# checked: a third-party site being down is not a defect in this documentation,
# and a build that fails for that reason teaches people to ignore the build.
linkcheck_ignore = [r"^https?://"]

nitpicky = False

# M3.3.10: every code example in the documentation is executable and verified by
# `make docs-test`. Examples that show a command line need to actually run that
# command line, so this puts a hermetic project builder and a CLI runner in
# scope for every doctest. Each example gets its own temporary directory, so no
# example can pass because an earlier one left something behind.
doctest_global_setup = '''
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_TEMPORARY_DIRECTORIES = []


def make_project(files):
    """Write a throwaway pytest project and return its root."""
    holder = tempfile.TemporaryDirectory()
    _TEMPORARY_DIRECTORIES.append(holder)
    root = Path(holder.name)
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    (root / "pytest.ini").write_text("[pytest]\\n")
    (root / "conftest.py").write_text(
        "import sys\\nfrom pathlib import Path\\n"
        "sys.path.insert(0, str(Path(__file__).parent))\\n"
    )
    return root


def moonbuggy(*args, cwd):
    """Run the CLI exactly as a user would, and return the completed process."""
    return subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli", *args],
        cwd=cwd, capture_output=True, text=True, timeout=300,
    )


def records(project):
    """Every JSONL record from the last run in `project`."""
    path = Path(project) / ".moonbuggy" / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
'''

doctest_global_cleanup = """
for _holder in _TEMPORARY_DIRECTORIES:
    _holder.cleanup()
_TEMPORARY_DIRECTORIES.clear()
"""
