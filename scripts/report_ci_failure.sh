#!/usr/bin/env bash
#
# Milestone M6.3.2: a scheduled job whose only failure signal is an email
# nobody reads is not a gate. Opens one issue per failing tier and comments on
# it thereafter, so a tier that has been broken for a week is one issue with a
# week of comments rather than seven issues.
#
# Usage: report_ci_failure.sh <label> <title>
# Requires: GH_TOKEN in the environment, and `issues: write` on the job.
set -euo pipefail

LABEL="$1"
TITLE="$2"
RUN_URL="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
BODY="Scheduled run failed: ${RUN_URL}"

EXISTING="$(gh issue list --label "$LABEL" --state open --limit 1 --json number --jq '.[0].number // empty')"

if [ -n "$EXISTING" ]; then
  echo "==> commenting on existing issue #${EXISTING}"
  gh issue comment "$EXISTING" --body "$BODY"
else
  echo "==> opening a new issue"
  gh issue create --label "$LABEL" --title "$TITLE" --body "$BODY"
fi
