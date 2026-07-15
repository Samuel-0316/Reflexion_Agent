"""
Simple in-memory reflection store.
Resets every session — no persistence by design.
"""

from __future__ import annotations


class ReflectionMemory:
    """Append-only list of reflection strings for the current session."""

    def __init__(self) -> None:
        self._reflections: list[str] = []

    def add(self, reflection: str) -> None:
        """Store a new critique."""
        self._reflections.append(reflection)

    def get_all(self) -> list[str]:
        """Return all stored reflections (oldest first)."""
        return list(self._reflections)

    def clear(self) -> None:
        """Reset memory (useful between runs if the object is reused)."""
        self._reflections.clear()

    def __len__(self) -> int:
        return len(self._reflections)

    def __repr__(self) -> str:
        return f"ReflectionMemory({len(self)} reflections)"