import sys
from pathlib import Path

sys.path.insert(0, "scripts")
import tempfile

import workloads

with tempfile.TemporaryDirectory() as root:
    p = workloads.build(root, "fast-tests")
    print(Path(str(p)) / "app" / "mod_0.py")
    print((Path(str(p)) / "app" / "mod_0.py").read_text())
