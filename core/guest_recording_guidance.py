"""Bounded host guidance for authenticated guest Local Original reports.

These reports explain a blocked inventory. They never authorize a take or
supply the missing mono/stereo topology.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from itertools import islice

from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS
from core.redaction import redact_log_text
from core.session_transfer import PresenceV2Proof


def _failure_detail(proof: PresenceV2Proof) -> str:
    codes = proof.local_original_failure_codes
    if "invalid_capture_settings" in codes:
        return "Local Original audio settings are invalid."
    if "unsupported_sample_rate" in codes:
        return "Local Original capture requires 48 kHz."
    if "invalid_block_size" in codes:
        return "The Local Original audio buffer size is invalid."
    if "invalid_track_map" in codes:
        return "The Local Original track map is invalid."
    if "insufficient_input_channels" in codes:
        channels = proof.local_original_required_input_channels
        needed = f"{channels} input channels" if channels else "more input channels"
        return f"The Local Original map needs {needed}; the selected input has too few."
    if "input_device_or_format_unavailable" in codes:
        return "The Local Original input is unavailable or cannot use 48 kHz."
    return "Local Original readiness could not be verified."


def guest_recording_failure_summary(proofs: Iterable[PresenceV2Proof]) -> str:
    """Explain current reports in at most 540 plain-text characters.

    The caller supplies fresh registry proofs. Older clients, repaired maps,
    and explicit zero-track opt-outs never create an invented diagnosis.
    """

    by_participant = {}
    for proof in islice(proofs, MAX_JAMULUS_ROSTER_ROWS):
        if not isinstance(proof, PresenceV2Proof):
            continue
        if (proof.local_original_diagnostic_version != 1
                or not proof.capture_enabled
                or proof.local_original_track_count is not None):
            continue
        prior = by_participant.get(proof.participant_id)
        if prior is None or proof.presence_generation > prior.presence_generation:
            by_participant[proof.participant_id] = proof
    ordered = sorted(by_participant.values(), key=lambda proof: proof.self_ordinal)
    names = {
        proof.participant_id: redact_log_text(" ".join(proof.display_name.split()))[:45]
        or "Guest"
        for proof in ordered
    }
    counts = Counter(names.values())
    rows = []
    for proof in ordered:
        name = names[proof.participant_id]
        if counts[name] > 1:
            name += f" (participant {proof.self_ordinal + 1})"
        row = f"{name}: {_failure_detail(proof)}"
        if len(rows) == 3 or len(" ".join((*rows, row))) > 490:
            break
        rows.append(row)
    if len(rows) < len(ordered):
        rows.append(f"{len(ordered) - len(rows)} more guests also need attention.")
    return " ".join(rows)
