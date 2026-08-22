"""Finding the real AI image stack: Krita, its AI plugin, and a local backend.

Art lets someone in the room make a new image from text, or edit a photo they
already own, with AI. WebJam does not generate anything. It does for the image
stack exactly what it does for Jamulus and Drawpile: it finds the real
programs, launches them, and stays out of the way.

The real stack here is **Krita AI Diffusion** -- a Krita plugin -- driving a
**local ComfyUI** backend. That shape decides most of this module:

* Krita is the editor and the host of the generator UI, so WebJam looks for
  Krita the way it looks for Drawpile.
* The plugin is an add-on the artist installed into Krita's own resource
  folder at ``pykrita/ai_diffusion``, so WebJam checks for it there and says
  plainly when it is missing rather than opening Krita to a docker that is not
  there.
* ComfyUI runs on the artist's own machine. The plugin can install and manage
  one itself, and it also connects to a server that is already running, so a
  backend WebJam cannot see is a normal state rather than a failure.

The one hard rule is the network boundary. WebJam will only ever look at a
**loopback** backend. A remote or cloud endpoint is refused outright, so no
code path here can turn "generate an image" into "upload the artist's photo to
somebody else's computer".
"""

from __future__ import annotations

import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from core.external_program import find_program, resolve_directory

#: Krita 5.2.0 is the plugin's stated minimum. WebJam does not enforce a
#: version -- it cannot read one without launching Krita -- but the copy that
#: points someone at the download says which version they need.
KRITA_MINIMUM_VERSION = "5.2.0"

#: The folder the plugin installs into, inside Krita's own resource directory.
AI_PLUGIN_DIRECTORY = "ai_diffusion"
AI_PLUGIN_PARENT = "pykrita"

#: ComfyUI's default address, and the one Krita AI Diffusion uses.
DEFAULT_BACKEND_URL = "http://127.0.0.1:8188"
#: ComfyUI answers this with a small JSON document and no side effects.
BACKEND_PROBE_PATH = "/system_stats"

MAX_BACKEND_URL_CHARS = 200
MAX_IMAGE_BYTES = 512 * 1024 * 1024
MAX_IMAGE_NAME_CHARS = 255

#: What the artist may hand to Edit. Krita opens far more than this; the list
#: is bounded to ordinary local raster images plus Krita's and the open raster
#: formats, so a stray document cannot be routed here by accident.
AI_IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".kra", ".ora"}
)

#: Loopback only. Anything else is refused before a request is ever built.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})
_BACKEND_SCHEMES = frozenset({"http", "https"})
_HOSTNAME = re.compile(r"\A[A-Za-z0-9._-]{1,253}\Z")

INSTALL_KRITA_MESSAGE = (
    "WebJam could not find Krita on this computer. Install Krita "
    f"{KRITA_MINIMUM_VERSION} or newer, then open AI Image again."
)
INSTALL_PLUGIN_MESSAGE = (
    "Krita is installed, but its AI Image Generation plugin is not. Install "
    "Krita AI Diffusion, then open AI Image again."
)
NOT_AN_IMAGE_MESSAGE = (
    "WebJam edits local image files you already have. Choose a regular image "
    "file on this computer."
)
REMOTE_BACKEND_REFUSED_MESSAGE = (
    "WebJam only talks to an image backend running on this computer. A remote "
    "or cloud address is refused so your work is never uploaded anywhere."
)


class AiImageError(RuntimeError):
    """A bounded, path-free AI image failure safe to show a person."""


class AiImageUnavailableError(AiImageError):
    """The local AI image stack is not installed on this computer."""


# ---------------------------------------------------------------------------
# The local backend address
# ---------------------------------------------------------------------------


