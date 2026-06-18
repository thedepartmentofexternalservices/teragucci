"""Thin entrypoint — `python -m client` dispatches to client.app:main."""
from __future__ import annotations

from client.app import main


if __name__ == "__main__":
    main()
