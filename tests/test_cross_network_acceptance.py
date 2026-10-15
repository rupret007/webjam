"""Receipt, topology, and CLI contracts. Never skip/xfail'd."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.cross_network_acceptance import CONDUCTOR_KIND, SCHEMA
from tools.cross_network_acceptance.receipt import (
    ReceiptError, build_receipt, conductor_line, contains_secret_keys,
    require_full_sha, write_receipt,
)
from tools.cross_network_acceptance.run import (
    _run_profile, completed_guest_result, discarded_test_net3_hides_nat_type,
    honest_product_fail_notes, job_exit_code,
)
from tools.cross_network_acceptance.topology import (
    HOST_ADDR, NETEM_DELAY, NETEM_JITTER, NETEM_LOSS, RFC4787, TopologyError,
    TopologySpec, assert_no_port_forward, assert_unsolicited_inbound_udp_dropped,
    netem_commands, setup_commands,
)

FULL_SHA = "a" * 40


def _honest(**kw):
    row = {
        "guest_connected": False, "session_state_exchanged": False,
        "failure_class": "honest_unreachable", "attempt_finished": True,
    }
    row.update(kw)
    return row


def _job(ok=False, **kw):
    return job_exit_code(status="FAIL", expected_status="FAIL", harness_ok=ok, **kw)


def _receipt(**kw):
    defaults = dict(
        sha=FULL_SHA, case="cross-network", status="FAIL",
        profiles=[{"profile": "art", "guest_connected": False,
                   "session_state_exchanged": False, "harness_ok": True}],
        rendezvous_reachable_from_host=True, rendezvous_reachable_from_guest=True,
        invite_host_routable_from_guest=False, netem=False)
    defaults.update(kw)
    return build_receipt(**defaults)


def test_require_full_sha_rejects_short_and_dirty_values():
    assert require_full_sha(FULL_SHA) == FULL_SHA
    with pytest.raises(ReceiptError):
        require_full_sha("abc")
    with pytest.raises(ReceiptError):
        require_full_sha("A" * 39 + "g")


def test_conductor_line_is_full_sha_and_pass():
    line = conductor_line(sha=FULL_SHA, status="PASS", case="cross-network")
    assert line == f"{CONDUCTOR_KIND} sha={FULL_SHA} status=PASS case=cross-network"


def test_receipt_omits_invitation_material(tmp_path: Path):
    receipt = _receipt(
        case="same-lan", status="PASS", invite_host_routable_from_guest=True,
        profiles=[{"profile": "art", "guest_connected": True,
                   "session_state_exchanged": True, "harness_ok": True}],
        rendezvous_reachable_from_host=None, rendezvous_reachable_from_guest=None,
    )
    assert receipt["schema"] == SCHEMA and receipt["git_sha"] == FULL_SHA
    assert receipt["port_forwarding"] is False and not contains_secret_keys(receipt)
    path = write_receipt(tmp_path, receipt)
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "PASS"
    assert (tmp_path / f"{FULL_SHA}-same-lan.PASS").read_text(
        encoding="utf-8").strip() == receipt["conductor_line"]


def test_fail_receipt_does_not_write_pass_file(tmp_path: Path):
    receipt = _receipt(netem=True, notes="Expected until v3.")
    write_receipt(tmp_path, receipt)
    assert not (tmp_path / f"{FULL_SHA}-cross-network.PASS").exists()
    assert (tmp_path / f"{FULL_SHA}-cross-network.json").is_file()


def test_later_fail_removes_stale_pass_file(tmp_path: Path):
    write_receipt(tmp_path, _receipt(
        status="PASS", invite_host_routable_from_guest=True,
        profiles=[{"profile": "art", "guest_connected": True,
                   "session_state_exchanged": True, "harness_ok": True}],
    ))
    pass_file = tmp_path / f"{FULL_SHA}-cross-network.PASS"
    assert pass_file.is_file()
    write_receipt(tmp_path, _receipt(notes="Later FAIL"))
    assert not pass_file.exists()
    payload = json.loads((tmp_path / f"{FULL_SHA}-cross-network.json").read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"


def test_receipt_rejects_secret_keys_and_port_forwarding():
    lan = dict(case="same-lan", status="PASS", invite_host_routable_from_guest=True,
               rendezvous_reachable_from_host=None, rendezvous_reachable_from_guest=None)
    with pytest.raises(ReceiptError):
        _receipt(**lan, profiles=[{"profile": "art", "invite_token": "secret"}])
    with pytest.raises(ReceiptError):
        _receipt(**lan, profiles=[], port_forwarding=True)


def test_cross_network_plan_masquerades_without_port_forward():
    spec = TopologySpec(case="cross-network", prefix="wjx1", netem=True)
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert text.count("MASQUERADE") == 2
    assert "NETMAP" not in text and "--random-fully" not in text
    assert "DNAT" not in text and "REDIRECT" not in text
    delay = " ".join(" ".join(item) for item in netem_commands(spec))
    assert f"delay {NETEM_DELAY} {NETEM_JITTER} loss {NETEM_LOSS}" in delay
    assert "203.0.113.1" in text and "192.168.110.10" in text and "192.168.120.10" in text


def test_same_lan_plan_has_no_nat_and_no_netem():
    spec = TopologySpec(case="same-lan", prefix="wjx1", netem=False)
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert "MASQUERADE" not in text and "netem" not in text
    assert "192.168.110.10" in text and "192.168.110.11" in text and "203.0.113" not in text


def test_workflow_is_optional_and_path_filtered():
    workflow = Path(".github/workflows/cross-network-acceptance.yml")
    text = workflow.read_text(encoding="utf-8")
    for token in (
        "name: Cross-network acceptance", "workflow_dispatch:", "pull_request:",
        "core/network_invite.py", "core/session_transfer.py",
        "services/lan_room_guest.py", "transport/", "push:", "ci_prep.sh",
        "chown -R", "--expected-status PASS", "--expected-status FAIL",
        "continue-on-error:", "github.ref == 'refs/heads/master'",
        "classified failure-to-connect", "connected-without-state-exchange",
        "fake connect", "Prove RFC 4787 NAT packet behavior",
    ):
        assert token in text
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "cross-network-acceptance" not in ci and "Cross-network acceptance" not in ci
    setup = Path("tools/cross_network_acceptance/ci_prep.sh").read_text(encoding="utf-8")
    assert "coturn" in setup and "linux-modules-extra" in setup
    assert "modprobe sch_netem" in setup and "ip netns add" in setup
    assert "modprobe sch_netem || true" not in setup
    same_lan, two_nat = text.split("cross-network:", 1)
    assert "continue-on-error" not in same_lan and "--expected-status FAIL" not in same_lan
    assert "probe-nat" in same_lan
    assert "--expected-status FAIL" in two_nat and "continue-on-error:" in two_nat


def test_cone_nat_plan_uses_masquerade_without_static_forward():
    spec = TopologySpec(case="cross-network", prefix="wjx1", nat_mode="cone")
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    assert_unsolicited_inbound_udp_dropped(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert text.count("MASQUERADE") == 2
    assert "NETMAP" not in text and "--random-fully" not in text
    assert "DNAT" not in text and "--dport" not in text
    assert text.count("ESTABLISHED,RELATED") == 2
    assert RFC4787["cone"] == ("endpoint-independent", "address-and-port-dependent")
    assert RFC4787["masquerade"] == RFC4787["cone"]
    from tools.cross_network_acceptance.nat_probe import check_probe, expected_behavior
    cone = {"unsolicited_dropped": True, "punch_admitted": True, "mapping_reused": True}
    assert expected_behavior("cone") == expected_behavior("masquerade") == cone
    assert expected_behavior("symmetric") == {**cone, "mapping_reused": False}
    mapped = {"host_mapped": 41234, "guest_mapped": 42000,
              "observed_ports": [41234, 41234], "mapped_addr": "203.0.113.10"}
    assert check_probe({"nat_mode": "cone", **cone, **mapped})
    assert not check_probe({"nat_mode": "cone", **cone, **mapped, "unsolicited_dropped": False})
    assert not check_probe({"nat_mode": "symmetric", **cone, **mapped})
    assert "reply-from-contacted-endpoint" in Path(
        "tools/cross_network_acceptance/nat_probe.py").read_text(encoding="utf-8")


def test_cone_nat_drops_unsolicited_inbound_udp_before_outbound():
    spec = TopologySpec(case="cross-network", prefix="wjx1", nat_mode="cone")
    commands = setup_commands(spec)
    assert_unsolicited_inbound_udp_dropped(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert "NETMAP" not in text and "DNAT" not in text
    with pytest.raises(TopologyError):
        assert_unsolicited_inbound_udp_dropped([
            ["iptables", "-t", "nat", "-A", "PREROUTING", "-p", "udp", "-j",
             "NETMAP", "--to", f"{HOST_ADDR}/32"],
            ["iptables", "-A", "FORWARD", "-p", "udp", "-j", "ACCEPT"],
        ])


def test_symmetric_nat_plan_randomizes_host_mapping_only():
    spec = TopologySpec(case="cross-network", prefix="wjx1", nat_mode="symmetric")
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    assert_unsolicited_inbound_udp_dropped(commands)
    text = " ".join(" ".join(item) for item in commands)
    assert text.count("--random-fully") == 1 and "NETMAP" not in text and "DNAT" not in text
    assert RFC4787["symmetric"] == ("endpoint-dependent", "address-and-port-dependent")


def test_stun_binding_reports_xor_mapped_source():
    from tools.cross_network_acceptance.stun import binding_success, parse_xor_mapped
    request = b"\x00\x01\x00\x00\x21\x12\xa4\x42" + b"\x11" * 12
    assert parse_xor_mapped(binding_success(request, "203.0.113.10", 41234)) == (
        "203.0.113.10", 41234)


def test_stun_name_override_is_test_only_hosts_mapping():
    from tools.cross_network_acceptance.topology import INET_ADDR, stun_override_hosts_text
    body = stun_override_hosts_text()
    assert INET_ADDR in body and "stun.cloudflare.com" in body and "stun.l.google.com" in body
    docs = Path("docs/CROSS-NETWORK-TEST.md").read_text(encoding="utf-8")
    assert "test-only" in docs.lower() or "Test-only" in docs
    assert "never" in docs.lower() and "packaged" in docs.lower()


def test_job_exit_code_keeps_fail_assertions():
    ok = dict(status="FAIL", expected_status="FAIL", harness_ok=True)
    assert job_exit_code(**ok) == 0
    assert job_exit_code(**{**ok, "harness_ok": False}) == 1
    assert job_exit_code(status="PASS", expected_status="FAIL", harness_ok=True) == 1
    assert job_exit_code(status="PASS", expected_status="PASS", harness_ok=True) == 0
    assert job_exit_code(status="FAIL", expected_status="PASS", harness_ok=True) == 1
    assert job_exit_code(status="PASS", expected_status="PASS", harness_ok=False) == 1


def test_completed_guest_result_rejects_crash_after_result_file():
    after_write = _honest()
    assert completed_guest_result(after_write, timed_out=False, returncode=1) is True
    for crash_code in (-11, -6, 139):
        ok = completed_guest_result(after_write, timed_out=False, returncode=crash_code)
        assert ok is False and _job(ok) == 1


def test_completed_guest_result_rejects_programming_and_startup_failures():
    from core.session_transfer import SessionTransferError

    from tools.cross_network_acceptance.roles import NETWORK_CONNECT_EXCEPTIONS

    honest = _honest(reason="unreachable")
    assert completed_guest_result(honest, timed_out=False, returncode=1) is True
    startup = {"guest_connected": False, "reason": "startup_refused",
               "failure_class": "startup_failure"}
    assert completed_guest_result(startup, timed_out=False, returncode=1) is False
    assert completed_guest_result(
        {"guest_connected": False, "reason": "ingress_rejected",
         "failure_class": "startup_failure"}, timed_out=False, returncode=1) is False
    assert completed_guest_result(
        {"guest_connected": False, "failure_class": "unknown"},
        timed_out=False, returncode=1) is False
    for code in (2, None, 127):
        assert completed_guest_result(honest, timed_out=False, returncode=code) is False
    assert not isinstance(TypeError("broken guest"), NETWORK_CONNECT_EXCEPTIONS)
    assert not isinstance(ValueError("bad args"), NETWORK_CONNECT_EXCEPTIONS)
    assert isinstance(OSError("connection refused"), NETWORK_CONNECT_EXCEPTIONS)
    assert isinstance(SessionTransferError("no route"), NETWORK_CONNECT_EXCEPTIONS)
    import errno
    from tools.cross_network_acceptance.roles import guest_failure_from_exception
    busy = guest_failure_from_exception(OSError(errno.EADDRINUSE, "busy"))
    assert busy["failure_class"] != "honest_unreachable" and not busy["attempt_finished"]
    assert not completed_guest_result({"guest_connected": False, **busy}, timed_out=False, returncode=1)
    source = Path("tools/cross_network_acceptance/roles.py").read_text(encoding="utf-8")
    assert "except NETWORK_CONNECT_EXCEPTIONS:" in source
    assert "ValueError, TypeError" not in source and ", TypeError):" not in source
    assert _job(completed_guest_result(startup, timed_out=False, returncode=1)) == 1


def test_build_sidecar_resolves_relative_output_against_cwd(tmp_path, monkeypatch):
    from tools.cross_network_acceptance.sidecar import build_sidecar
    product = tmp_path / "pr-372"
    fabric = product / "transport" / "cmd" / "webjam-fabric"
    fabric.mkdir(parents=True)
    (fabric / "main.go").write_text("package main\n", encoding="utf-8")
    docs = Path("docs/CROSS-NETWORK-TEST.md").read_text(encoding="utf-8")
    assert "SIDECAR=out/cross-network-acceptance/webjam-fabric" in docs
    assert '--output "$SIDECAR"' in docs
    repo_cwd = tmp_path / "harness-repo"
    repo_cwd.mkdir()
    monkeypatch.chdir(repo_cwd)

    def fake_run(argv, **kwargs):
        argv = list(argv)
        if argv[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="b" * 40 + "\n", stderr="")
        if argv and argv[0] == "go":
            assert Path(kwargs.get("cwd")) == product / "transport"
            out = Path(argv[argv.index("-o") + 1])
            assert out.is_absolute() and product / "transport" not in out.parents
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"ok")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr("tools.cross_network_acceptance.sidecar.subprocess.run", fake_run)
    relative = Path("out/cross-network-acceptance/webjam-fabric")
    built = build_sidecar(product, relative)
    assert built == (repo_cwd / relative).resolve() and built.is_file()
    assert not (product / "transport" / relative).exists()


def test_completed_guest_result_rejects_connected_without_state_exchange():
    partial = {"guest_connected": True, "session_state_exchanged": False}
    ok = completed_guest_result(partial, timed_out=False, returncode=1)
    assert ok is False and _job(ok) == 1
    unlabeled = {"guest_connected": False, "session_state_exchanged": False}
    assert completed_guest_result(unlabeled, timed_out=False, returncode=1) is False
    success = {"guest_connected": True, "session_state_exchanged": True}
    assert completed_guest_result(success, timed_out=False, returncode=0) is True
    assert completed_guest_result(success, timed_out=False, returncode=1) is False
    assert completed_guest_result(_honest(), timed_out=False, returncode=1) is True
    roles = Path("tools/cross_network_acceptance/roles.py").read_text(encoding="utf-8")
    assert roles.count('failure_class="" if connected else "honest_unreachable"') == 1


def test_transport_connected_without_state_exchange_is_not_honest_unreachable():
    from services.remote_session_runtime import RemoteSessionPhase
    from tools.cross_network_acceptance.roles import (
        _native_guest_connected, _native_state_exchanged, classify_native_guest_failure,
    )
    app = SimpleNamespace(
        _remote_session=SimpleNamespace(
            snapshot=SimpleNamespace(phase=RemoteSessionPhase.CONNECTED)),
        _room_participant=SimpleNamespace(
            state=SimpleNamespace(value="failed"),
            native_state=SimpleNamespace(creator_profile_key=""), borrowed_start=""),
    )
    assert _native_guest_connected(app, "music") is True
    assert _native_state_exchanged(app, "music") is False
    klass = classify_native_guest_failure(
        transport_connected=True, state_exchanged=False,
        failure_text="couldn't reach the host", room_failed=True,
    )
    assert klass == "state_exchange_failure" and klass != "honest_unreachable"
    ok = completed_guest_result(
        {"guest_connected": True, "session_state_exchanged": False, "failure_class": klass},
        timed_out=False, returncode=1,
    )
    assert ok is False and _job(ok) == 1


class _FakeRoleProc:
    def __init__(self, returncode: int | None, *, already_exited: bool = False):
        self._final_rc = 0 if returncode is None else returncode
        self.returncode = self._final_rc if already_exited else None
        self._exited = already_exited
        self._stderr_handle = None

    def poll(self):
        return self._final_rc if self._exited else None

    def wait(self, timeout=None):
        self._exited = True
        self.returncode = self._final_rc
        return self.returncode

    def kill(self):
        self._exited = True
        self._final_rc = self.returncode = -9


def _run_profile_row(tmp_path, monkeypatch, host, guest, result=None):
    honest = result or _honest(transport="lan")
    topology = SimpleNamespace(
        host_ns="h", guest_ns="g", spec=SimpleNamespace(nat_mode="masquerade"))
    procs = {"host": host, "guest": guest}
    run = "tools.cross_network_acceptance.run"
    monkeypatch.setattr(
        f"{run}.ns_popen", lambda _ns, argv, **_k: procs[argv[argv.index("--role") + 1]])
    monkeypatch.setattr(f"{run}._wait_json", lambda path, _t: (
        {"ok": True, "transport": "lan"} if Path(path).name == "host.ready" else dict(honest)))
    monkeypatch.setattr(f"{run}._invite_host_routable", lambda *_a, **_k: False)
    monkeypatch.setattr(f"{run}._chown_tree", lambda _p: None)
    return _run_profile(topology, "art", tmp_path / "art", {}, tmp_path)


def test_run_profile_rejects_host_crash_during_guest(tmp_path, monkeypatch):
    crashed = _run_profile_row(
        tmp_path, monkeypatch, _FakeRoleProc(139, already_exited=True), _FakeRoleProc(1),
    )
    assert crashed["harness_ok"] is False
    assert crashed["reason"] == "host_crash" and crashed["host_returncode"] == 139
    assert _job(False) == 1
    alive = _run_profile_row(
        tmp_path, monkeypatch, _FakeRoleProc(None), _FakeRoleProc(1, already_exited=True),
    )
    assert alive["harness_ok"] is True and alive["host_returncode"] is None
    assert _job(True) == 0


def test_run_profile_rejects_host_crash_after_liveness_snapshot(tmp_path, monkeypatch):
    class LateCrash(_FakeRoleProc):
        def wait(self, timeout=None):
            self._exited = True
            self._final_rc = self.returncode = 139
            return 139

    row = _run_profile_row(
        tmp_path, monkeypatch, LateCrash(None), _FakeRoleProc(1, already_exited=True),
    )
    assert row["harness_ok"] is False
    assert row["reason"] == "host_crash" and row["host_returncode"] == 139
    assert _job(False) == 1


def test_job_exit_code_rejects_incomplete_or_fake_expected_fail():
    honest = _honest()
    assert completed_guest_result(honest, timed_out=False, returncode=1) is True
    assert completed_guest_result(honest, timed_out=True, returncode=1) is False
    assert completed_guest_result({}, timed_out=False, returncode=1) is False
    assert completed_guest_result(honest, timed_out=False, returncode=None) is False
    assert completed_guest_result({"ok": False}, timed_out=False, returncode=1) is False
    assert _job(False) == 1
    assert _job(True, fake_connect=True) == _job(False, fake_connect=True) == 1


def test_stun_server_prefers_coturn(monkeypatch):
    from tools.cross_network_acceptance.stun import python_stun_command, stun_server_commands
    monkeypatch.setattr(
        "tools.cross_network_acceptance.stun.shutil.which",
        lambda name: "/usr/bin/turnserver" if name == "turnserver" else None,
    )
    commands = stun_server_commands("203.0.113.1", (3478, 19302))
    assert len(commands) == 2 and commands[0][0] == "/usr/bin/turnserver"
    assert "--stun-only" in commands[0] and "--listening-port=3478" in commands[0]
    assert "--listening-port=19302" in commands[1]
    fallback = python_stun_command("203.0.113.1", (3478, 19302))
    assert fallback[-2:] == ["3478", "19302"]
    assert "tools.cross_network_acceptance.stun" in fallback
    monkeypatch.setattr("tools.cross_network_acceptance.stun.shutil.which", lambda _n: None)
    monkeypatch.setattr("tools.cross_network_acceptance.stun.Path.is_file", lambda _s: False)
    python_only = stun_server_commands("203.0.113.1", (3478, 19302))
    assert len(python_only) == 1
    assert "tools.cross_network_acceptance.stun" in python_only[0]


def test_cone_and_symmetric_receipt_cases_are_allowed(tmp_path: Path):
    for case in ("cross-network-cone", "cross-network-symmetric"):
        receipt = _receipt(
            case=case, nat_mode="cone" if "cone" in case else "symmetric",
            profiles=[{"profile": "art", "guest_connected": False,
                       "session_state_exchanged": False, "harness_ok": True,
                       "transport": "direct-stun", "failure_class": "honest_unreachable",
                       "fake_connect": False}],
            stun_reachable_from_host=True, stun_reachable_from_guest=True)
        assert receipt["case"] == case
        write_receipt(tmp_path, receipt)
        assert (tmp_path / f"{FULL_SHA}-{case}.json").is_file()
        assert not (tmp_path / f"{FULL_SHA}-{case}.PASS").exists()


def test_test_net3_notes_do_not_claim_nat_type_proof():
    assert discarded_test_net3_hides_nat_type("cone") is discarded_test_net3_hides_nat_type("symmetric") is True
    assert discarded_test_net3_hides_nat_type("masquerade") is False
    cone = honest_product_fail_notes(case="cross-network", nat_mode="cone")
    symmetric = honest_product_fail_notes(case="cross-network", nat_mode="symmetric")
    masquerade = honest_product_fail_notes(case="cross-network", nat_mode="masquerade")
    for notes in (cone, symmetric):
        assert "does not prove" in notes and "TEST-NET-3" in notes
        assert "random port mapping" not in notes
        assert "cannot complete packaged direct-stun without a relay" not in notes
    assert "does not prove" not in masquerade
    docs = Path("docs/CROSS-NETWORK-TEST.md").read_text(encoding="utf-8")
    topology = Path("tools/cross_network_acceptance/topology.py").read_text(encoding="utf-8")
    for token in (
        "do **not** prove product cone-versus-symmetric", "endpoint-independent",
        "address-and-port-dependent", "probe-nat", "stale `.PASS`",
    ):
        assert token in docs
    assert "do not prove product cone-vs-symmetric" in topology
    assert "endpoint-independent mapping" in topology
    assert "address-and-port-dependent" in topology
    assert "NETMAP" in topology and "static inbound rewrite" in topology


def test_uncaught_exception_after_success_is_not_harness_ok(tmp_path: Path):
    import subprocess
    import sys
    result = tmp_path / "guest.result"
    (tmp_path / "guest.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text(json.dumps({"
        "'guest_connected': True, 'session_state_exchanged': True}))\n"
        "raise RuntimeError('cleanup')\n", encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(tmp_path / "guest.py"), str(result)], check=False)
    assert proc.returncode == 1
    payload = json.loads(result.read_text(encoding="utf-8"))
    ok = completed_guest_result(payload, timed_out=False, returncode=proc.returncode)
    assert ok is False
    receipt = _receipt(
        case="same-lan", status="FAIL", invite_host_routable_from_guest=True,
        profiles=[{"profile": "art", "guest_connected": True,
                   "session_state_exchanged": True, "harness_ok": ok}],
        rendezvous_reachable_from_host=None, rendezvous_reachable_from_guest=None,
    )
    write_receipt(tmp_path, receipt)
    assert receipt["status"] == "FAIL"
    assert not (tmp_path / f"{FULL_SHA}-same-lan.PASS").exists()
    assert _job(ok) == 1


def test_run_profile_rejects_success_then_exception_exit(tmp_path, monkeypatch):
    row = _run_profile_row(
        tmp_path, monkeypatch, _FakeRoleProc(None),
        _FakeRoleProc(1, already_exited=True),
        result={"guest_connected": True, "session_state_exchanged": True,
                "transport": "lan"},
    )
    assert row["harness_ok"] is False and row["reason"] == "guest_crash"
    assert row["guest_connected"] is True
    assert _job(False) == 1


def _guest_boom(exc: BaseException):
    def _raise(*_a, **_k):
        raise exc
    return _raise


def _load_guest_result(path: Path) -> dict:
    return json.loads((path / "guest.result").read_text(encoding="utf-8"))


def _fake_art_guest(tmp_path, monkeypatch, room, name):
    from contextlib import contextmanager
    from tools.cross_network_acceptance.roles import _art_guest
    r = "tools.cross_network_acceptance.roles"

    @contextmanager
    def runtime():
        yield {"app": None}

    monkeypatch.setattr(f"{r}._art_runtime", runtime)
    monkeypatch.setattr(f"{r}.parse_invite_link", lambda _u: object())
    monkeypatch.setattr(f"{r}._prepare_controller", lambda *_a, **_k: (
        SimpleNamespace(_room_participant=room, begin_startup_journey=lambda: True), object()))
    monkeypatch.setattr(f"{r}._drain", lambda *_a, **_k: False)
    work = tmp_path / name
    work.mkdir()
    (work / "invite").write_text("webjam://x", encoding="utf-8")
    return _art_guest(work), _load_guest_result(work)


def test_guest_startup_and_unfinished_attempts_are_harness_errors(tmp_path, monkeypatch):
    from tools.cross_network_acceptance.roles import (
        _music_guest, classify_native_guest_failure, run_role,
    )

    unfinished = _honest(attempt_finished=False)
    assert completed_guest_result(unfinished, timed_out=False, returncode=1) is False
    assert _job(False) == 1
    timeout = classify_native_guest_failure(
        transport_connected=False, state_exchanged=False, room_failed=False,
        failure_text="Join timed out before opening the room")
    failed_only = classify_native_guest_failure(
        transport_connected=False, state_exchanged=False, failure_text="", room_failed=True)
    assert "honest_unreachable" not in {timeout, failed_only}
    roles = "tools.cross_network_acceptance.roles"
    monkeypatch.setattr(f"{roles}.parse_invite_link", lambda _url: SimpleNamespace(session_id="s"))
    monkeypatch.setattr(f"{roles}._direct_stun_available", lambda: False)
    (tmp_path / "invite").write_text("webjam://x", encoding="utf-8")
    monkeypatch.setattr(f"{roles}.GuestPeerSession", _guest_boom(FileNotFoundError("bin")))
    missing_rc = _music_guest(tmp_path)
    missing = _load_guest_result(tmp_path)
    assert missing.get("failure_class") != "honest_unreachable"
    assert missing.get("attempt_finished") is False
    assert completed_guest_result(missing, timed_out=False, returncode=missing_rc) is False
    for exc, folder in ((TypeError("bad args"), "prog"), (ImportError("no Qt"), "imp")):
        work = tmp_path / folder
        work.mkdir()
        (work / "invite").write_text("webjam://x", encoding="utf-8")
        monkeypatch.setattr(f"{roles}.GuestPeerSession", _guest_boom(exc))
        rc = run_role("guest", "music", work)
        payload = _load_guest_result(work)
        assert payload.get("failure_class") == "programming_error"
        assert completed_guest_result(payload, timed_out=False, returncode=rc) is False
    from core.session_conductor import ArtRoomState

    art_rc, art = _fake_art_guest(
        tmp_path, monkeypatch, SimpleNamespace(state=ArtRoomState.FAILED, borrowed_start=""),
        "art-fail")
    assert art.get("failure_class") != "honest_unreachable"
    assert not completed_guest_result(art, timed_out=False, returncode=art_rc)


def test_art_unavailable_terminal_is_honest_unreachable(tmp_path, monkeypatch):
    from core.session_conductor import ArtRoomState
    from services.lan_room_guest import LanRoomTerminalReason

    finished = SimpleNamespace(
        state=ArtRoomState.FAILED, borrowed_start="",
        lan_guest=SimpleNamespace(terminal_reason=LanRoomTerminalReason.UNAVAILABLE))
    art_rc, art = _fake_art_guest(tmp_path, monkeypatch, finished, "art-unavail")
    assert art.get("failure_class") == "honest_unreachable" and art.get("attempt_finished")
    assert completed_guest_result(art, timed_out=False, returncode=art_rc)
    denied = SimpleNamespace(
        state=ArtRoomState.FAILED, borrowed_start="",
        lan_guest=SimpleNamespace(terminal_reason=LanRoomTerminalReason.INVITATION_REJECTED))
    denied_rc, rejected = _fake_art_guest(tmp_path, monkeypatch, denied, "art-reject")
    assert rejected.get("failure_class") != "honest_unreachable"
    assert not completed_guest_result(rejected, timed_out=False, returncode=denied_rc)


def test_nat_probe_fails_on_subprocess_or_missing_mapping(monkeypatch):
    import subprocess
    import sys
    from tools.cross_network_acceptance.nat_probe import ProbeError, _exec, _read, check_probe
    matching = {
        "nat_mode": "symmetric", "unsolicited_dropped": True, "punch_admitted": True,
        "mapping_reused": False, "host_mapped": 0, "guest_mapped": 0, "observed_ports": [],
    }
    assert check_probe(matching) is False
    assert check_probe({**matching, "host_mapped": 41234, "guest_mapped": 42000,
                        "observed_ports": [41234], "mapped_addr": "203.0.113.10"}) is False
    monkeypatch.setattr("tools.cross_network_acceptance.nat_probe.required_bin", lambda _n: "/bin/false")
    with pytest.raises(ProbeError):
        _exec("ns", "print(1)", [])
    for script in ("print('not-json')", "import sys; sys.exit(3)"):
        proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        with pytest.raises(ProbeError):
            _read(proc)


def test_sidecar_env_kept_and_requested_netem_fails_closed(tmp_path, monkeypatch):
    import os
    from tools.cross_network_acceptance.roles import _direct_stun_available
    from tools.cross_network_acceptance.run import _user_env
    from tools.cross_network_acceptance import run as r
    monkeypatch.setenv("WEBJAM_TRANSPORT_BINARY", "/tmp/webjam-fabric")
    env = _user_env(os.getuid(), product_root=Path("."), transport_binary="", build_id="x")
    assert env["WEBJAM_TRANSPORT_BINARY"] == "/tmp/webjam-fabric"
    assert _direct_stun_available() is True
    monkeypatch.delenv("WEBJAM_TRANSPORT_BINARY")
    monkeypatch.setattr("services.native_remote_transport.ordinary_native_host_profile",
                        lambda: (_ for _ in ()).throw(RuntimeError("p")), raising=False)
    with pytest.raises(RuntimeError):
        _direct_stun_available()
    monkeypatch.setattr(r, "apply_topology", lambda spec: SimpleNamespace(
        spec=spec, host_ns="h", guest_ns="g", inet_ns=None, inet_addr=None))
    for name in ("cleanup_topology", "_chown_tree", "_copy_logs"):
        monkeypatch.setattr(r, name, lambda *_a, **_k: None)
    monkeypatch.setattr(r, "_run_profile", lambda *_a, **_k: _honest(
        harness_ok=True, fake_connect=False, transport="lan"))
    monkeypatch.setattr(r, "apply_netem", lambda _s: False)
    assert r.run_case(output_dir=tmp_path, case="cross-network", profiles=("art",),
                      netem=True, sha=FULL_SHA, expected_status="FAIL") == 1
    monkeypatch.setattr(r, "apply_netem", lambda _s: (_ for _ in ()).throw(OSError("n")))
    assert r.run_case(output_dir=tmp_path / "b", case="cross-network", profiles=("art",),
                      netem=True, sha=FULL_SHA, expected_status="FAIL") == 1
