"""Support `python -m moonbuggy.cli` with the package layout.

As a single module, cli.py *was* the `__main__` target, so `-m moonbuggy.cli`
ran it and hit the `if __name__ == "__main__"` guard. As a package the engine
looks for this file instead (PEP 338), so the same invocation funnels here.
"""

from . import run

if __name__ == "__main__":
    run()
