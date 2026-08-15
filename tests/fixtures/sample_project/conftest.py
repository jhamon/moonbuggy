"""Make the `sample` package importable without installing this fixture.

Explicit sys.path insertion rather than relying on pytest's rootdir behaviour,
so the fixture runs identically under plain pytest, under xdist, and under the
naive oracle runner (which copies the tree to a temp directory).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
