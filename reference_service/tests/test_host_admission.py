from __future__ import annotations

import hashlib
import json
import os
import ssl
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from webjam_reference.host_admission import HostAdmissionPolicy, HostPrincipal, MAX_POLICY_BYTES
from webjam_reference.protocol import ProtocolError

PRINCIPAL = 'a' * 32
OTHER_PRINCIPAL = 'b' * 32
LEAF = b'explicitly synthetic already-verified test leaf'
FINGERPRINT = hashlib.sha256(LEAF).hexdigest()
BEFORE = 'Sep  9 00:00:00 2026 GMT'
AFTER = 'Sep 10 00:00:00 2026 GMT'
START = ssl.cert_time_to_seconds(BEFORE)
END = ssl.cert_time_to_seconds(AFTER)


def manifest(*rows):
    return {'v': 1, 'hosts': list(rows or [{'principal': PRINCIPAL, 'certificate_sha256': FINGERPRINT}])}


def load_policy(tmp_path, value=None):
    target = tmp_path / 'host-policy.json'
    target.write_text(json.dumps(manifest() if value is None else value))
    return HostAdmissionPolicy.load(target)


class VerifiedPeer:
    """Synthetic extraction seam; the separate actual TLS suite proves trust."""

    def __init__(self, der=LEAF, metadata=None, *, mode=ssl.CERT_OPTIONAL, version='TLSv1.3'):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.verify_mode = mode
        self.der = der
        self.metadata = {'notBefore': BEFORE, 'notAfter': AFTER} if metadata is None else metadata
        self.tls_version = version

    def version(self):
        return self.tls_version

    def getpeercert(self, binary_form=False):
        return self.der if binary_form else self.metadata


def test_loaded_policy_and_receipt_are_immutable_and_redacted(tmp_path):
    policy = load_policy(tmp_path)
    assert policy.principals == (PRINCIPAL,)
    receipt = policy.identify(VerifiedPeer())
    assert receipt == HostPrincipal(PRINCIPAL, FINGERPRINT, START, END)
    assert policy.authorize(receipt, START) == PRINCIPAL
    with pytest.raises(FrozenInstanceError):
        policy.principals = ()
    with pytest.raises(TypeError):
        policy._by_fingerprint[FINGERPRINT] = OTHER_PRINCIPAL
    with pytest.raises(FrozenInstanceError):
        receipt.not_after = END + 1000
    for value in (str(policy), repr(policy), str(receipt), repr(receipt)):
        assert PRINCIPAL not in value and FINGERPRINT not in value


@pytest.mark.parametrize('version', [True, False, 1.0, '1', 0, 2, None])
def test_manifest_version_is_exact_integer_one(tmp_path, version):
    value = manifest()
    value['v'] = version
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        load_policy(tmp_path, value)


@pytest.mark.parametrize('value', [[], 1, None, {}, {'v': 1}, {'v': 1, 'hosts': []},
                                   {'v': 1, 'hosts': {}}, {'v': 1, 'hosts': [None]},
                                   {'v': 1, 'hosts': [], 'extra': True}])
def test_manifest_shape_is_strict(tmp_path, value):
    target = tmp_path / 'bad-policy.json'
    target.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(target)


@pytest.mark.parametrize('field,value', [
    ('principal', 'A' * 32), ('principal', 'a' * 31), ('principal', 'a' * 33),
    ('principal', 'a' * 31 + '\n'), ('principal', 7), ('principal', []),
    ('certificate_sha256', 'A' * 64), ('certificate_sha256', 'a' * 63),
    ('certificate_sha256', 'g' * 64), ('certificate_sha256', False), ('certificate_sha256', {}),
])
def test_manifest_ids_and_fingerprints_are_strict(tmp_path, field, value):
    row = manifest()['hosts'][0]
    row[field] = value
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        load_policy(tmp_path, manifest(row))


@pytest.mark.parametrize('text', [
    '{"v":1,"v":1,"hosts":[]}',
    '{"v":1,"hosts":[{"principal":"a","principal":"b","certificate_sha256":"c"}]}',
    '{"v":1,"hosts":NaN}', '{"v":1,"hosts":Infinity}', '{"v":1,"hosts":1e999}',
    '{"v":1,"hosts":[]', '[' * 2000 + ']' * 2000,
])
def test_manifest_duplicate_keys_constants_and_malformed_json_are_safe(tmp_path, text):
    target = tmp_path / 'PRIVATE-POLICY-LOCATION'
    target.write_text(text)
    with pytest.raises(ValueError) as caught:
        HostAdmissionPolicy.load(target)
    assert str(caught.value) == 'invalid host admission policy'
    assert caught.value.__suppress_context__ is True


