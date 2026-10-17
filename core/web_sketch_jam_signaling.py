"""HKDF mailbox locators for Web Sketch Jam (4-peer mesh, 6 pairwise links).

Each locator is a distinct 32-byte HKDF-SHA256 output hex-encoded (64 chars),
compatible with ``deploy/relay/cloudflare-worker/src/mailbox.js`` validation.
"""

from __future__ import annotations

import os
from typing import Iterable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SKETCH_JAM_IKM_BYTES = 32
SEAT_COUNT = 4
PAIR_INDICES = tuple((i, j) for i in range(SEAT_COUNT) for j in range(i + 1, SEAT_COUNT))

SEAT_MAILBOX_INFO = tuple(
    f"webjam/sketch-jam/seat/{seat}".encode("ascii") for seat in range(SEAT_COUNT)
)
PAIR_MAILBOX_INFO = {
    (i, j): f"webjam/sketch-jam/pair/{i}/{j}".encode("ascii") for i, j in PAIR_INDICES
}
SKETCH_JAM_AEAD_INFO = b"webjam/sketch-jam/aead/1"


def _fixed_ikm(invite_secret: bytes) -> bytes:
    raw = bytes(invite_secret or b"")
    if len(raw) != SKETCH_JAM_IKM_BYTES:
        raise ValueError("invite secret must be 32 bytes")
    return raw


def _hkdf(ikm: bytes, *, salt: bytes, info: bytes, length: int = 32) -> bytes:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(ikm)
    if len(derived) != length or derived == b"\x00" * length:
        raise ValueError("derived key is not valid")
    return derived


def sketch_jam_seat_mailbox_id(invite_secret: bytes, seat: int) -> str:
    if seat not in range(SEAT_COUNT):
        raise ValueError("seat out of range")
    return _hkdf(_fixed_ikm(invite_secret), salt=b"", info=SEAT_MAILBOX_INFO[seat]).hex()


def sketch_jam_pair_mailbox_id(invite_secret: bytes, i: int, j: int) -> str:
    if i >= j or i not in range(SEAT_COUNT) or j not in range(SEAT_COUNT):
        raise ValueError("pair indices out of range")
    info = PAIR_MAILBOX_INFO[(i, j)]
    return _hkdf(_fixed_ikm(invite_secret), salt=b"", info=info).hex()


def sketch_jam_mailbox_aead_key(invite_secret: bytes, mailbox_id_hex: str) -> bytes:
    mailbox_id = bytes.fromhex(str(mailbox_id_hex))
    if len(mailbox_id) != 32:
        raise ValueError("mailbox id must be 32 bytes")
    return _hkdf(
        _fixed_ikm(invite_secret),
        salt=mailbox_id,
        info=SKETCH_JAM_AEAD_INFO,
    )


def seat_claim_order(join_nonce: bytes) -> tuple[int, ...]:
    """Rotate seat indices so peers spread load; 409 duplicate -> try next seat."""

    raw = bytes(join_nonce or b"")
    if len(raw) != 32:
        raise ValueError("join nonce must be 32 bytes")
    start = raw[0] % SEAT_COUNT
    order: list[int] = []
    for offset in range(SEAT_COUNT):
        order.append((start + offset) % SEAT_COUNT)
    return tuple(order)


def all_sketch_jam_mailbox_ids(invite_secret: bytes) -> tuple[str, ...]:
    seats = tuple(sketch_jam_seat_mailbox_id(invite_secret, s) for s in range(SEAT_COUNT))
    pairs = tuple(
        sketch_jam_pair_mailbox_id(invite_secret, i, j) for i, j in PAIR_INDICES
    )
    return seats + pairs


def claim_seat_without_collision(
    invite_secret: bytes,
    join_nonces: Iterable[bytes],
    occupied: set[int] | None = None,
) -> dict[bytes, int]:
    """Simulate seat mailboxes (write-once offer) for each joiner."""

    taken = set(occupied or ())
    assignments: dict[bytes, int] = {}
    for nonce in join_nonces:
        for seat in seat_claim_order(nonce):
            if seat in taken:
                continue
            taken.add(seat)
            assignments[nonce] = seat
            break
        else:
            raise ValueError("no seat available")
    return assignments
