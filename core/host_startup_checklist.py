"""Ordered host/guest Jamulus setup steps shown in the session HUD."""

from __future__ import annotations

HOST_STARTUP_CHECKLIST_STEPS: tuple[str, str, str] = (
    "Pick your interface, input channels, headphones, and buffer in Jamulus.",
    "WebJam continues when Jamulus connects. It will wait up to 10 minutes.",
    "Copy Invite appears here.",
)

HOST_STARTUP_PROFILE_NOTE = (
    "WebJam uses a dedicated Jamulus profile for this app and leaves your "
    "regular Jamulus settings untouched."
)

WIN32_MUSIC_HOST_REASON = (
    "Hosting needs the macOS app. You can still Join."
)


def host_startup_checklist_current_step(phase: str) -> int | None:
    """Return the emphasized checklist step for a startup phase, if any."""

    if phase == "launching_client":
        return 1
    if phase == "native_sound_setup":
        return 2
    if phase == "invite_ready":
        return 3
    return None


def format_host_startup_checklist(
    current_step: int,
    *,
    steps: tuple[str, str, str] = HOST_STARTUP_CHECKLIST_STEPS,
) -> tuple[str, str]:
    """Return plain and rich-text bodies for the three checklist lines."""

    if current_step not in {1, 2, 3}:
        raise ValueError("current_step must be 1, 2, or 3")
    plain_lines: list[str] = []
    rich_lines: list[str] = []
    for index, body in enumerate(steps, start=1):
        line = f"{index}. {body}"
        plain_lines.append(line)
        if index == current_step:
            rich_lines.append(f"<b>{line}</b>")
        else:
            rich_lines.append(line)
    plain = "\n".join(plain_lines)
    rich = "<br>".join(rich_lines)
    return plain, rich