def test_duplicate_identity_or_leaf_cannot_allocate_another_host(tmp_path):
    row = manifest()['hosts'][0]
    for second in (dict(row), dict(row, principal=OTHER_PRINCIPAL),
                   dict(row, certificate_sha256='c' * 64)):
        with pytest.raises(ValueError, match='^invalid host admission policy$'):
            load_policy(tmp_path, manifest(row, second))
    for altered in (dict(row, extra=1), {'principal': PRINCIPAL}, {'certificate_sha256': FINGERPRINT}):
        with pytest.raises(ValueError, match='^invalid host admission policy$'):
            load_policy(tmp_path, manifest(altered))


def test_manifest_size_and_host_count_bounds(tmp_path):
    target = tmp_path / 'host-policy.json'
    payload = json.dumps(manifest()).encode()
    target.write_bytes(payload + b' ' * (MAX_POLICY_BYTES - len(payload)))
    assert HostAdmissionPolicy.load(target).principals == (PRINCIPAL,)
    for payload in (b'', payload + b' ' * MAX_POLICY_BYTES, b'\xff'):
        target.write_bytes(payload)
        with pytest.raises(ValueError, match='^invalid host admission policy$'):
            HostAdmissionPolicy.load(target)
    rows = [{'principal': f'{index:032x}', 'certificate_sha256': f'{index:064x}'} for index in range(129)]
    assert len(load_policy(tmp_path, manifest(*rows[:128])).principals) == 128
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        load_policy(tmp_path, manifest(*rows))


def test_policy_requires_regular_file_and_refuses_link(tmp_path):
    missing = tmp_path / 'PRIVATE-MISSING-POLICY'
    for target in (missing, tmp_path):
        with pytest.raises(ValueError, match='^invalid host admission policy$'):
            HostAdmissionPolicy.load(target)
    original = tmp_path / 'policy.json'
    original.write_text(json.dumps(manifest()))
    link = tmp_path / 'policy-link'
    link.symlink_to(original)
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(link)
    if hasattr(os, 'mkfifo'):
        fifo = tmp_path / 'policy-fifo'
        os.mkfifo(fifo)
        with pytest.raises(ValueError, match='^invalid host admission policy$'):
            HostAdmissionPolicy.load(fifo)


def test_policy_mutation_during_read_is_refused_and_descriptor_closed(tmp_path, monkeypatch):
    import webjam_reference.host_admission as module

    target = tmp_path / 'host-policy.json'
    target.write_text(json.dumps(manifest()))
    original_read, original_close = os.read, os.close
    changed = False
    closed = []

    def read(descriptor, size):
        nonlocal changed
        value = original_read(descriptor, size)
        if not changed:
            changed = True
            target.write_text(json.dumps(manifest({'principal': OTHER_PRINCIPAL, 'certificate_sha256': FINGERPRINT})))
        return value

    def close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(module.os, 'read', read)
    monkeypatch.setattr(module.os, 'close', close)
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(target)
    assert changed and len(closed) == 1


def _split_stat_policy_file(tmp_path, monkeypatch, *, platform='nt', mutation=None,
                           mismatched_field=None, split_ctime=True, after_read=None,
                           after_seek=None, payload=None):
    """Real file I/O with synthetic stat receipts; not a Windows execution test.

    CPython 3.12 Windows path stat reports creation time in ctime while fstat
    reports change time. Only those receipts and this module's OS name change.
    """
    import webjam_reference.host_admission as module

    target = tmp_path / 'host-policy.json'
    target.write_bytes(json.dumps(manifest()).encode() if payload is None else payload)
    initial = target.lstat()
    observed = SimpleNamespace(reads=0, closed=[], seeks=0, bytes_read=0)

    def receipt(api):
        values = {name: getattr(initial, name) for name in
                  ('st_mode', 'st_dev', 'st_ino', 'st_size', 'st_mtime_ns')}
        values['st_ctime_ns'] = 100 if api == 'lstat' or not split_ctime else 200
        if mutation == api and observed.reads:
            values['st_ctime_ns'] += 1
        if mismatched_field is not None and api == 'fstat':
            values[mismatched_field] += 1
        return SimpleNamespace(**values)

    class PolicyPath:
        def __init__(self, supplied):
            assert supplied == target

        def __fspath__(self):
            return os.fspath(target)

        def lstat(self):
            return receipt('lstat')

    class PolicyOS:
        name = platform

        def __getattr__(self, name):
            return getattr(os, name)

        def fstat(self, descriptor):
            os.fstat(descriptor)  # The descriptor must really remain open.
            return receipt('fstat')

        def read(self, descriptor, size):
            observed.reads += 1
            value = os.read(descriptor, size)
            observed.bytes_read += len(value)
            if after_read is not None:
                after_read(target, observed)
            return value

        def lseek(self, descriptor, offset, whence):
            observed.seeks += 1
            value = os.lseek(descriptor, offset, whence)
            if after_seek is not None:
                after_seek(target, observed)
            return value

        def close(self, descriptor):
            observed.closed.append(descriptor)
            os.close(descriptor)

    monkeypatch.setattr(module, 'Path', PolicyPath)
    monkeypatch.setattr(module, 'os', PolicyOS())
    return target, observed


