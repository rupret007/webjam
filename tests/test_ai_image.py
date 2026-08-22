"""Art's AI image action: two verbs, local only, and nothing published.

WebJam generates nothing here. Everything below is about being honest
concerning a stack WebJam does not own -- whether Krita and its AI plugin are
installed, whether a *local* backend answered -- and about the boundaries that
keep one artist's generator out of the room and off the network.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.ai_image import (
    MANAGED_BACKEND_MESSAGE,
    NOT_IN_A_ROOM_MESSAGE,
    AiImageAvailability,
    AiImageController,
    AiImageSnapshot,
    AiImageState,
    AiImageStudio,
)
from core.krita_ai import (
    AI_IMAGE_SUFFIXES,
    DEFAULT_BACKEND_URL,
    DEFAULT_KRITA_CANDIDATES,
    DEFAULT_KRITA_RESOURCE_DIRS,
    INSTALL_KRITA_MESSAGE,
    INSTALL_PLUGIN_MESSAGE,
    NEW_IMAGE_HEIGHT,
    NEW_IMAGE_WIDTH,
    REMOTE_BACKEND_REFUSED_MESSAGE,
    AiImageError,
    AiImageUnavailableError,
    LocalImage,
    backend_probe_url,
    find_ai_plugin,
    find_krita,
    krita_edit_arguments,
    krita_make_arguments,
    load_local_image,
    normalize_local_backend_url,
)


class FakeStudio:
    """A Krita stack that records what it was asked to open."""

    def __init__(
        self,
        *,
        krita: bool = True,
        plugin: bool = True,
        backend: str = DEFAULT_BACKEND_URL,
    ) -> None:
        self.availability = AiImageAvailability(
            krita_found=krita, plugin_found=plugin, backend_url=backend
        )
        self.new_images = 0
        self.opened: list[str] = []
        self.fail_with: Exception | None = None

    def probe(self) -> AiImageAvailability:
        return self.availability

    def open_new_image(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.new_images += 1

    def open_image(self, image: LocalImage) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.opened.append(image.display_name)


def _controller(studio: FakeStudio, *, in_room: bool = True) -> AiImageController:
    return AiImageController(studio, in_room=lambda: in_room)


def _image(tmp_path: Path, name: str = "photo.png") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pretend pixels")
    return path


# ---------------------------------------------------------------------------
# The network boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:8188",
        "127.0.0.1:8188",
        "localhost:8188",
        "http://localhost",
        "http://[::1]:8188",
        "HTTP://127.0.0.1:8188/",
        None,
        "",
    ],
)
def test_a_loopback_backend_is_accepted(value: object):
    assert normalize_local_backend_url(value).startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))


@pytest.mark.parametrize(
    "value",
    [
        "http://192.168.1.5:8188",
        "http://10.0.0.4:8188",
        "https://api.example.com",
        "https://api.openai.com/v1/images",
        "http://127.0.0.1.evil.example",
        "http://evil.example/?host=127.0.0.1",
        "http://user:secret@127.0.0.1:8188",
        "http://127.0.0.1:8188/queue",
        "http://127.0.0.1:8188?token=abc",
        "http://127.0.0.1:99999",
        "file:///etc/passwd",
        "ws://127.0.0.1:8188",
        42,
        "x" * 400,
    ],
)
def test_anything_that_is_not_loopback_is_refused(value: object):
    """This is the whole network boundary of the AI feature.

    No path through Make or Edit may reach off this machine, so an address
    that is not loopback is refused before a request is ever built.
    """

    with pytest.raises(AiImageError) as failure:
        normalize_local_backend_url(value)
    assert str(failure.value) == REMOTE_BACKEND_REFUSED_MESSAGE


def test_the_refusal_never_echoes_the_address():
    try:
        normalize_local_backend_url("https://api.example.com/v1?key=sk-secret")
    except AiImageError as exc:
        assert "sk-secret" not in str(exc)
        assert "api.example.com" not in str(exc)
    else:  # pragma: no cover - the parse must fail
        pytest.fail("a remote backend must be refused")


def test_the_probe_address_is_a_read_only_status_endpoint():
    probe = backend_probe_url(DEFAULT_BACKEND_URL)

    assert probe == "http://127.0.0.1:8188/system_stats"
    assert "prompt" not in probe
    assert "upload" not in probe


def test_the_probe_address_cannot_be_pointed_off_the_machine():
    with pytest.raises(AiImageError):
        backend_probe_url("https://api.example.com")


# ---------------------------------------------------------------------------
# Finding Krita and its plugin
# ---------------------------------------------------------------------------


def test_no_installed_krita_reports_nothing_rather_than_guessing(tmp_path: Path):
    assert find_krita([str(tmp_path / "nowhere" / "krita")]) is None


def test_a_real_krita_executable_is_found(tmp_path: Path):
    binary = tmp_path / "krita"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    assert find_krita([str(binary)]) == binary.resolve()


def test_only_explicit_absolute_candidates_are_searched(tmp_path: Path, monkeypatch):
    impostor = tmp_path / "krita"
    impostor.write_text("#!/bin/sh\n")
    impostor.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert find_krita(["krita"]) is None
    assert find_krita(["./krita"]) is None


def test_the_default_candidate_lists_carry_no_wildcards():
    from core.external_program import has_no_wildcards

    assert DEFAULT_KRITA_CANDIDATES
    assert DEFAULT_KRITA_RESOURCE_DIRS
    assert has_no_wildcards(DEFAULT_KRITA_CANDIDATES)
    assert has_no_wildcards(DEFAULT_KRITA_RESOURCE_DIRS)


def test_the_ai_plugin_is_found_where_krita_installs_it(tmp_path: Path):
    plugin = tmp_path / "krita" / "pykrita" / "ai_diffusion"
    plugin.mkdir(parents=True)

    assert find_ai_plugin([str(tmp_path / "krita")]) == plugin.resolve()


def test_krita_without_the_plugin_reports_no_plugin(tmp_path: Path):
    (tmp_path / "krita" / "pykrita").mkdir(parents=True)

    assert find_ai_plugin([str(tmp_path / "krita")]) is None


def test_a_plugin_file_is_not_a_plugin_folder(tmp_path: Path):
    parent = tmp_path / "krita" / "pykrita"
    parent.mkdir(parents=True)
    (parent / "ai_diffusion").write_text("not a package")

    assert find_ai_plugin([str(tmp_path / "krita")]) is None


def test_malformed_resource_entries_are_skipped_not_raised(tmp_path: Path):
    plugin = tmp_path / "krita" / "pykrita" / "ai_diffusion"
    plugin.mkdir(parents=True)

    assert find_ai_plugin(
        [None, 7, "", "relative/dir", str(tmp_path / "krita")]
    ) == plugin.resolve()


# ---------------------------------------------------------------------------
# The artist's own file
# ---------------------------------------------------------------------------


def test_a_local_image_is_accepted(tmp_path: Path):
    image = load_local_image(_image(tmp_path))

    assert image.display_name == "photo.png"
    assert image.byte_size > 0


def test_a_loaded_image_never_repeats_its_path(tmp_path: Path):
    """A stray log line must not leak the artist's directory layout."""

    text = repr(load_local_image(_image(tmp_path)))

    assert str(tmp_path) not in text
    assert "photo.png" in text


