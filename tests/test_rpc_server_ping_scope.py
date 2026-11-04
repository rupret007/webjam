"""Directory and local-sidecar pings are not current remote audio latency."""
from core.jamulus_rpc_client import JamulusRpcClient, JamulusRpcMonitorIdentity


def client():
    rpc = JamulusRpcClient()
    rpc._running = True
    rpc._monitor_epoch = 1
    rpc._monitor_identity = JamulusRpcMonitorIdentity(1, 1, 101)
    return rpc


def emit(rpc, address, *, epoch=1):
    rpc._handle_notification('jamulusclient/serverInfoReceived',
                             {'address': address, 'pingtime': 12}, epoch=epoch)


def test_unspecified_ping_target_cannot_publish_latency():
    rpc = client()
    observed = []
    rpc.set_server_info_listener(lambda *args: observed.append(args))
    emit(rpc, '192.168.1.20:22124')
    assert observed == []


def test_only_exact_launched_server_and_current_monitor_publish_latency():
    rpc = client()
    observed = []
    rpc.set_server_info_listener(lambda *args: observed.append(args),
                                server_address='192.168.1.20:22124')
    for address in ['192.168.1.21:22124', '192.168.1.20:22125', '', None]:
        emit(rpc, address)
    emit(rpc, '192.168.1.20:22124', epoch=2)
    assert observed == []
    emit(rpc, '192.168.1.20:22124')
    assert observed == [(12, rpc._monitor_identity)]


def test_loopback_sidecar_and_unresolved_hostname_cannot_claim_wan_ping():
    for target in ['127.0.0.1:22124', 'localhost:22124', 'band.example:22124']:
        rpc = client()
        observed = []
        rpc.set_server_info_listener(lambda *args: observed.append(args), server_address=target)
        emit(rpc, target)
        assert observed == []