def test_policy_accepts_distinct_windows_path_and_descriptor_ctime(tmp_path, monkeypatch):
    target, observed = _split_stat_policy_file(tmp_path, monkeypatch)
    assert HostAdmissionPolicy.load(target).principals == (PRINCIPAL,)
    assert observed.reads > 0 and len(observed.closed) == 1
    with pytest.raises(OSError):
        os.fstat(observed.closed[0])


@pytest.mark.parametrize('mutation', ['fstat', 'lstat'])
def test_policy_refuses_same_api_metadata_mutation_despite_windows_ctime_split(
        tmp_path, monkeypatch, mutation):
    target, observed = _split_stat_policy_file(tmp_path, monkeypatch, mutation=mutation)
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(target)
    assert observed.reads > 0  # Rejection is after reading, not the initial API difference.
    assert len(observed.closed) == 1
    with pytest.raises(OSError):
        os.fstat(observed.closed[0])


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_size', 'st_mtime_ns'])
def test_policy_refuses_windows_cross_api_file_identity_mismatch(tmp_path, monkeypatch, field):
    target, observed = _split_stat_policy_file(tmp_path, monkeypatch, mismatched_field=field)
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(target)
    assert observed.reads == 0 and len(observed.closed) == 1


def test_policy_keeps_posix_cross_api_ctime_check(tmp_path, monkeypatch):
    target, observed = _split_stat_policy_file(tmp_path, monkeypatch, platform='posix')
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(target)
    assert observed.reads == 0 and len(observed.closed) == 1


@pytest.mark.parametrize('platform', ['posix', 'nt'])
def test_policy_rejects_real_same_size_content_change_with_stable_stat_receipts(
        tmp_path, monkeypatch, platform):
    replacement = json.dumps(manifest({
        'principal': OTHER_PRINCIPAL, 'certificate_sha256': FINGERPRINT})).encode()

    def rewrite(target, observed):
        if observed.reads == 1:
            assert target.stat().st_size == len(replacement)
            target.write_bytes(replacement)

    target, observed = _split_stat_policy_file(
        tmp_path, monkeypatch, platform=platform, split_ctime=platform == 'nt',
        after_read=rewrite)
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(target)
    assert target.read_bytes() == replacement
    assert len(observed.closed) == 1
    with pytest.raises(OSError):
        os.fstat(observed.closed[0])


def test_policy_refuses_content_change_mid_verification_read(tmp_path, monkeypatch):
    prefix = b' ' * 8192
    original = prefix + json.dumps(manifest()).encode()
    replacement = prefix + json.dumps(manifest({
        'principal': OTHER_PRINCIPAL, 'certificate_sha256': FINGERPRINT})).encode()
    changed = False

    def rewrite(target, observed):
        nonlocal changed
        if observed.seeks and not changed:
            changed = True
            target.write_bytes(replacement)

    target, observed = _split_stat_policy_file(
        tmp_path, monkeypatch, payload=original, after_read=rewrite)
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(target)
    assert changed and len(observed.closed) == 1
    assert observed.bytes_read <= 2 * (MAX_POLICY_BYTES + 1)


def test_policy_bounds_verification_read_when_file_grows(tmp_path, monkeypatch):
    def grow(target, _observed):
        target.write_bytes(b' ' * (MAX_POLICY_BYTES + 100))

    target, observed = _split_stat_policy_file(tmp_path, monkeypatch, after_seek=grow)
    initial_size = target.stat().st_size
    with pytest.raises(ValueError, match='^invalid host admission policy$'):
        HostAdmissionPolicy.load(target)
    assert observed.seeks == 1 and len(observed.closed) == 1
    assert observed.bytes_read == initial_size + MAX_POLICY_BYTES + 1


@pytest.mark.parametrize('failure', ['seek', 'read'])
def test_policy_verification_io_error_is_private_and_closes_descriptor(
        tmp_path, monkeypatch, failure):
    def fail_seek(_target, _observed):
        if failure == 'seek':
            raise OSError('PRIVATE-POLICY-SEEK')

    def fail_read(_target, observed):
        if failure == 'read' and observed.seeks:
            raise OSError('PRIVATE-POLICY-READ')

    target, observed = _split_stat_policy_file(
        tmp_path, monkeypatch, after_seek=fail_seek, after_read=fail_read)
    with pytest.raises(ValueError, match='^invalid host admission policy$') as caught:
        HostAdmissionPolicy.load(target)
    assert caught.value.__suppress_context__ is True
    assert observed.seeks == 1 and len(observed.closed) == 1
    with pytest.raises(OSError):
        os.fstat(observed.closed[0])


