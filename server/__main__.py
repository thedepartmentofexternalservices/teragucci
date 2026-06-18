"""Thin entrypoint for ``python -m server``.

D-11 / Plan 01-11 Task 2. Dispatches to ``server.main:main`` so the existing
invocation paths all keep working:

  * ``python -m server.main --auth-mode none``   (legacy path, still works)
  * ``python -m server`` (this module — new, short form)
  * ``teraguchi-server`` (pyproject.toml ``[project.scripts]`` console-script —
    points at ``server.main:main`` and remains unchanged)

``sys.path.insert(0, ".")`` matches the idiom at ``server/main.py:44``. The
CONVENTIONS.md note says that idiom is intentional — it lets the module be run
directly from a source checkout without needing an editable install.
"""
import sys

sys.path.insert(0, ".")
from server.main import main

if __name__ == "__main__":
    main()
