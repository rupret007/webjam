from __future__ import annotations

import pytest

from webjam_reference.__main__ import config_from_args
from webjam_reference.config import ServiceConfig


def test_active_limit_default_keeps_admission_idle_and_listener_defaults() -> None:
    config = ServiceConfig()
    assert config.max_active_session_seconds == 28_800
    assert config.max_session_ttl_seconds == 600
    assert config.min_session_ttl_seconds == 30
    assert config.idle_timeout_seconds == 90
    assert config.control_bind == config.relay_bind == config.http_bind == "127.0.0.1"


@pytest.mark.parametrize("value", [False, True, 0, -1, 28_801, 600.0, float("nan"), float("inf"), "3600", None])
def test_active_limit_refuses_invalid_or_unbounded_retention(value) -> None:
    with pytest.raises(ValueError, match="active session limit"):
        ServiceConfig(max_active_session_seconds=value)


def test_active_limit_cannot_end_before_maximum_admission_window() -> None:
    with pytest.raises(ValueError, match="shorter than maximum enrollment TTL"):
        ServiceConfig(max_active_session_seconds=599)
    assert ServiceConfig(max_active_session_seconds=600).max_active_session_seconds == 600


def test_active_limit_can_be_lowered_by_configuration_environment_or_cli(monkeypatch) -> None:
    monkeypatch.setenv("WEBJAM_MAX_ACTIVE_SESSION_SECONDS", "7200")
    from_environment = config_from_args([])
    assert from_environment.max_active_session_seconds == 7200
    assert from_environment.max_session_ttl_seconds == 600
    from_cli = config_from_args(["--max-active-session-seconds", "3600"])
    assert from_cli.max_active_session_seconds == 3600
    assert from_cli.idle_timeout_seconds == 90
    assert from_cli.control_bind == from_cli.relay_bind == from_cli.http_bind == "127.0.0.1"


@pytest.mark.parametrize("value", ["0", "599", "28801"])
def test_cli_cannot_bypass_active_limit_validation(monkeypatch, value) -> None:
    monkeypatch.delenv("WEBJAM_MAX_ACTIVE_SESSION_SECONDS", raising=False)
    with pytest.raises(ValueError, match="active session limit"):
        config_from_args(["--max-active-session-seconds", value])


def test_active_limit_invalid_environment_is_categorical(monkeypatch) -> None:
    sentinel = "private-value-must-not-appear"
    monkeypatch.setenv("WEBJAM_MAX_ACTIVE_SESSION_SECONDS", sentinel)
    with pytest.raises(SystemExit) as caught:
        config_from_args([])
    assert str(caught.value) == "WEBJAM_MAX_ACTIVE_SESSION_SECONDS must be an integer"
    assert sentinel not in str(caught.value)


@pytest.mark.parametrize('listener', ['control_bind', 'relay_bind'])
@pytest.mark.parametrize('with_tls', [False, True])
@pytest.mark.parametrize('legacy_override', [False, True])
def test_exposure_without_admission_is_rejected(listener, with_tls, legacy_override) -> None:
    from pathlib import Path

    values = {listener: '0.0.0.0', 'allow_insecure_public_control': legacy_override}
    if with_tls:
        values.update(tls_cert_path=Path('server.pem'), tls_key_path=Path('server-key.pem'))
    with pytest.raises(ValueError):
        ServiceConfig(**values)


def admission_values():
    from pathlib import Path
    return dict(tls_cert_path=Path('server.pem'), tls_key_path=Path('server-key.pem'),
                host_client_ca_path=Path('host-ca.pem'), host_admission_path=Path('host-policy.json'))