@pytest.mark.parametrize("suffix", sorted(AI_IMAGE_SUFFIXES))
def test_every_advertised_suffix_is_actually_accepted(tmp_path: Path, suffix: str):
    path = tmp_path / f"art{suffix}"
    path.write_bytes(b"pixels")

    assert load_local_image(path).display_name == f"art{suffix}"


def test_an_unsupported_file_is_refused(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("not an image")

    with pytest.raises(AiImageError, match="local image files ending in"):
        load_local_image(path)


def test_a_missing_empty_or_indirect_file_is_refused(tmp_path: Path):
    with pytest.raises(AiImageError):
        load_local_image(tmp_path / "gone.png")

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(AiImageError):
        load_local_image(empty)

    directory = tmp_path / "folder.png"
    directory.mkdir()
    with pytest.raises(AiImageError):
        load_local_image(directory)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_a_symlink_is_refused(tmp_path: Path):
    """The artist chose a file; a link could point somewhere they did not."""

    real = _image(tmp_path, "real.png")
    link = tmp_path / "link.png"
    link.symlink_to(real)

    with pytest.raises(AiImageError):
        load_local_image(link)


# ---------------------------------------------------------------------------
# The commands WebJam runs
# ---------------------------------------------------------------------------


def test_make_opens_a_fresh_canvas_and_takes_no_prompt():
    """Krita owns the prompt. WebJam reproducing one would be a generator."""

    arguments = krita_make_arguments("/bin/krita")

    assert arguments == [
        "/bin/krita",
        "--nosplash",
        "--new-image",
        f"RGBA,U8,{NEW_IMAGE_WIDTH},{NEW_IMAGE_HEIGHT}",
    ]
    joined = " ".join(arguments).casefold()
    for absent in ("prompt", "model", "checkpoint", "lora", "steps", "sampler"):
        assert absent not in joined, absent


@pytest.mark.parametrize("size", [(32, 32), (10_000, 512), (512.0, 512), (512, None)])
def test_an_unsupported_new_image_size_is_refused(size):
    width, height = size
    with pytest.raises(AiImageError):
        krita_make_arguments("/bin/krita", width=width, height=height)


def test_edit_opens_exactly_the_file_the_artist_chose(tmp_path: Path):
    image = load_local_image(_image(tmp_path))

    assert krita_edit_arguments("/bin/krita", image) == [
        "/bin/krita",
        "--nosplash",
        str(image.path),
    ]


def test_only_a_verified_image_can_become_an_edit_command(tmp_path: Path):
    with pytest.raises(AiImageError):
        krita_edit_arguments("/bin/krita", str(_image(tmp_path)))


# ---------------------------------------------------------------------------
# When the two verbs are honest to offer
# ---------------------------------------------------------------------------


def test_outside_a_room_the_action_exists_but_does_nothing():
    """AI Image is an in-session action, not a launch workflow."""

    studio = FakeStudio()
    controller = _controller(studio, in_room=False)
    snapshot = controller.snapshot

    assert snapshot.state is AiImageState.NOT_IN_A_ROOM
    assert snapshot.can_generate is False
    assert snapshot.message == NOT_IN_A_ROOM_MESSAGE

    with pytest.raises(AiImageError, match="part of an art session"):
        controller.make()
    assert studio.new_images == 0


def test_a_ready_stack_offers_make_and_edit(tmp_path: Path):
    studio = FakeStudio()
    controller = _controller(studio)

    assert controller.snapshot.state is AiImageState.READY
    assert controller.snapshot.can_generate is True
    assert controller.snapshot.backend_label == DEFAULT_BACKEND_URL

    controller.make()
    assert studio.new_images == 1

    controller.edit(_image(tmp_path))
    assert studio.opened == ["photo.png"]


def test_no_local_backend_is_a_normal_state_not_a_failure():
    """Krita's plugin installs and manages its own server."""

    controller = _controller(FakeStudio(backend=""))
    snapshot = controller.snapshot

    assert snapshot.state is AiImageState.READY_MANAGED_BACKEND
    assert snapshot.can_generate is True
    assert snapshot.needs_install is False
    assert snapshot.message == MANAGED_BACKEND_MESSAGE
    # There is no backend to name, so none is claimed.
    assert snapshot.backend_label == ""


def test_no_krita_fails_closed_with_an_install_path(tmp_path: Path):
    studio = FakeStudio(krita=False)
    controller = _controller(studio)
    snapshot = controller.snapshot

    assert snapshot.state is AiImageState.NEEDS_KRITA
    assert snapshot.can_generate is False
    assert snapshot.needs_install is True
    assert snapshot.message == INSTALL_KRITA_MESSAGE

    with pytest.raises(AiImageUnavailableError):
        controller.make()
    with pytest.raises(AiImageUnavailableError):
        controller.edit(_image(tmp_path))
    assert studio.new_images == 0
    assert studio.opened == []


def test_krita_without_the_ai_plugin_fails_closed_too(tmp_path: Path):
    studio = FakeStudio(plugin=False)
    controller = _controller(studio)

    assert controller.snapshot.state is AiImageState.NEEDS_PLUGIN
    assert controller.snapshot.message == INSTALL_PLUGIN_MESSAGE

    with pytest.raises(AiImageUnavailableError):
        controller.make()
    assert studio.new_images == 0


def test_a_probe_that_raises_reads_as_nothing_installed():
    class Exploding:
        def probe(self):
            raise RuntimeError("probe blew up")

        def open_new_image(self):  # pragma: no cover - never reached
            raise AssertionError

        def open_image(self, image):  # pragma: no cover
            raise AssertionError

    controller = AiImageController(Exploding(), in_room=lambda: True)

    assert controller.snapshot.state is AiImageState.NEEDS_KRITA


def test_a_krita_that_will_not_start_is_reported_not_swallowed():
    studio = FakeStudio()
    studio.fail_with = OSError("no exec")
    controller = _controller(studio)

    with pytest.raises(AiImageError, match="couldn't open Krita"):
        controller.make()


def test_a_bad_file_choice_never_reaches_krita(tmp_path: Path):
    studio = FakeStudio()
    controller = _controller(studio)
    bad = tmp_path / "notes.txt"
    bad.write_text("not an image")

    with pytest.raises(AiImageError):
        controller.edit(bad)
    assert studio.opened == []


def test_the_activity_line_names_what_was_opened(tmp_path: Path):
    controller = _controller(FakeStudio())

    controller.make()
    assert "new canvas" in controller.snapshot.activity

    controller.edit(_image(tmp_path))
    assert "photo.png" in controller.snapshot.activity

    assert controller.clear_activity().activity == ""


# ---------------------------------------------------------------------------
# What this feature structurally cannot do
# ---------------------------------------------------------------------------


def test_nobody_drives_anyone_elses_generator():
    """There is no host and no guest here, only this computer.

    A host that could Make on a guest's machine would need a role and a
    publisher. The controller is given neither.
    """

    controller = _controller(FakeStudio())

    for forbidden in ("publish", "broadcast", "share", "withdraw", "observe"):
        assert not hasattr(controller, forbidden), forbidden
    assert "is_host" not in AiImageController.__init__.__code__.co_varnames


def test_a_generated_image_is_never_projected_to_the_room():
    """The session wire schema has no AI member, and gains none."""

    import uuid

    from core.session_transfer import RecordingSignal, SessionStateSnapshot

    state = SessionStateSnapshot(
        session_id=str(uuid.uuid4()),
        generation=1,
        signal=RecordingSignal.IDLE,
        creator_profile_key="art",
    )
    payload = set(state.__dataclass_fields__)

    assert not any("ai" == name or name.startswith("ai_") for name in payload)
    assert not hasattr(state, "ai_image")


def test_the_snapshot_carries_no_prompt_model_or_cloud_key():
    snapshot = AiImageSnapshot()

    for forbidden in ("prompt", "model", "api_key", "token", "checkpoint"):
        assert not hasattr(snapshot, forbidden), forbidden


def test_the_fake_studio_satisfies_the_real_seam():
    assert isinstance(FakeStudio(), AiImageStudio)
