"""The desktop and browser consume identical, independently generated vectors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.signaling_mailbox import mailbox_id, mailbox_key, open_slot


VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "mailbox-crypto-vectors.json").read_text()
)


@pytest.mark.parametrize("vector", VECTORS, ids=lambda vector: vector["name"])
def test_desktop_browser_mailbox_vectors(vector) -> None:
    capability = bytes.fromhex(vector["capability_hex"])
    session = bytes.fromhex(vector["session_reference_hex"])
    pin = bytes.fromhex(vector["host_spki_sha256_hex"])
    key = mailbox_key(capability, session)
    assert mailbox_id(capability) == vector["mailbox_id"]
    assert key.hex() == vector["mailbox_key_hex"]
    for kind in ("offer", "answer"):
        assert open_slot(
            key,
            vector[f"{kind}_payload"],
            kind=kind,
            session_reference=session,
            host_spki_sha256=pin,
        ) == vector["plaintext_utf8"].encode()
        with pytest.raises(ValueError):
            open_slot(
                key,
                vector[f"{kind}_payload"],
                kind="answer" if kind == "offer" else "offer",
                session_reference=session,
                host_spki_sha256=pin,
            )