def test_admission_configuration_is_explicit_and_does_not_read_files():
    lab = ServiceConfig(max_sessions=1)
    assert lab.host_admission_enabled is False and lab.max_sessions_per_host == 4
    assert (lab.tls_handshake_timeout_seconds, lab.connection_write_timeout_seconds,
            lab.connection_shutdown_timeout_seconds) == (5, 3, 3)
    for values in ({}, {'control_bind': '0.0.0.0'}, {'relay_bind': '::'},
                   {'control_bind': '0.0.0.0', 'relay_bind': '::'}):
        admitted = ServiceConfig(**admission_values(), **values, max_sessions=1)
        assert admitted.host_admission_enabled and admitted.max_sessions_per_host == 4
        assert admitted.max_sessions == 1  # Global caps below per-host caps remain valid.
    with pytest.raises(ValueError, match='insecure public control is unsupported'):
        ServiceConfig(**admission_values(), control_bind='0.0.0.0', allow_insecure_public_control=True)


@pytest.mark.parametrize('missing', ['host_client_ca_path', 'host_admission_path'])
def test_admission_paths_are_required_together(missing):
    values = admission_values()
    values[missing] = None
    with pytest.raises(ValueError, match='configured together'):
        ServiceConfig(**values)


def test_admission_even_on_loopback_requires_server_tls():
    values = admission_values()
    values.update(tls_cert_path=None, tls_key_path=None)
    with pytest.raises(ValueError, match='host admission requires built-in TLS'):
        ServiceConfig(**values)


@pytest.mark.parametrize('value', [None, True, False, 0, -1, 257, 4.0, float('nan'), float('inf'), '4', [], 10 ** 1000])
def test_host_capacity_requires_a_bounded_integer(value):
    with pytest.raises(ValueError, match='host session limit'):
        ServiceConfig(max_sessions_per_host=value)


@pytest.mark.parametrize('name', ['tls_handshake_timeout_seconds', 'connection_write_timeout_seconds',
                                  'connection_shutdown_timeout_seconds'])
@pytest.mark.parametrize('value', [None, True, False, 0, -1, 30.01, float('nan'), float('inf'), '3', [], 10 ** 1000])
def test_connection_timeout_bounds_are_strict_finite_and_positive(name, value):
    with pytest.raises(ValueError, match='connection timeouts'):
        ServiceConfig(**{name: value})


def test_positive_short_timeout_and_capacity_endpoints_are_accepted():
    for value in (1, 256):
        assert ServiceConfig(max_sessions_per_host=value).max_sessions_per_host == value
    config = ServiceConfig(tls_handshake_timeout_seconds=0.3, connection_write_timeout_seconds=30,
                           connection_shutdown_timeout_seconds=0.1)
    assert config.tls_handshake_timeout_seconds == 0.3 and config.connection_write_timeout_seconds == 30


def test_host_environment_and_cli_override_are_exact(monkeypatch):
    from pathlib import Path

    for name, value in {'WEBJAM_TLS_CERT': 'server.pem', 'WEBJAM_TLS_KEY': 'server-key.pem',
                        'WEBJAM_HOST_CLIENT_CA': 'host-ca.pem', 'WEBJAM_HOST_ADMISSION_FILE': 'host-policy.json',
                        'WEBJAM_MAX_SESSIONS_PER_HOST': '3'}.items():
        monkeypatch.setenv(name, value)
    configured = config_from_args([])
    assert configured.host_admission_enabled and configured.max_sessions_per_host == 3
    assert configured.host_client_ca_path == Path('host-ca.pem')
    overridden = config_from_args(['--host-client-ca', 'other-ca.pem', '--host-admission-file', 'other-policy.json',
                                   '--max-sessions-per-host', '2'])
    assert overridden.host_client_ca_path == Path('other-ca.pem')
    assert overridden.host_admission_path == Path('other-policy.json') and overridden.max_sessions_per_host == 2


def test_bad_host_capacity_cli_and_environment_are_categorical(monkeypatch, capsys):
    sentinel = 'PRIVATE-HOST-CONFIGURATION'
    monkeypatch.delenv('WEBJAM_MAX_SESSIONS_PER_HOST', raising=False)
    with pytest.raises(SystemExit):
        config_from_args(['--max-sessions-per-host', sentinel])
    output = capsys.readouterr()
    assert sentinel not in output.err and 'host session limit must be an integer' in output.err
    monkeypatch.setenv('WEBJAM_MAX_SESSIONS_PER_HOST', sentinel)
    with pytest.raises(SystemExit) as caught:
        config_from_args([])
    assert str(caught.value) == 'WEBJAM_MAX_SESSIONS_PER_HOST must be an integer'


