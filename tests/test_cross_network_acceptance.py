"""Proof-only harness contracts. Never skip/xfail'd."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.cross_network_acceptance import CONDUCTOR_KIND, SCHEMA
from tools.cross_network_acceptance.nat_probe import (
    ProbeError, check_probe, expected_behavior, mapping_mode_ok, verify_mapping_mode,
)
from tools.cross_network_acceptance.proof import evaluate_proof
from tools.cross_network_acceptance.receipt import (
    ReceiptError, build_receipt, conductor_line, contains_secret_keys,
    require_full_sha, write_receipt,
)
from tools.cross_network_acceptance.topology import (
    NETEM_DELAY, NETEM_JITTER, NETEM_LOSS, RFC4787, TopologyError, TopologySpec,
    addrs, assert_no_port_forward, assert_router_drop, assert_unsolicited_inbound_udp_dropped,
    netem_commands, setup_commands,
)
from tools.cross_network_acceptance.turn import mint_credentials, relayed_bytes

FULL_SHA = "a" * 40
MUSIC, ART = "11" * 16, "22" * 16


def _proof(**kw):
    row = {
        "local_type": "srflx", "remote_type": "srflx",
        "music_nonce": MUSIC, "art_nonce": ART,
        "music_echo": MUSIC, "art_echo": ART,
    }
    row.update(kw)
    return row


def _receipt(**kw):
    defaults = dict(
        sha=FULL_SHA, case="same-lan", status="PASS",
        profiles=[{"role": "host", "local_type": "host", "remote_type": "host"}],
        rendezvous_reachable_from_host=None, rendezvous_reachable_from_guest=None,
        netem=False, kernel="6.x", expect_path="direct")
    defaults.update(kw)
    return build_receipt(**defaults)


def test_require_full_sha_rejects_short_and_dirty_values():
    assert require_full_sha(FULL_SHA) == FULL_SHA
    with pytest.raises(ReceiptError):
        require_full_sha("abc")
    with pytest.raises(ReceiptError):
        require_full_sha("A" * 39 + "g")


def test_conductor_line_is_full_sha_and_pass():
    line = conductor_line(sha=FULL_SHA, status="PASS", case="cone")
    assert line == f"{CONDUCTOR_KIND} sha={FULL_SHA} status=PASS case=cone"


def test_receipt_omits_invitation_material(tmp_path: Path):
    receipt = _receipt()
    assert receipt["schema"] == SCHEMA and receipt["git_sha"] == FULL_SHA
    assert receipt["port_forwarding"] is False and not contains_secret_keys(receipt)
    path = write_receipt(tmp_path, receipt)
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "PASS"
    assert (tmp_path / f"{FULL_SHA}-same-lan.PASS").read_text(
        encoding="utf-8").strip() == receipt["conductor_line"]


def test_fail_receipt_does_not_write_pass_file(tmp_path: Path):
    write_receipt(tmp_path, _receipt(case="cone", status="FAIL", expect_path="direct"))
    assert not (tmp_path / f"{FULL_SHA}-cone.PASS").exists()
    assert (tmp_path / f"{FULL_SHA}-cone.json").is_file()


def test_stale_pass_is_removed_at_start(tmp_path, monkeypatch):
    from tools.cross_network_acceptance import run as r
    write_receipt(tmp_path, _receipt(case="cone", status="PASS"))
    marker = tmp_path / f"{FULL_SHA}-cone.PASS"
    assert marker.is_file()
    seen: dict[str, bool] = {}

    def boom(_spec):
        seen["exists_during_setup"] = marker.exists()
        raise RuntimeError("topology down")

    monkeypatch.setattr(r, "apply_topology", boom)
    monkeypatch.setattr(r, "cleanup_topology", lambda spec: None)
    monkeypatch.setattr(r, "_chown_tree", lambda _p: None)
    monkeypatch.setattr(r, "_arm_timeout", lambda _s: None)
    monkeypatch.setattr(r, "_disarm_timeout", lambda: None)
    code = r.run_case(case="cone", output_dir=tmp_path, netem=False, sha=FULL_SHA, job_id="t1")
    assert code == 1
    assert seen.get("exists_during_setup") is False
    assert not marker.exists()


def test_receipt_rejects_secret_keys_and_port_forwarding():
    with pytest.raises(ReceiptError):
        _receipt(profiles=[{"role": "host", "credential": "secret"}])
    with pytest.raises(ReceiptError):
        _receipt(port_forwarding=True)


def test_job_ids_use_unique_prefixes_and_subnets():
    from tools.cross_network_acceptance.run import make_spec
    a = make_spec("cone", "run-1-cone", 0, False)
    b = make_spec("relay", "run-1-relay", 0, False)
    assert a.prefix != b.prefix and a.salt != b.salt
    assert addrs(a.salt)["inet"] != addrs(b.salt)["inet"]


def test_same_lan_plan_has_no_nat_and_no_netem():
    spec = TopologySpec(case="same-lan", prefix="wjx1", netem=False)
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert "SNAT" not in text and "MASQUERADE" not in text and "netem" not in text
    a = addrs(1)
    assert a["host"] in text and a["guest_lan"] in text


def test_cone_nat_plan_uses_explicit_snat_and_drop():
    spec = TopologySpec(case="cone", prefix="wjx1", nat_mode="cone")
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    assert_unsolicited_inbound_udp_dropped(commands)
    assert_router_drop(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert "MASQUERADE" not in text and "--random-fully" not in text
    assert "SNAT" in text and "--to-source" in text
    assert "REJECT" not in text and "ICMP" not in text
    assert "-P INPUT DROP" in text and "-P FORWARD DROP" in text
    assert RFC4787["cone"] == ("endpoint-independent", "address-and-port-dependent")
    delay = " ".join(" ".join(item) for item in netem_commands(
        TopologySpec(case="cone", prefix="wjx1", nat_mode="cone", netem=True)))
    assert f"delay {NETEM_DELAY} {NETEM_JITTER} loss {NETEM_LOSS}" in delay


def test_cone_nat_drops_unsolicited_inbound_udp_before_outbound():
    spec = TopologySpec(case="cone", prefix="wjx1", nat_mode="cone")
    assert_unsolicited_inbound_udp_dropped(setup_commands(spec))
    with pytest.raises(TopologyError):
        assert_unsolicited_inbound_udp_dropped([
            ["iptables", "-t", "nat", "-A", "PREROUTING", "-p", "udp", "-j", "NETMAP"],
        ])


def test_symmetric_nat_plan_randomizes_both_sides():
    spec = TopologySpec(case="relay", prefix="wjx1", nat_mode="symmetric")
    commands = setup_commands(spec)
    assert_router_drop(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert text.count("--random-fully") == 2
    assert RFC4787["symmetric"] == ("endpoint-dependent", "address-and-port-dependent")


def test_stun_binding_reports_xor_mapped_source():
    from tools.cross_network_acceptance.stun import binding_success, parse_xor_mapped
    request = b"\x00\x01\x00\x00\x21\x12\xa4\x42" + b"\x11" * 12
    assert parse_xor_mapped(binding_success(request, "27.1.0.10", 41234)) == (
        "27.1.0.10", 41234)


def test_stun_name_override_is_test_only_hosts_mapping():
    from tools.cross_network_acceptance.topology import stun_override_hosts_text
    body = stun_override_hosts_text("27.1.0.1")
    assert "27.1.0.1" in body and "stun.cloudflare.com" in body
    docs = Path("docs/CROSS-NETWORK-TEST.md").read_text(encoding="utf-8")
    readme = Path("tools/cross_network_acceptance/README.md").read_text(encoding="utf-8")
    assert "pion vnet" in readme.lower() and "hairpin" in readme.lower()
    assert "NOT RUN" in docs


def test_workflow_is_optional_and_path_filtered():
    workflow = Path(".github/workflows/cross-network-acceptance.yml").read_text(encoding="utf-8")
    for token in (
        "name: Cross-network acceptance", "workflow_dispatch:", "pull_request:",
        "tools/cross_network_acceptance/**", "deploy/relay/**", "probe-nat",
        "--case same-lan", "--case cone", "--case relay", "pcap", "conntrack",
        "build-sidecar", "webjam-fabric", "setup-go",
    ):
        assert token in workflow
    assert "--expected-status" not in workflow
    assert "continue-on-error" not in workflow
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "cross-network-acceptance" not in ci
    setup = Path("tools/cross_network_acceptance/ci_prep.sh").read_text(encoding="utf-8")
    assert "coturn" in setup and "tcpdump" in setup and "conntrack" in setup
    assert "modprobe sch_netem || true" not in setup


def test_path_labels_are_topology_not_ice_pairs():
    from tools.cross_network_acceptance.proof import path_label
    assert path_label("direct", same_lan=True) == "host"
    assert path_label("direct", same_lan=False) == "srflx"
    assert path_label("relay", same_lan=False) == "relay"


def test_missing_nonce_or_candidate_fields_fail():
    host, guest = _proof(), _proof()
    assert evaluate_proof(host, guest, expect_path="direct", music_nonce=MUSIC, art_nonce=ART) == ""
    missing = dict(host)
    missing.pop("music_echo")
    assert evaluate_proof(missing, guest, expect_path="direct", music_nonce=MUSIC, art_nonce=ART)
    blank = _proof(local_type="")
    assert evaluate_proof(blank, guest, expect_path="direct", music_nonce=MUSIC, art_nonce=ART)
    unknown = _proof(local_type="unknown")
    assert evaluate_proof(unknown, guest, expect_path="direct", music_nonce=MUSIC, art_nonce=ART)
    bad_nonce = _proof(music_echo="00" * 16)
    assert evaluate_proof(bad_nonce, guest, expect_path="direct", music_nonce=MUSIC, art_nonce=ART)


def test_relay_pair_on_direct_job_fails_and_vice_versa():
    host, guest = _proof(), _proof()
    relay_host = _proof(local_type="relay", remote_type="relay")
    relay_guest = _proof(local_type="relay", remote_type="srflx")
    assert evaluate_proof(
        relay_host, relay_guest, expect_path="direct", music_nonce=MUSIC, art_nonce=ART,
    ) == "direct path used relay"
    assert evaluate_proof(
        host, guest, expect_path="relay", music_nonce=MUSIC, art_nonce=ART, relay_bytes=40,
    ) == "relay path used direct"
    assert evaluate_proof(
        relay_host, relay_guest, expect_path="relay", music_nonce=MUSIC, art_nonce=ART,
        relay_bytes=0,
    ) == "relay carried no bytes"
    assert evaluate_proof(
        relay_host, guest, expect_path="relay", music_nonce=MUSIC, art_nonce=ART, relay_bytes=12,
    ) == ""


def test_exception_leaves_no_pass(tmp_path, monkeypatch):
    from tools.cross_network_acceptance import run as r
    marker = tmp_path / f"{FULL_SHA}-same-lan.PASS"
    marker.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(r, "apply_topology", lambda spec: (_ for _ in ()).throw(RuntimeError("netns")))
    monkeypatch.setattr(r, "cleanup_topology", lambda spec: None)
    monkeypatch.setattr(r, "_chown_tree", lambda _p: None)
    monkeypatch.setattr(r, "_arm_timeout", lambda _s: None)
    monkeypatch.setattr(r, "_disarm_timeout", lambda: None)
    code = r.run_case(case="same-lan", output_dir=tmp_path, netem=False, sha=FULL_SHA, job_id="x")
    assert code == 1
    assert not marker.exists()
    assert not (tmp_path / f"{FULL_SHA}-same-lan.PASS").exists()


def test_requested_netem_and_missing_binary_fail_closed(tmp_path, monkeypatch):
    from tools.cross_network_acceptance.sidecar import SidecarError, require_transport_binary
    from tools.cross_network_acceptance import run as r
    monkeypatch.setenv("WEBJAM_TRANSPORT_BINARY", str(tmp_path / "missing-bin"))
    with pytest.raises(SidecarError):
        require_transport_binary()
    monkeypatch.delenv("WEBJAM_TRANSPORT_BINARY")
    topo = SimpleNamespace(
        spec=TopologySpec(case="cone", prefix="wjx1", nat_mode="cone"),
        host_ns="h", guest_ns="g", inet_ns="i", inet_addr="27.1.0.1",
        host_addr="10.1.10.10", guest_addr="10.1.20.10",
        nat_a_wan="27.1.0.10", nat_b_wan="27.1.0.20",
    )
    monkeypatch.setattr(r, "apply_topology", lambda spec: topo)
    monkeypatch.setattr(r, "cleanup_topology", lambda spec: None)
    monkeypatch.setattr(r, "_chown_tree", lambda _p: None)
    monkeypatch.setattr(r, "_start_pcap", lambda *_a, **_k: [])
    monkeypatch.setattr(r, "_mapping_ports", lambda *_a, **_k: (41000, 41000))
    monkeypatch.setattr(r, "apply_netem", lambda _s: False)
    monkeypatch.setattr(r, "_arm_timeout", lambda _s: None)
    monkeypatch.setattr(r, "_disarm_timeout", lambda: None)
    monkeypatch.setattr(r, "_copy_logs", lambda *_a, **_k: None)
    monkeypatch.setattr(r, "_stop_procs", lambda _p: None)
    monkeypatch.setattr(r, "_capture_router_state", lambda *_a, **_k: None)
    monkeypatch.setattr(r, "ns_popen", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(r, "_pause", lambda _s: None)
    assert r.run_case(case="cone", output_dir=tmp_path, netem=True, sha=FULL_SHA, job_id="n") == 1
    assert not (tmp_path / f"{FULL_SHA}-cone.PASS").exists()


def test_connectivity_assertion_is_not_retried(tmp_path, monkeypatch):
    from tools.cross_network_acceptance import run as r
    calls = {"n": 0}
    topo = SimpleNamespace(
        spec=TopologySpec(case="cone", prefix="wjx1", nat_mode="cone"),
        host_ns="h", guest_ns="g", inet_ns="i", inet_addr="27.1.0.1",
        host_addr="10.1.10.10", guest_addr="10.1.20.10",
        nat_a_wan="27.1.0.10", nat_b_wan="27.1.0.20",
    )

    def apply(spec):
        calls["n"] += 1
        return topo

    monkeypatch.setattr(r, "apply_topology", apply)
    monkeypatch.setattr(r, "cleanup_topology", lambda spec: None)
    monkeypatch.setattr(r, "_chown_tree", lambda _p: None)
    monkeypatch.setattr(r, "_start_pcap", lambda *_a, **_k: [])
    monkeypatch.setattr(r, "_mapping_ports", lambda *_a, **_k: (41000, 41000))
    monkeypatch.setattr(r, "_arm_timeout", lambda _s: None)
    monkeypatch.setattr(r, "_disarm_timeout", lambda: None)
    monkeypatch.setattr(r, "_copy_logs", lambda *_a, **_k: None)
    monkeypatch.setattr(r, "_stop_procs", lambda _p: None)
    monkeypatch.setattr(r, "_capture_router_state", lambda *_a, **_k: None)
    monkeypatch.setattr(r, "_pause", lambda _s: None)
    monkeypatch.setattr(r, "ns_popen", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(r, "_wait_port", lambda *_a, **_k: None)
    monkeypatch.setattr(r, "_reap", lambda *_a, **_k: 1)
    monkeypatch.setattr(r, "_wait_json", lambda *_a, **_k: {})
    assert r.run_case(case="cone", output_dir=tmp_path, netem=False, sha=FULL_SHA, job_id="once") == 1
    assert calls["n"] == 1
    assert not (tmp_path / f"{FULL_SHA}-cone.PASS").exists()


def test_dual_stun_mapping_mismatch_is_setup_error():
    assert mapping_mode_ok("cone", 41000, 41000)
    assert not mapping_mode_ok("cone", 41000, 41001)
    assert mapping_mode_ok("symmetric", 41000, 41001)
    assert not mapping_mode_ok("symmetric", 41000, 41000)
    with pytest.raises(ProbeError):
        verify_mapping_mode("cone", 41000, 41111)
    with pytest.raises(ProbeError):
        verify_mapping_mode("symmetric", 41000, 41000)


def test_nat_probe_fails_on_subprocess_or_missing_mapping(monkeypatch):
    import subprocess
    import sys
    from tools.cross_network_acceptance.nat_probe import _exec, _read
    cone = {"unsolicited_dropped": True, "punch_admitted": True, "mapping_reused": True}
    mapped = {"host_mapped": 41234, "guest_mapped": 42000,
              "observed_ports": [41234, 41234], "mapped_addr": "27.1.0.10"}
    assert expected_behavior("cone") == cone
    assert expected_behavior("symmetric") == {**cone, "mapping_reused": False}
    assert check_probe({"nat_mode": "cone", **cone, **mapped})
    assert not check_probe({"nat_mode": "cone", **cone, **mapped, "unsolicited_dropped": False})
    assert check_probe({
        "nat_mode": "symmetric", "unsolicited_dropped": True, "punch_admitted": True,
        "mapping_reused": False, "host_mapped": 41234, "guest_mapped": 42000,
        "observed_ports": [41234, 41999], "mapped_addr": "27.1.0.10",
    })
    assert not check_probe({
        "nat_mode": "symmetric", "unsolicited_dropped": True, "punch_admitted": True,
        "mapping_reused": True, "host_mapped": 41234, "guest_mapped": 42000,
        "observed_ports": [41234, 41234], "mapped_addr": "27.1.0.10",
    })
    monkeypatch.setattr("tools.cross_network_acceptance.nat_probe.required_bin", lambda _n: "/bin/false")
    with pytest.raises(ProbeError):
        _exec("ns", "print(1)", [])
    proc = subprocess.Popen([sys.executable, "-c", "print('not-json')"], stdout=subprocess.PIPE, text=True)
    with pytest.raises(ProbeError):
        _read(proc)


def test_turn_credentials_contract_and_relay_byte_logs(monkeypatch):
    import time

    from deploy.relay import turn_credentials as tc
    from tools.cross_network_acceptance.turn import REALM

    monkeypatch.setattr(tc, "SECRET", b"s3cret")
    monkeypatch.setattr(tc, "EXTERNAL_IP", "27.1.0.1")
    monkeypatch.setattr(tc, "LISTEN_PORT", 3478)
    minted = tc.mint_coturn(time.time())
    assert minted["username"].endswith(":webjam")
    assert minted["urls"][0] == "turn:27.1.0.1:3478?transport=udp"
    assert minted["credential"] and minted["allowed_relay_ips"] == ["27.1.0.1"]
    also = mint_credentials("s3cret", "27.1.0.1", 3478)
    assert also["urls"] == minted["urls"] and also["credential"]
    assert REALM == "webjam"
    assert tc.valid_session_invite_shape({
        "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "invite_token": "A" * 32,
    })
    assert not tc.valid_session_invite_shape({"session_id": "x", "invite_token": "y"})
    assert relayed_bytes("session x usage: rp=2, rb=80, sp=2, sb=40") == 120
    assert relayed_bytes("incoming packet Data from peer") >= 8
    assert relayed_bytes("idle") == 0
    assert "mint_coturn" in Path(
        "tools/cross_network_acceptance/turn.py").read_text(encoding="utf-8")


def test_stun_server_prefers_coturn(monkeypatch):
    from tools.cross_network_acceptance.stun import python_stun_command, stun_server_commands
    monkeypatch.setattr(
        "tools.cross_network_acceptance.stun.shutil.which",
        lambda name: "/usr/bin/turnserver" if name == "turnserver" else None,
    )
    commands = stun_server_commands("27.1.0.1", (3478, 19302))
    assert len(commands) == 2 and "--stun-only" in commands[0]
    fallback = python_stun_command("27.1.0.1", (3478, 19302))
    assert fallback[-2:] == ["3478", "19302"]


def test_build_sidecar_resolves_relative_output_against_cwd(tmp_path, monkeypatch):
    from tools.cross_network_acceptance.sidecar import build_sidecar
    product = tmp_path / "pr-372"
    fabric = product / "transport" / "cmd" / "webjam-fabric"
    fabric.mkdir(parents=True)
    (fabric / "main.go").write_text("package main\n", encoding="utf-8")
    repo_cwd = tmp_path / "harness-repo"
    repo_cwd.mkdir()
    monkeypatch.chdir(repo_cwd)

    def fake_run(argv, **kwargs):
        argv = list(argv)
        if argv[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="b" * 40 + "\n", stderr="")
        if argv and argv[0] == "go":
            out = Path(argv[argv.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"ok")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr("tools.cross_network_acceptance.sidecar.subprocess.run", fake_run)
    relative = Path("out/cross-network-acceptance/webjam-fabric")
    built = build_sidecar(product, relative)
    assert built == (repo_cwd / relative).resolve() and built.is_file()


def test_cli_has_no_expected_status_fail():
    text = Path("tools/cross_network_acceptance/__main__.py").read_text(encoding="utf-8")
    assert "--expected-status" not in text
    assert "honest_unreachable" not in Path("tools/cross_network_acceptance/run.py").read_text(
        encoding="utf-8")
    assert not Path("tools/cross_network_acceptance/roles.py").exists()


def test_guest_holds_sidecar_until_delayed_host_proof(tmp_path):
    import threading
    import time

    from tools.cross_network_acceptance import proof as p

    host = tmp_path / "host.proof"

    def writer():
        time.sleep(0.08)
        host.write_text("ready\n", encoding="utf-8")

    threading.Thread(target=writer, daemon=True).start()
    started = time.monotonic()
    assert p._wait_text(host, 1.0) == "ready"
    assert time.monotonic() - started >= 0.05
    source = Path("tools/cross_network_acceptance/proof.py").read_text(encoding="utf-8")
    send_at = source.rfind("process.send_help")
    wait_at = source.find('_wait_text(work_dir / "host.proof"')
    stop_at = source.rfind("process.stop()")
    assert 0 <= send_at < wait_at < stop_at


def test_proof_peers_drive_real_sidecar_not_standalone_ice():
    proof = Path("tools/cross_network_acceptance/proof.py").read_text(encoding="utf-8")
    run = Path("tools/cross_network_acceptance/run.py").read_text(encoding="utf-8")
    shim = Path("tools/cross_network_acceptance/shim.py").read_text(encoding="utf-8")
    readme = Path("tools/cross_network_acceptance/README.md").read_text(encoding="utf-8")
    assert "TransportProcess" in proof and "issue_remote_invitation" in proof
    assert "TurnClient" not in proof and "KIND_MUSIC" not in proof
    assert "host.proof" in proof
    assert "webjam_reference" in run and "mint_coturn" not in proof
    assert "credential_script" in run and "_post_in_ns" in run
    assert "inner.connect(" not in shim and "_udp_loop" in shim
    assert "transport/internal/ipc" in readme and "POST /turn-credentials" in readme
    assert "reference-local" in readme and "directstun" in readme