def normalize_local_backend_url(text: object = None) -> str:
    """Return a loopback backend address, refusing anything that is not.

    This is the whole network boundary of the AI feature. Every probe and
    every displayed address goes through here, so there is exactly one place
    to check that WebJam never reaches off this machine.
    """

    if text is None or (isinstance(text, str) and not text.strip()):
        return DEFAULT_BACKEND_URL
    if not isinstance(text, str):
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE)
    value = text.strip()
    if len(value) > MAX_BACKEND_URL_CHARS or not value.isascii():
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE)
    if "://" not in value:
        # ``127.0.0.1:8188`` is how the plugin itself writes it.
        value = f"http://{value}"
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE) from exc
    if parts.scheme.lower() not in _BACKEND_SCHEMES:
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE)
    if parts.path not in ("", "/"):
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE)
    host = (parts.hostname or "").lower()
    if not host or _HOSTNAME.fullmatch(host) is None and host != "::1":
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE)
    if host not in _LOOPBACK_HOSTS:
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE)
    try:
        port = parts.port
    except ValueError as exc:
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE) from exc
    if port is not None and not 1 <= port <= 65535:
        raise AiImageError(REMOTE_BACKEND_REFUSED_MESSAGE)
    netloc = host if port is None else f"{host}:{port}"
    if ":" in host:  # IPv6 literal needs its brackets back
        netloc = f"[{host}]" if port is None else f"[{host}]:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, "", "", ""))


def backend_probe_url(backend_url: str) -> str:
    """Return the read-only status address for an already-checked backend."""

    return f"{normalize_local_backend_url(backend_url)}{BACKEND_PROBE_PATH}"


# ---------------------------------------------------------------------------
# Finding Krita and its plugin
# ---------------------------------------------------------------------------

DEFAULT_KRITA_CANDIDATES: tuple[str, ...] = (
    # macOS
    "/Applications/krita.app/Contents/MacOS/krita",
    "~/Applications/krita.app/Contents/MacOS/krita",
    # Windows
    r"C:\Program Files\Krita (x64)\bin\krita.exe",
    r"C:\Program Files\Krita\bin\krita.exe",
    # Linux distribution packages, Flatpak exports, Homebrew, and Snap
    "/usr/bin/krita",
    "/usr/local/bin/krita",
    "/var/lib/flatpak/exports/bin/org.kde.krita",
    "~/.local/share/flatpak/exports/bin/org.kde.krita",
    "/opt/homebrew/bin/krita",
    "/snap/bin/krita",
)

#: Krita's own resource directory, where an artist's plugins live.
DEFAULT_KRITA_RESOURCE_DIRS: tuple[str, ...] = (
    # macOS
    "~/Library/Application Support/krita",
    # Linux
    "~/.local/share/krita",
    # Linux, Flatpak sandbox
    "~/.var/app/org.kde.krita/data/krita",
    # Windows
    "~/AppData/Roaming/krita",
)


def find_krita(candidates: object = None) -> Path | None:
    """Return the first real Krita executable among ``candidates``."""

    return find_program(
        DEFAULT_KRITA_CANDIDATES if candidates is None else candidates
    )


def find_ai_plugin(resource_dirs: object = None) -> Path | None:
    """Return the installed Krita AI Diffusion plugin folder, or ``None``.

    Presence is all WebJam checks. It does not read the plugin's settings,
    parse its version, or touch its user data: that folder belongs to Krita
    and to the artist.
    """

    if resource_dirs is None:
        resource_dirs = DEFAULT_KRITA_RESOURCE_DIRS
    if isinstance(resource_dirs, (str, bytes, os.PathLike)):
        resource_dirs = (resource_dirs,)
    try:
        entries = list(resource_dirs)
    except TypeError:
        return None
    for entry in entries:
        root = resolve_directory(entry)
        if root is None:
            continue
        plugin = root / AI_PLUGIN_PARENT / AI_PLUGIN_DIRECTORY
        resolved = resolve_directory(plugin)
        if resolved is not None:
            return resolved
    return None


# ---------------------------------------------------------------------------
# The artist's own image file
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, repr=False)
class LocalImage:
    """One descriptor-verified local image the artist chose to edit.

    ``__repr__`` omits the path so a stray log line cannot leak the artist's
    directory layout, the same rule the reference video source follows.
    """

    path: Path
    display_name: str
    byte_size: int

    def __repr__(self) -> str:
        return (
            f"LocalImage(display_name={self.display_name!r}, "
            f"bytes={self.byte_size})"
        )


