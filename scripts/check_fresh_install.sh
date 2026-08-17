#!/usr/bin/env bash
#
# Criteria H1 and H2: moonbuggy installs from this repo into a clean virtualenv,
# and bare `moonbuggy` -- no flags, no config file -- runs end to end against a
# pytest project it has never seen.
#
# The throwaway project is generated here rather than reusing the fixture. The
# fixture is the layout moonbuggy was developed against, so passing on it says
# nothing about a project encountered for the first time.
set -euo pipefail

# M7.4.1: with no argument this installs the repository, which is criteria
# H1/H2 exactly as they have always run. With --wheel it installs a built
# artifact instead, which is what the release workflow needs: the wheel that
# is about to be published, not the source tree it came from.
WHEEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --wheel)
      WHEEL="${2:?--wheel needs a path}"
      shift 2
      ;;
    *)
      echo "usage: check_fresh_install.sh [--wheel PATH]" >&2
      exit 2
      ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> creating clean virtualenv"
"${PYENV_PY:-$HOME/.pyenv/versions/3.12.13/bin/python3}" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --quiet --upgrade pip

if [ -n "$WHEEL" ]; then
  test -f "$WHEEL" || { echo "FAIL: no such wheel: $WHEEL" >&2; exit 2; }
  echo "==> installing moonbuggy from the built wheel $WHEEL"
  "$WORK/venv/bin/pip" install --quiet "$WHEEL"
else
  echo "==> installing moonbuggy from $REPO"
  "$WORK/venv/bin/pip" install --quiet "$REPO"
fi

echo "==> confirming the console script exists"
"$WORK/venv/bin/moonbuggy" --version

echo "==> generating a project moonbuggy has never seen"
PROJECT="$WORK/unseen"
mkdir -p "$PROJECT"
cat > "$PROJECT/shipping.py" <<'PY'
FREE_THRESHOLD = 50


def shipping_cost(subtotal):
    if subtotal >= FREE_THRESHOLD:
        return 0
    return 5


def with_shipping(subtotal):
    return subtotal + shipping_cost(subtotal)
PY
cat > "$PROJECT/test_shipping.py" <<'PY'
from shipping import shipping_cost, with_shipping


def test_small_order_pays_shipping():
    assert shipping_cost(10) == 5


def test_large_order_ships_free():
    assert shipping_cost(100) == 0


def test_total_includes_shipping():
    assert with_shipping(10) == 15
PY
cat > "$PROJECT/pytest.ini" <<'PY'
[pytest]
pythonpath = .
PY

echo "==> the project's own suite must pass first"
(cd "$PROJECT" && "$WORK/venv/bin/python" -m pytest -q)

echo "==> running bare moonbuggy, no flags"
cd "$PROJECT"
set +e
"$WORK/venv/bin/moonbuggy"
STATUS=$?
set -e
# 0 = nothing survived, 1 = survivors found. Both are successful runs.
if [ "$STATUS" -gt 1 ]; then
  echo "FAIL: moonbuggy exited $STATUS" >&2
  exit 1
fi

echo "==> checking both artifacts exist"
test -s .moonbuggy/results.jsonl || { echo "FAIL: no results.jsonl" >&2; exit 1; }
test -s .moonbuggy/results.txt   || { echo "FAIL: no results.txt" >&2; exit 1; }

echo "==> checking every JSONL line parses"
"$WORK/venv/bin/python" - <<'PY'
import json
with open(".moonbuggy/results.jsonl") as handle:
    records = [json.loads(line) for line in handle if line.strip()]
assert records, "no records"
statuses = {"KILLED", "SURVIVED", "TIMEOUT", "SUSPICIOUS", "SKIPPED"}
for record in records:
    assert record["status"] in statuses, record
print(f"    {len(records)} records, all valid")
PY

echo "==> checking grep works on the plaintext view"
grep -qE '^(KILLED|SURVIVED|TIMEOUT|SUSPICIOUS|SKIPPED)' .moonbuggy/results.txt

echo "==> checking the project's own suite still passes afterwards"
"$WORK/venv/bin/python" -m pytest -q

echo
echo "PASS: H1 (clean install) and H2 (zero-config run on an unseen project)"
