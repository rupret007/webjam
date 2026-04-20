"""Load the Conductor QSS stylesheet with token substitution."""

from __future__ import annotations

from pathlib import Path

from webjam_qt.theme.tokens import Color, Font, Radius, Space


def load_stylesheet() -> str:
    qss_path = Path(__file__).parent / "conductor.qss"
    template = qss_path.read_text(encoding="utf-8")

    substitutions: dict[str, str] = {}
    for cls in (Color, Space, Radius, Font):
        for name in dir(cls):
            if name.startswith("_"):
                continue
            value = getattr(cls, name)
            if callable(value):
                continue
            key = f"{cls.__name__.upper()}_{name}"
            substitutions[key] = str(value)

    for key, value in substitutions.items():
        template = template.replace("${" + key + "}", value)

    return template
