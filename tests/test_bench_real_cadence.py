"""The bench-real drift cadence (H40 follow-up, t_ad5398cd).

Decision: weekly, paired with a fresh capture (workflow ``bench-real.yml``).
These tests pin the glue the workflow depends on — the entry point's behavior
when the gate is wired after a real capture — without running the ~75-min
bench. The workflow YAML itself is validated structurally: its steps must call
the same targets the tests exercise, so a rename in the Makefile fails a test
here instead of failing silently in a Monday cron.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"


class TestWorkflowGlue:
    """Structural pins on bench-real.yml: the cron slot, the gate wiring,
    and the commit-back surface."""

    def setup_method(self):
        self.text = (WORKFLOWS / "bench-real.yml").read_text(encoding="utf-8")

    def test_workflow_file_exists(self):
        assert (WORKFLOWS / "bench-real.yml").is_file()

    def test_scheduled_weekly_monday(self):
        # 06:00 UTC Mondays: after the nightly Bench (03:00), before the
        # Weekly tier (08:00), and only on the day a fresh capture lands.
        assert "0 6 * * 1" in self.text

    def test_manual_dispatch_allowed(self):
        assert "workflow_dispatch" in self.text

    def test_runs_the_bench_real_target(self):
        # The capture step must run `make bench-real`, whose target runs
        # scripts/bench_real.py — the same entry point the drift tests pin.
        assert "make bench-real" in self.text

    def test_workflow_actually_runs_the_drift_gate(self):
        # QA rework: `make bench-real` alone only captures and exits 0 — the
        # gate lives in the separate --check-drift invocation. Pin that the
        # workflow's run steps include the drift target, so a sustained
        # regression can actually exit 2 in CI (fails on the pre-rework
        # wiring, where the only step was the capture).
        steps = [
            line.strip()
            for line in self.text.splitlines()
            if line.strip().startswith("run: make ")
        ]
        assert any(s.startswith("run: make bench-real-drift") for s in steps), (
            f"no drift-gate step among run steps: {steps}"
        )

    def test_failure_reporter_can_post(self):
        # report_ci_failure.sh requires `issues: write` on the job (same as
        # nightly.yml / weekly.yml); without it the failure() step silently
        # cannot create the board issue.
        assert "issues: write" in self.text

    def test_target_runs_bench_real_script(self):
        makefile = (REPO / "Makefile").read_text(encoding="utf-8")
        assert "$(PYTHON) scripts/bench_real.py" in makefile

    def test_commit_back_covers_both_artifacts(self):
        # The archive rows AND the stored prime must be committed back, or
        # the next Monday run sees neither (check_drift would re-prime
        # forever and no regression could ever sustain across weeks).
        assert "intel/perf-bench-real-baseline.jsonl" in self.text
        assert "intel/perf-baseline-real.json" in self.text

    def test_commit_back_is_noop_when_unchanged(self):
        # Same convention as bench.yml: no empty commit noise on main.
        assert "git diff --cached --quiet" in self.text

    def test_failure_reports_to_the_board(self):
        # The failure-reporter convention the other tiered workflows use.
        assert "report_ci_failure.sh" in self.text

    def test_timeout_ceiling_declared(self):
        # The bench is ~75 min locally; the ceiling must exist and exceed it,
        # but stay bounded (never "no timeout").
        assert "timeout-minutes:" in self.text


class TestDriftExitContract:
    """The workflow gates on the process exit code, so the documented exit
    contract (0 pass/prime, 2 sustained regression, 3 archive/baseline error)
    is load-bearing glue. Re-verify it against the live entry point."""

    def test_check_drift_help_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, "scripts/bench_real.py", "--help"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0
        assert "--check-drift" in proc.stdout

    def test_check_drift_on_missing_archive_is_error_three(self, tmp_path, monkeypatch):
        sys.path.insert(0, str(REPO / "scripts"))
        import bench_real

        monkeypatch.setattr(bench_real, "REAL_ARCHIVE", tmp_path / "absent.jsonl")
        monkeypatch.setattr(bench_real, "REAL_BASELINE", tmp_path / "absent.json")
        assert bench_real.check_drift() == 3

    def test_check_drift_exit_codes_are_documented(self):
        doc = (REPO / "Makefile").read_text(encoding="utf-8")
        assert "0 pass/prime; 2 sustained regression; 3 archive/baseline error" in doc
