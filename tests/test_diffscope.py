"""Diff scoping: which lines a branch changed, and what that excludes.

Every test here builds a real git repository in a tmp_path and asks git the
same question moonbuggy asks it. Faking `git diff` output would test the
parser against a fixture of what we *believe* git prints, which is precisely
the belief most likely to be wrong.
"""

import subprocess

import pytest

from moonbuggy.diffscope import DiffScopeError, scope_since, scope_summary


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    """A repository with one commit on `main` and nothing else."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "lib.py").write_text(
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n",
        encoding="utf-8",
    )
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "first")
    return tmp_path


def commit(repo, message="change"):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def test_only_changed_lines_are_in_scope(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "lib.py").write_text(
        "def one():\n    return 1\n\n\ndef two():\n    return 22\n",
        encoding="utf-8",
    )
    commit(repo)

    scope = scope_since("main", repo)

    assert scope.contains("lib.py", 6)
    assert not scope.contains("lib.py", 2)
    assert scope.files == ("lib.py",)
    assert scope.changed_lines == 1


def test_uncommitted_edits_are_in_scope(repo):
    # The working tree is what moonbuggy mutates, so it is what the diff has to
    # be taken against: a scope computed from HEAD alone would hand back line
    # numbers for a file that is not the one being read.
    (repo / "lib.py").write_text(
        "def one():\n    return 111\n\n\ndef two():\n    return 2\n",
        encoding="utf-8",
    )

    scope = scope_since("main", repo)

    assert scope.contains("lib.py", 2)
    assert not scope.contains("lib.py", 6)


def test_an_untracked_file_is_entirely_in_scope(repo):
    # A module you have just written is new code with no test behind it, which
    # is the most valuable thing a diff-scoped run can look at. It is invisible
    # to `git diff`, so it is collected separately -- otherwise a brand new
    # file would be silently skipped, which is the worst way to be fast.
    (repo / "fresh.py").write_text("def added():\n    return 3\n", encoding="utf-8")

    scope = scope_since("main", repo)

    assert scope.contains("fresh.py", 1)
    assert scope.contains("fresh.py", 2)
    assert not scope.contains("fresh.py", 99)


def test_the_ref_is_compared_against_the_merge_base_not_its_tip(repo):
    # main moves on after the branch is cut. Those commits are not this
    # branch's work and must not be pulled into its scope.
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "lib.py").write_text(
        "def one():\n    return 1\n\n\ndef two():\n    return 22\n",
        encoding="utf-8",
    )
    commit(repo, "branch work")
    git(repo, "checkout", "-q", "main")
    (repo / "other.py").write_text("def elsewhere():\n    return 0\n", encoding="utf-8")
    commit(repo, "main moved on")
    git(repo, "checkout", "-q", "feature")

    scope = scope_since("main", repo)

    assert scope.files == ("lib.py",)
    assert not scope.contains("other.py", 1)


def test_a_pure_deletion_contributes_no_lines(repo):
    # There is nothing left to mutate where the lines used to be, so a hunk
    # that only removes lines puts nothing in scope. The file is not reported
    # as changed either, because "1 file, 0 lines" would send a reader looking
    # for mutants that cannot exist.
    (repo / "lib.py").write_text("def one():\n    return 1\n", encoding="utf-8")

    scope = scope_since("main", repo)

    assert scope.files == ()
    assert scope.changed_lines == 0
    assert not scope.contains("lib.py", 2)


def test_a_deleted_file_is_not_in_scope(repo):
    (repo / "lib.py").unlink()

    scope = scope_since("main", repo)

    assert scope.files == ()


def test_a_rename_is_scoped_under_its_new_path(repo):
    # The mutant's module is the path on disk now. Scoping a renamed file
    # under the name it no longer has would drop every one of its mutants.
    git(repo, "checkout", "-q", "-b", "feature")
    git(repo, "mv", "lib.py", "renamed.py")
    (repo / "renamed.py").write_text(
        "def one():\n    return 1\n\n\ndef two():\n    return 22\n",
        encoding="utf-8",
    )
    commit(repo, "rename and edit")

    scope = scope_since("main", repo)

    assert "renamed.py" in scope.files
    assert scope.contains("renamed.py", 6)


def test_paths_are_relative_to_the_project_not_the_repository_root(repo):
    # A project may sit in a subdirectory of its repository -- a monorepo
    # package, say -- and mutants are named relative to the project.
    (repo / "pkg").mkdir()
    (repo / "pkg" / "inner.py").write_text("def x():\n    return 1\n", encoding="utf-8")
    (repo / "outside.py").write_text("def y():\n    return 1\n", encoding="utf-8")
    commit(repo, "subdir")
    (repo / "pkg" / "inner.py").write_text("def x():\n    return 2\n", encoding="utf-8")
    (repo / "outside.py").write_text("def y():\n    return 2\n", encoding="utf-8")

    scope = scope_since("HEAD", repo / "pkg")

    assert scope.files == ("inner.py",)
    assert scope.contains("inner.py", 2)


def test_not_a_git_repository_is_a_clear_error(tmp_path):
    with pytest.raises(DiffScopeError) as raised:
        scope_since("main", tmp_path)

    assert "git" in str(raised.value)
    assert str(tmp_path) in str(raised.value)


def test_an_unknown_ref_is_a_clear_error(repo):
    with pytest.raises(DiffScopeError) as raised:
        scope_since("origin/nope", repo)

    assert "origin/nope" in str(raised.value)
    # The commonest cause in CI is a checkout that fetched one commit.
    assert "fetch-depth" in str(raised.value)


def test_a_shallow_clone_with_no_merge_base_is_a_clear_error(repo, tmp_path):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "lib.py").write_text("def one():\n    return 9\n", encoding="utf-8")
    commit(repo, "second")
    (repo / "lib.py").write_text("def one():\n    return 10\n", encoding="utf-8")
    commit(repo, "third")

    clone = tmp_path / "shallow"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            "--branch",
            "feature",
            repo.as_uri(),
            str(clone),
        ],
        check=True,
        capture_output=True,
    )
    # A depth-1 clone has feature's tip and nothing that main and feature
    # share, so there is no merge base to diff against.
    subprocess.run(
        ["git", "fetch", "-q", "--depth", "1", "origin", "main:main"],
        cwd=clone,
        check=True,
        capture_output=True,
    )

    with pytest.raises(DiffScopeError) as raised:
        scope_since("main", clone)

    assert "merge base" in str(raised.value)
    assert "fetch-depth" in str(raised.value)


def test_the_scope_describes_itself_honestly(repo):
    (repo / "lib.py").write_text(
        "def one():\n    return 1\n\n\ndef two():\n    return 22\n",
        encoding="utf-8",
    )

    text = scope_since("main", repo).describe()

    assert "main" in text
    assert "1 file" in text
    assert "1 line" in text


def test_the_summary_is_structured_data(repo):
    (repo / "lib.py").write_text(
        "def one():\n    return 1\n\n\ndef two():\n    return 22\n",
        encoding="utf-8",
    )
    scope = scope_since("main", repo)

    summary = scope_summary(scope)

    assert summary["diff_scoped"] is True
    assert summary["since"] == "main"
    assert summary["files"] == 1
    assert summary["changed_lines"] == 1
    assert summary["merge_base"] == git(repo, "rev-parse", "main").strip()


def test_the_summary_of_a_full_run_says_so():
    summary = scope_summary(None)

    assert summary["diff_scoped"] is False
    assert summary["since"] is None
    assert summary["merge_base"] is None
