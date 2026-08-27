"""Write the digest into the Obsidian vault as a dated note.

Plain filesystem write — the vault is a folder of Markdown, and Obsidian picks up
the file the moment it lands. Nothing is deleted or overwritten: re-running on the
same day appends a numbered suffix rather than clobbering the morning's note.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..config import obsidian_dir


def write(body: str, *, day: date, directory: Path | None = None) -> Path:
    """Write the note and return its path. Raises if the vault folder is unset."""
    target = directory or obsidian_dir()
    if target is None:
        raise RuntimeError("OBSIDIAN_DIGEST_DIR is not set — nowhere to write the note.")
    target.mkdir(parents=True, exist_ok=True)

    path = target / f"CRE Radar {day.isoformat()}.md"
    suffix = 2
    while path.exists():
        path = target / f"CRE Radar {day.isoformat()} ({suffix}).md"
        suffix += 1

    path.write_text(body)
    return path
