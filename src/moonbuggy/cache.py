"""Persistent results cache, so repeat runs skip mutants nothing has changed for.

Two independent sources pointed at this (6.4): Hypothesis's replay database and
Ruff's incremental re-linting. Both converge on the same idea, which is why the
design promoted it into the MVP rather than deferring it.

The risk is not a cache miss, it is a stale hit. Serving a SURVIVED after the
user added the test that kills it hides the gap they just closed and reports it
as still outstanding -- worse than no cache, because it is confidently wrong.
The key therefore covers everything the outcome depends on:

    the mutant's identity and mutated text
  + the full source of the module being mutated
  + the contents of every test file selected for it

Hashing the whole module rather than just the mutated function is deliberately
coarser than criterion F2 requires. A mutant's behaviour can depend on anything
else in its module -- a helper it calls, an import, a module-level constant --
and per-function hashing would miss those. Coarse and correct beats precise and
occasionally stale; if this ever shows up in profiles, it is a safe thing to
tighten with evidence.
"""

import hashlib
import json
import os
from pathlib import Path

# Bumped whenever the key derivation or record shape changes. An old cache is
# then ignored rather than misread -- entries keyed by a different algorithm are
# not wrong-looking, they are silently wrong.
CACHE_VERSION = 1


class ResultCache:
    def __init__(self, path):
        self.path = Path(path)
        self._entries = self._load()

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable: cost a cold run, never a wrong result.
            return {}
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return {}
        entries = payload.get("entries")
        return entries if isinstance(entries, dict) else {}

    def key_for(self, mutant, project_dir, selected_tests):
        project_dir = Path(project_dir)
        digest = hashlib.sha256()
        digest.update(mutant.id.encode())
        digest.update(mutant.mutated.encode())
        digest.update(_read_bytes(project_dir / mutant.module))
        # Sorted so selection order cannot change the key.
        for test_id in sorted(selected_tests):
            test_file = test_id.split("::")[0]
            digest.update(test_file.encode())
            digest.update(_read_bytes(project_dir / test_file))
        return digest.hexdigest()

    def get(self, key):
        return self._entries.get(key)

    def put(self, key, record):
        self._entries[key] = record

    def save(self):
        """Persist the cache, atomically.

        Written to a sibling temp file and renamed, so a run killed during the
        save leaves the previous cache intact rather than a half-written file
        (criterion M1.4.13). `os.replace` is atomic within a filesystem, and the
        temp file is deliberately a sibling so it is on the same one.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps({"version": CACHE_VERSION, "entries": self._entries}, sort_keys=True)
        )
        os.replace(temporary, self.path)

    def clear(self):
        self._entries = {}
        self.path.unlink(missing_ok=True)

    def __len__(self):
        return len(self._entries)


def _read_bytes(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        # A missing file is a real state, not an error: hash it as such so the
        # key changes if it later appears.
        return b"\0missing\0"
