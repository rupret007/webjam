"""Host-facing Web Join invite URL and offline QR helpers (no network)."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_web_join_invite_url(
    join_page: str,
    capability: bytes,
    session: bytes,
    generation: int,
    offer_expiry: int,
) -> str:
    """Return the full browser join URL including the credential fragment."""

    page = str(join_page or "").strip()
    if not page.endswith("/"):
        page = f"{page}/"
    if (
        len(capability) != 32
        or len(session) != 32
        or type(generation) is not int
        or not 1 <= generation <= 2**32 - 1
        or type(offer_expiry) is not int
        or offer_expiry <= 0
    ):
        raise ValueError("invalid web join invite material")
    return (
        f"{page}#k={_b64(capability)}&s={session.hex()}&g={generation}&e={offer_expiry}"
    )


def web_join_invite_qr_png(url: str, *, dark: str = "#1a1a1a") -> bytes:
    """Render a scannable QR PNG for the invite URL using the bundled segno encoder."""

    import segno

    qr = segno.make(str(url), error="m")
    output = io.BytesIO()
    qr.save(output, kind="png", scale=3, border=4, dark=dark)
    return output.getvalue()


@dataclass(frozen=True, slots=True)
class ClipboardCopyResult:
    ok: bool
    used_fallback: bool
    notice: str


COPIED_NOTICE = "Copied! Send it to your bandmates"
CLIPBOARD_DENIED_NOTICE = "Select the link below and copy it yourself."


def copy_text_to_qt_clipboard(clipboard, text: str) -> ClipboardCopyResult:
    """Copy invite text with a privacy-safe outcome (never logs the payload)."""

    value = str(text or "")
    if not value:
        return ClipboardCopyResult(
            ok=False, used_fallback=True, notice=CLIPBOARD_DENIED_NOTICE
        )
    try:
        clipboard.setText(value)
    except Exception:  # noqa: BLE001 - clipboard backends vary by platform
        return ClipboardCopyResult(
            ok=False, used_fallback=True, notice=CLIPBOARD_DENIED_NOTICE
        )
    return ClipboardCopyResult(ok=True, used_fallback=False, notice=COPIED_NOTICE)