def load_local_image(path: str | os.PathLike[str]) -> LocalImage:
    """Accept one regular local image file, failing closed on anything else."""

    try:
        candidate = Path(path).expanduser()
    except (TypeError, ValueError, OSError) as exc:
        raise AiImageError(NOT_AN_IMAGE_MESSAGE) from exc
    if not candidate.is_absolute():
        candidate = candidate.resolve()
    if candidate.suffix.lower() not in AI_IMAGE_SUFFIXES:
        supported = ", ".join(sorted(AI_IMAGE_SUFFIXES))
        raise AiImageError(f"WebJam edits local image files ending in {supported}.")

    name = " ".join(candidate.name.split())
    if (
        not name
        or len(name) > MAX_IMAGE_NAME_CHARS
        or any(character in name for character in ("/", "\\", "\0"))
        or not all(character.isprintable() for character in name)
    ):
        raise AiImageError(NOT_AN_IMAGE_MESSAGE)

    try:
        details = candidate.lstat()
    except OSError as exc:
        raise AiImageError(NOT_AN_IMAGE_MESSAGE) from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise AiImageError(NOT_AN_IMAGE_MESSAGE)
    if details.st_size <= 0:
        raise AiImageError(NOT_AN_IMAGE_MESSAGE)
    if details.st_size > MAX_IMAGE_BYTES:
        raise AiImageError("That image is larger than WebJam will hand to Krita.")

    return LocalImage(
        path=candidate, display_name=name, byte_size=int(details.st_size)
    )


# ---------------------------------------------------------------------------
# The commands WebJam runs
# ---------------------------------------------------------------------------

#: A square canvas large enough for current diffusion models to work well and
#: small enough not to guess at someone's final output size. Krita's own New
#: Image dialog is one menu away for anything else.
NEW_IMAGE_WIDTH = 1024
NEW_IMAGE_HEIGHT = 1024


def krita_make_arguments(
    executable: str | os.PathLike[str],
    *,
    width: int = NEW_IMAGE_WIDTH,
    height: int = NEW_IMAGE_HEIGHT,
) -> list[str]:
    """Open Krita on a fresh canvas, ready for the AI docker.

    WebJam takes no prompt. The plugin owns the prompt, the model, and every
    generation setting, and reproducing any of that here would be inventing a
    generator rather than integrating one.
    """

    if type(width) is not int or type(height) is not int:
        raise AiImageError("A new image needs whole-number dimensions.")
    if not 64 <= width <= 8192 or not 64 <= height <= 8192:
        raise AiImageError("That new image size is outside the supported range.")
    return [
        str(executable),
        "--nosplash",
        "--new-image",
        f"RGBA,U8,{width},{height}",
    ]


def krita_edit_arguments(
    executable: str | os.PathLike[str], image: LocalImage
) -> list[str]:
    """Open Krita on one image the artist already owns."""

    if not isinstance(image, LocalImage):
        raise AiImageError(NOT_AN_IMAGE_MESSAGE)
    return [str(executable), "--nosplash", str(image.path)]


def krita_download_url() -> str:
    return "https://krita.org/en/download/"


def ai_plugin_download_url() -> str:
    return "https://github.com/Acly/krita-ai-diffusion/releases/latest"


def platform_supports_krita() -> bool:
    """Whether this platform has documented Krita install locations."""

    return sys.platform in {"darwin", "win32"} or sys.platform.startswith("linux")


__all__ = [
    "AI_IMAGE_SUFFIXES",
    "AI_PLUGIN_DIRECTORY",
    "AI_PLUGIN_PARENT",
    "BACKEND_PROBE_PATH",
    "DEFAULT_BACKEND_URL",
    "DEFAULT_KRITA_CANDIDATES",
    "DEFAULT_KRITA_RESOURCE_DIRS",
    "INSTALL_KRITA_MESSAGE",
    "INSTALL_PLUGIN_MESSAGE",
    "KRITA_MINIMUM_VERSION",
    "NEW_IMAGE_HEIGHT",
    "NEW_IMAGE_WIDTH",
    "NOT_AN_IMAGE_MESSAGE",
    "REMOTE_BACKEND_REFUSED_MESSAGE",
    "AiImageError",
    "AiImageUnavailableError",
    "LocalImage",
    "ai_plugin_download_url",
    "backend_probe_url",
    "find_ai_plugin",
    "find_krita",
    "krita_download_url",
    "krita_edit_arguments",
    "krita_make_arguments",
    "load_local_image",
    "normalize_local_backend_url",
    "platform_supports_krita",
]
