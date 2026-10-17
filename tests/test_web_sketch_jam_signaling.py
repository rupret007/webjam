from __future__ import annotations

import os

import pytest

from core.web_sketch_jam_signaling import (
    PAIR_INDICES,
    SEAT_COUNT,
    all_sketch_jam_mailbox_ids,
    claim_seat_without_collision,
    seat_claim_order,
    sketch_jam_pair_mailbox_id,
    sketch_jam_seat_mailbox_id,
)


def _invite_secret() -> bytes:
    return os.urandom(32)


def test_four_peers_derive_six_distinct_pairwise_mailbox_ids_and_claim_seats_without_slot_collision() -> None:
    invite = _invite_secret()
    ids = all_sketch_jam_mailbox_ids(invite)
    assert len(ids) == SEAT_COUNT + len(PAIR_INDICES)
    assert len(set(ids)) == len(ids)
    for locator in ids:
        assert len(locator) == 64
        assert locator.isascii() and all(ch in "0123456789abcdef" for ch in locator)

    nonces = [os.urandom(32) for _ in range(SEAT_COUNT)]
    seats = claim_seat_without_collision(invite, nonces)
    assert set(seats.values()) == set(range(SEAT_COUNT))
    for nonce in nonces:
        order = seat_claim_order(nonce)
        assert len(order) == SEAT_COUNT
        assert len(set(order)) == SEAT_COUNT

    taken = {0, 1}
    late = os.urandom(32)
    assert seat_claim_order(late)[0] in taken
    assigned = claim_seat_without_collision(invite, [late], occupied=taken)
    assert assigned[late] in {2, 3}


def test_pair_mailbox_ids_are_ordered_and_stable() -> None:
    invite = _invite_secret()
    for i, j in PAIR_INDICES:
        assert sketch_jam_pair_mailbox_id(invite, i, j) == sketch_jam_pair_mailbox_id(invite, i, j)
    with pytest.raises(ValueError):
        sketch_jam_pair_mailbox_id(invite, 1, 0)
    with pytest.raises(ValueError):
        sketch_jam_seat_mailbox_id(invite, 4)
