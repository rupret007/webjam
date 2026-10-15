"""Receipt, topology plan, and CLI contracts for the optional NAT harness.

These cases do not create namespaces and are safe in the required master suite.
The privileged two-NAT run lives in tools/ and the Cross-network acceptance
workflow; it is never skip/xfail'd here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.cross_network_acceptance import CONDUCTOR_KIND, SCHEMA
from tools.cross_network_acceptance.receipt import (
    ReceiptError,
    build_receipt,
    conductor_line,
    contains_secret_keys,
    require_full_sha,
    write_receipt,
)
from tools.cross_network_acceptance.topology import (
    NETEM_DELAY,
    NETEM_JITTER,
    NETEM_LOSS,
    TopologySpec,
    assert_no_port_forward,
    netem_commands,
    setup_commands,
)

FULL_SHA = "a" * 40


def test_require_full_sha_rejects_short_and_dirty_values():
    assert require_full_sha(FULL_SHA) == FULL_SHA
    with pytest.raises(ReceiptError):
        require_full_sha("abc")
    with pytest.raises(ReceiptError):
        require_full_sha("A" * 39 + "g")


def test_conductor_line_is_full_sha_and_pass():
    line = conductor_line(sha=FULL_SHA, status="PASS", case="cross-network")
    assert line == (
        f"{CONDUCTOR_KIND} sha={FULL_SHA} status=PASS case=cross-network"
    )


def test_receipt_omits_invitation_material(tmp_path: Path):
    receipt = build_receipt(
        sha=FULL_SHA,
        case="same-lan",
        status="PASS",
        profiles=[
            {
                "profile": "art",
                "guest_connected": True,
                "session_state_exchanged": True,
                "harness_ok": True,
            }
        ],
        rendezvous_reachable_from_host=None,
        rendezvous_reachable_from_guest=None,
        invite_host_routable_from_guest=True,
        netem=False,
    )
    assert receipt["schema"] == SCHEMA
    assert receipt["git_sha"] == FULL_SHA
    assert receipt["port_forwarding"] is False
    assert not contains_secret_keys(receipt)
    path = write_receipt(tmp_path, receipt)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    pass_file = tmp_path / f"{FULL_SHA}-same-lan.PASS"
    assert pass_file.read_text(encoding="utf-8").strip() == receipt["conductor_line"]


def test_fail_receipt_does_not_write_pass_file(tmp_path: Path):
    receipt = build_receipt(
        sha=FULL_SHA,
        case="cross-network",
        status="FAIL",
        profiles=[
            {
                "profile": "art",
                "guest_connected": False,
                "session_state_exchanged": False,
                "harness_ok": True,
            }
        ],
        rendezvous_reachable_from_host=True,
        rendezvous_reachable_from_guest=True,
        invite_host_routable_from_guest=False,
        netem=True,
        notes="Expected until v3 Internet Host/Join ships.",
    )
    write_receipt(tmp_path, receipt)
    assert not (tmp_path / f"{FULL_SHA}-cross-network.PASS").exists()
    assert (tmp_path / f"{FULL_SHA}-cross-network.json").is_file()


def test_receipt_rejects_secret_keys_and_port_forwarding():
    with pytest.raises(ReceiptError):
        build_receipt(
            sha=FULL_SHA,
            case="same-lan",
            status="PASS",
            profiles=[{"profile": "art", "invite_token": "secret"}],
            rendezvous_reachable_from_host=None,
            rendezvous_reachable_from_guest=None,
            invite_host_routable_from_guest=True,
            netem=False,
        )
    with pytest.raises(ReceiptError):
        build_receipt(
            sha=FULL_SHA,
            case="same-lan",
            status="PASS",
            profiles=[],
            rendezvous_reachable_from_host=None,
            rendezvous_reachable_from_guest=None,
            invite_host_routable_from_guest=True,
            netem=False,
            port_forwarding=True,
        )


def test_cross_network_plan_masquerades_without_port_forward():
    spec = TopologySpec(case="cross-network", prefix="wjx1", netem=True)
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert "MASQUERADE" in text
    assert text.count("MASQUERADE") == 2
    assert "DNAT" not in text
    assert "REDIRECT" not in text
    delay = " ".join(
        " ".join(item) for item in netem_commands(spec)
    )
    assert f"delay {NETEM_DELAY} {NETEM_JITTER} loss {NETEM_LOSS}" in delay
    assert "203.0.113.1" in text
    assert "192.168.110.10" in text
    assert "192.168.120.10" in text


def test_same_lan_plan_has_no_nat_and_no_netem():
    spec = TopologySpec(case="same-lan", prefix="wjx1", netem=False)
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert "MASQUERADE" not in text
    assert "netem" not in text
    assert "192.168.110.10" in text
    assert "192.168.110.11" in text
    assert "203.0.113" not in text


def test_workflow_is_optional_and_path_filtered():
    workflow = Path(".github/workflows/cross-network-acceptance.yml")
    text = workflow.read_text(encoding="utf-8")
    assert "name: Cross-network acceptance" in text
    assert "workflow_dispatch:" in text
    assert "pull_request:" in text
    assert "core/network_invite.py" in text
    assert "core/session_transfer.py" in text
    assert "services/lan_room_guest.py" in text
    assert "transport/" in text
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "cross-network-acceptance" not in ci
    assert "Cross-network acceptance" not in ci
