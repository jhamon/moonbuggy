"""Milestone M7.1: refuse to release a tag that disagrees with the repository.

Checks, in order, that the tag matches the packaged version and that the
changelog has a non-empty section for it. Each failure names which of the three
disagreed, because "version mismatch" without saying which side is wrong is a
message that sends the reader to look at both.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def packaged_version():
    """Return the version literal from src/moonbuggy/__init__.py.

    Returns:
        The version string, without quotes.
    """
    text = (ROOT / "src" / "moonbuggy" / "__init__.py").read_text()
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        sys.exit("FAIL: no __version__ literal in src/moonbuggy/__init__.py")
    return match.group(1)


def changelog_section(version):
    """Return the changelog body for a version, or None if absent.

    Args:
        version: The version to look for, without a leading 'v'.

    Returns:
        The section body with surrounding whitespace stripped, or None.
    """
    text = (ROOT / "CHANGELOG.md").read_text()
    pattern = rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return None if match is None else match.group(1).strip()


def main():
    """Check the tag against the package version and the changelog.

    Returns:
        0 when everything agrees; exits non-zero with a message otherwise.
    """
    if len(sys.argv) != 2:
        sys.exit("usage: check_version_consistency.py <tag>")
    tag = sys.argv[1]

    if not tag.startswith("v"):
        sys.exit(f"FAIL: tag {tag!r} does not start with 'v'")
    version = tag[1:]

    packaged = packaged_version()
    if packaged != version:
        sys.exit(
            f"FAIL (M7.1.1): tag {tag} means version {version}, but "
            f"src/moonbuggy/__init__.py says {packaged}"
        )

    section = changelog_section(version)
    if section is None:
        sys.exit(f"FAIL (M7.1.3): CHANGELOG.md has no section for {version}")
    if not section:
        sys.exit(f"FAIL (M7.1.3): the CHANGELOG.md section for {version} is empty")

    print(f"OK: tag {tag}, package {packaged}, changelog section present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