@pytest.mark.parametrize('when,allowed', [(START - 1, False), (START, True), (END - 0.1, True),
                                         (END, False), (END + 1, False)])
def test_allocation_rechecks_current_wall_clock_at_exact_boundaries(tmp_path, when, allowed):
    policy = load_policy(tmp_path)
    receipt = policy.identify(VerifiedPeer())
    if allowed:
        assert policy.authorize(receipt, when) == PRINCIPAL
    else:
        with pytest.raises(ProtocolError, match='^unauthorized$'):
            policy.authorize(receipt, when)


@pytest.mark.parametrize('now', [True, None, '0', float('nan'), float('inf'), -float('inf'), 10 ** 1000])
def test_invalid_allocation_clock_is_unauthorized(tmp_path, now):
    policy = load_policy(tmp_path)
    with pytest.raises(ProtocolError, match='^unauthorized$'):
        policy.authorize(policy.identify(VerifiedPeer()), now)


def test_receipt_expiry_and_restart_policy_replacement_are_distinct(tmp_path):
    first = manifest()['hosts'][0]
    second = {'principal': OTHER_PRINCIPAL, 'certificate_sha256': 'b' * 64}
    old = load_policy(tmp_path, manifest(first, second))
    receipt = old.identify(VerifiedPeer())
    replacement = load_policy(tmp_path, manifest(second))
    assert old.authorize(receipt, START) == PRINCIPAL  # File replacement alone is not hot revocation.
    with pytest.raises(ProtocolError, match='^unauthorized$'):
        replacement.authorize(receipt, START)
    with pytest.raises(ProtocolError, match='^unauthorized$'):
        old.authorize(receipt, END)  # Existing connection receipt cannot extend certificate validity.


@pytest.mark.parametrize('changes', [
    {'principal': OTHER_PRINCIPAL}, {'principal': []}, {'certificate_sha256': []},
    {'certificate_sha256': 'd' * 64}, {'not_before': float('nan')},
    {'not_after': float('inf')}, {'not_after': False}, {'not_before': END},
])
def test_invalid_or_unapproved_receipt_is_categorical(tmp_path, changes):
    policy = load_policy(tmp_path)
    receipt = replace(policy.identify(VerifiedPeer()), **changes)
    with pytest.raises(ProtocolError, match='^unauthorized$'):
        policy.authorize(receipt, START)
    with pytest.raises(ProtocolError, match='^unauthorized$'):
        policy.authorize(None, START)


@pytest.mark.parametrize(
    'der', [None, b'', 'not-DER', bytearray(LEAF), b'x' * 65537, b'unapproved leaf'],
    ids=['missing', 'empty', 'wrong-type', 'mutable-bytes', 'oversized', 'unapproved'])
def test_missing_unapproved_or_unbounded_peer_leaf_has_no_principal(tmp_path, der):
    assert load_policy(tmp_path).identify(VerifiedPeer(der=der)) is None


@pytest.mark.parametrize('metadata', [{}, [], {'notBefore': BEFORE}, {'notBefore': 'bad', 'notAfter': AFTER},
                                      {'notBefore': BEFORE, 'notAfter': START},
                                      {'notBefore': 'a' * 65, 'notAfter': AFTER},
                                      {'notBefore': AFTER, 'notAfter': BEFORE}])
def test_invalid_verified_leaf_metadata_has_no_principal(tmp_path, metadata):
    assert load_policy(tmp_path).identify(VerifiedPeer(metadata=metadata)) is None


def test_unverified_context_old_tls_and_client_direction_are_not_identity(tmp_path):
    policy = load_policy(tmp_path)
    assert policy.identify(None) is None
    assert policy.identify(VerifiedPeer(mode=ssl.CERT_NONE)) is None
    assert policy.identify(VerifiedPeer(version='TLSv1.2')) is None
    client = VerifiedPeer()
    client.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert policy.identify(client) is None


def test_certificate_subject_and_extraction_error_never_grant_or_leak(tmp_path, caplog):
    policy = load_policy(tmp_path)
    peer = VerifiedPeer(der=b'other leaf', metadata={'subject': PRINCIPAL, 'notBefore': BEFORE, 'notAfter': AFTER})
    assert policy.identify(peer) is None

    class BrokenPeer(VerifiedPeer):
        def getpeercert(self, binary_form=False):
            raise OSError('PRIVATE-CERTIFICATE-PATH-AND-SUBJECT')

    assert policy.identify(BrokenPeer()) is None
    assert caplog.text == ''