def test_cli_startup_failure_never_echoes_private_paths(monkeypatch, caplog):
    from webjam_reference import __main__ as entry

    async def failed_run(config):
        raise OSError('PRIVATE-HOST-CA-KEY-PATH')

    monkeypatch.setattr(entry, 'run', failed_run)
    assert entry.main([]) == 1
    assert 'reference service configuration or startup failed' in caplog.text
    assert 'PRIVATE-HOST-CA-KEY-PATH' not in caplog.text


def test_cli_invalid_exposure_stops_before_service_start(monkeypatch, caplog):
    from unittest.mock import Mock
    from webjam_reference import __main__ as entry

    start = Mock(side_effect=AssertionError('service must not start'))
    monkeypatch.setattr(entry, 'run', start)
    assert entry.main(['--relay-bind', '0.0.0.0']) == 1
    assert start.call_count == 0
    assert 'reference service configuration or startup failed' in caplog.text


CONTROL_SETUP_LIMITS = (
    ('max_pending_handshakes', 64, 512),
    ('control_accepts_per_second', 32, 1024),
    ('control_accept_burst', 64, 1024),
)


def test_control_setup_defaults_are_separate_from_completed_connection_capacity():
    config = ServiceConfig()
    assert config.max_pending_handshakes == 64
    assert config.control_accepts_per_second == 32
    assert config.control_accept_burst == 64
    assert config.max_connections == 512
    assert config.max_http_connections == 64
    assert config.tls_handshake_timeout_seconds == 5
    assert config.host_admission_enabled is False


@pytest.mark.parametrize('name,default,upper', CONTROL_SETUP_LIMITS)
def test_control_setup_capacity_accepts_only_bounded_positive_integers(name, default, upper):
    for value in (True, False, None, '1', 1.0, float('nan'), float('inf'), 0, -1, upper + 1):
        with pytest.raises(ValueError, match='control setup limits'):
            ServiceConfig(**{name: value})
    for value in (1, upper):
        config = ServiceConfig(**{name: value}, max_connections=1)
        assert getattr(config, name) == value
        assert config.max_connections == 1  # Pending setup and completed limits remain independent.
    assert getattr(ServiceConfig(), name) == default


def test_control_setup_cli_and_environment_configure_each_limit_independently(monkeypatch):
    for name, _, _ in CONTROL_SETUP_LIMITS:
        monkeypatch.setenv('WEBJAM_' + name.upper(), '2')
    from_environment = config_from_args([])
    assert tuple(getattr(from_environment, name) for name, _, _ in CONTROL_SETUP_LIMITS) == (2, 2, 2)
    from_cli = config_from_args(['--max-pending-handshakes', '3', '--control-accepts-per-second', '4',
                                 '--control-accept-burst', '5'])
    assert tuple(getattr(from_cli, name) for name, _, _ in CONTROL_SETUP_LIMITS) == (3, 4, 5)
    assert from_cli.max_connections == 512 and from_cli.max_http_connections == 64
    assert from_cli.control_bind == from_cli.relay_bind == from_cli.http_bind == '127.0.0.1'
    assert from_cli.host_admission_enabled is False


@pytest.mark.parametrize('name,default,upper', CONTROL_SETUP_LIMITS)
def test_control_setup_bad_operator_values_are_categorical(monkeypatch, capsys, name, default, upper):
    option = '--' + name.replace('_', '-')
    environment = 'WEBJAM_' + name.upper()
    sentinel = 'PRIVATE-CONTROL-CAPACITY-VALUE'
    monkeypatch.delenv(environment, raising=False)
    with pytest.raises(SystemExit) as caught:
        config_from_args([option, sentinel])
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert sentinel not in output.err and 'control setup limit must be an integer' in output.err
    monkeypatch.setenv(environment, sentinel)
    with pytest.raises(SystemExit) as caught:
        config_from_args([])
    assert str(caught.value) == environment + ' must be an integer'
    assert sentinel not in str(caught.value)
    monkeypatch.setenv(environment, str(upper + 1))
    with pytest.raises(ValueError, match='control setup limits'):
        config_from_args([])
    monkeypatch.setenv(environment, str(default))
    with pytest.raises(ValueError, match='control setup limits'):
        config_from_args([option, '0'])


@pytest.mark.parametrize('name,default,upper', CONTROL_SETUP_LIMITS)
def test_invalid_control_setup_limit_stops_before_service_start(monkeypatch, caplog, name, default, upper):
    from unittest.mock import Mock
    from webjam_reference import __main__ as entry

    start = Mock(side_effect=AssertionError('service must not start'))
    monkeypatch.setattr(entry, 'run', start)
    assert entry.main(['--' + name.replace('_', '-'), str(upper + 1)]) == 1
    assert start.call_count == 0
    assert 'reference service configuration or startup failed' in caplog.text


@pytest.mark.parametrize('listener', ['control_bind', 'relay_bind', 'http_bind'])
def test_unknown_bind_name_is_rejected_even_with_valid_admission(listener):
    with pytest.raises(ValueError, match='listener addresses must be unscoped IP addresses or localhost'):
        ServiceConfig(**admission_values(), **{listener: 'private-bind.invalid'})


@pytest.mark.parametrize('listener', ['control_bind', 'relay_bind', 'http_bind'])
@pytest.mark.parametrize('value', [None, True, 123, b'127.0.0.1', '', ' localhost', 'localhost.',
                                  'localhoſt', '127.0.0.1:47131', 'fe80::1%en0', '::1%1'])
def test_listener_bind_validation_is_strict_and_never_resolves(listener, value, monkeypatch):
    import socket
    from unittest.mock import Mock

    lookup = Mock(side_effect=AssertionError('configuration must not resolve names'))
    monkeypatch.setattr(socket, 'getaddrinfo', lookup)
    with pytest.raises(ValueError, match='listener addresses must be unscoped IP addresses or localhost'):
        ServiceConfig(**admission_values(), **{listener: value})
    assert lookup.call_count == 0


@pytest.mark.parametrize('value,numeric', [('127.0.0.1', '127.0.0.1'), ('::1', '::1'),
                                         ('LOCALHOST', '127.0.0.1'), ('localhost', '127.0.0.1'),
                                         ('0.0.0.0', '0.0.0.0'), ('::', '::'),
                                         ('2001:DB8::1', '2001:db8::1')])
def test_numeric_bind_helper_preserves_explicit_localhost_and_ip_configuration(value, numeric, monkeypatch):
    import socket
    from unittest.mock import Mock
    from webjam_reference.config import numeric_listener_host

    lookup = Mock(side_effect=AssertionError('numeric binding must not resolve names'))
    monkeypatch.setattr(socket, 'getaddrinfo', lookup)
    config = ServiceConfig(**admission_values(), control_bind=value, relay_bind=value, http_bind=value)
    assert config.control_bind == config.relay_bind == config.http_bind == value
    assert numeric_listener_host(config.http_bind) == numeric
    assert lookup.call_count == 0


@pytest.mark.parametrize('listener', ['control', 'relay', 'http'])
def test_unknown_cli_bind_is_refused_before_start_without_echo(monkeypatch, caplog, listener):
    from unittest.mock import Mock
    from webjam_reference import __main__ as entry

    start = Mock(side_effect=AssertionError('service must not start'))
    monkeypatch.setattr(entry, 'run', start)
    sentinel = 'private-bind-do-not-log.invalid'
    assert entry.main(['--' + listener + '-bind', sentinel]) == 1
    assert start.call_count == 0
    assert sentinel not in caplog.text
    assert 'reference service configuration or startup failed' in caplog.text
