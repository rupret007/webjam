import test from 'node:test';
import assert from 'node:assert/strict';
import { adaptJitterBufferTarget, latencyMs } from '../audio.js';
import { WebJoinSession } from '../session.js';

function pathStats(selected = 'fast', reverse = false) {
  const pairs = [
    ['fast', { type: 'candidate-pair', state: 'succeeded', nominated: true, currentRoundTripTime: 0.04 }],
    ['slow', { type: 'candidate-pair', state: 'succeeded', nominated: true, currentRoundTripTime: 1.2 }],
  ];
  return new Map([
    ['transport', { type: 'transport', selectedCandidatePairId: selected, bytesSent: 5, bytesReceived: 5 }],
    ...(reverse ? pairs.reverse() : pairs),
    ['audio', { type: 'inbound-rtp', kind: 'audio', packetsReceived: 100, packetsLost: 0, jitter: 0,
      jitterBufferDelay: 2, jitterBufferEmittedCount: 100 }],
  ]);
}

test('delay and receiver buffering follow the selected path, independent of stats order', () => {
  for (const reverse of [false, true]) {
    for (const [selected, delay, rtt, target] of [['fast', 50, 40, 20], ['slow', 630, 1200, 30]]) {
      const stats = pathStats(selected, reverse);
      // The transport reference is authoritative even without the legacy flag.
      stats.get(selected).nominated = false;
      assert.equal(latencyMs(stats, 0.01), delay, `${selected}, reversed=${reverse}`);
      const adaptation = adaptJitterBufferTarget(stats, { targetMs: 20, lastChangeAt: 0 });
      assert.equal(adaptation.rttMs, rtt);
      assert.equal(adaptation.targetMs, target);
    }
  }
});

test('missing, ambiguous or invalid selected-path RTT never borrows an old path', () => {
  const variants = [
    stats => stats.delete('transport'),
    stats => delete stats.get('transport').selectedCandidatePairId,
    stats => { stats.get('transport').selectedCandidatePairId = 'missing'; },
    stats => { stats.get('fast').state = 'in-progress'; },
    stats => { stats.get('fast').type = 'local-candidate'; },
    stats => { stats.get('fast').currentRoundTripTime = -1; },
    stats => { stats.get('fast').currentRoundTripTime = NaN; },
    stats => { stats.get('fast').currentRoundTripTime = Infinity; },
    stats => { delete stats.get('fast').currentRoundTripTime; },
    stats => stats.set('second-transport', { type: 'transport', selectedCandidatePairId: 'slow' }),
  ];
  for (const mutate of variants) {
    const stats = pathStats();
    mutate(stats);
    assert.equal(latencyMs(stats, 0.01), null, mutate.toString());
    const adaptation = adaptJitterBufferTarget(stats, { targetMs: 20, lastChangeAt: 0 });
    assert.equal(adaptation.rttMs, undefined);
    assert.equal(adaptation.targetMs, 20);
  }
});

test('zero selected-path RTT is valid and audio jitter still adapts without a path estimate', () => {
  const stats = pathStats();
  stats.get('fast').currentRoundTripTime = 0;
  assert.equal(latencyMs(stats, 0.01), 30);
  stats.delete('transport');
  stats.get('audio').jitter = 0.03;
  const adaptation = adaptJitterBufferTarget(stats, { targetMs: 20, lastChangeAt: 0 });
  assert.equal(adaptation.rttMs, undefined);
  assert.equal(adaptation.targetMs, 30);
});

test('session diagnostics clear a retired path estimate and recover on a new selection', async () => {
  const stats = pathStats();
  const latencies = [], errors = [];
  const track = { enabled: true, stop() {} };
  const session = new WebJoinSession({ origin: 'https://relay.example', invite: {},
    stream: { getAudioTracks: () => [track], getTracks: () => [track] },
    audioContext: { baseLatency: 0.01 }, onLatency: value => latencies.push(value),
    onError: error => errors.push(error) });
  session.pc = { connectionState: 'connected', getStats: async () => stats, close() {} };
  session.authorized = true;
  session._inboundReceiver = { jitterBufferTarget: 20 };
  try {
    await session.updateStats();
    assert.equal(session._inboundReceiver.jitterBufferTarget, 20);
    stats.get('transport').selectedCandidatePairId = 'missing';
    await session.updateStats();
    stats.get('transport').selectedCandidatePairId = 'slow';
    await session.updateStats();
    assert.deepEqual(latencies, [50, null, 630]);
    assert.deepEqual(errors, []);
    assert.equal(session.previousBytes, 10);
  } finally {
    session.close();
  }
});

test('adding a camera preserves the music estimate and a timing trip leaves music open', async () => {
  const stats = pathStats();
  const delays = [], errors = [];
  const track = { enabled: true, stop() {} };
  let camera = null;
  let trips = 0;
  const session = new WebJoinSession({ origin: 'https://relay.example', invite: {},
    stream: { getAudioTracks: () => [track], getTracks: () => [track] },
    audioContext: { baseLatency: 0.01 }, onLatency: value => delays.push(value),
    onError: error => errors.push(error), videoPeer: () => camera, onVideoTrip() {
      trips++;
      camera = null;
    },
  });
  session.pc = { connectionState: 'connected', getStats: async () => stats, close() {} };
  session.authorized = true;
  track.enabled = true;
  session.videoBaselineDeadline = Date.now() + 30000;
  try {
    for (let i = 0; i < 20; i++) await session.updateStats();
    assert.equal(session.videoBaseline.latency, 50);
    camera = { connectionState: 'connected', getStats: () => assert.fail('video stats are not music stats') };
    for (let i = 0; i < 5; i++) await session.updateStats();
    assert.deepEqual(delays.slice(-5), [50, 50, 50, 50, 50]);
    assert.equal(trips, 0);

    stats.get('fast').currentRoundTripTime = 0.062;
    for (let i = 0; i < 3; i++) await session.updateStats();
    assert.equal(trips, 1);
    assert.equal(session.authorized, true);
    assert.equal(track.enabled, true);
    assert.deepEqual(errors, []);
  } finally {
    session.close();
  }
});

test('video can start on relayed music and pauses when music moves back onto relay', async () => {
  const stats = pathStats();
  const track = { enabled: true, stop() {} };
  const relay = { ...stats.get('fast'), localCandidateId: 'relay', remoteCandidateId: 'host' };
  stats.set('relay', { type: 'local-candidate', candidateType: 'relay' });
  stats.set('host', { type: 'remote-candidate', candidateType: 'host' });
  stats.set('first-relay', relay);
  stats.set('other-relay', { ...relay });
  stats.set('fast', { ...stats.get('fast'), localCandidateId: 'direct', remoteCandidateId: 'host' });
  stats.set('direct', { type: 'local-candidate', candidateType: 'host' });
  const transport = stats.get('transport');
  let camera;
  let trips = 0;
  const session = new WebJoinSession({ origin: 'https://relay.example', invite: {},
    stream: { getAudioTracks: () => [track], getTracks: () => [track] },
    audioContext: { baseLatency: 0.01 }, videoPeer: () => camera, onVideoTrip() { trips++; },
    onError: error => assert.fail(error),
  });
  session.pc = { connectionState: 'connected', getStats: async () => stats, close() {} };
  session.authorized = true;
  track.enabled = true;
  session.videoBaselineDeadline = Date.now() + 30000;
  try {
    transport.selectedCandidatePairId = 'first-relay';
    for (let i = 0; i < 20; i++) await session.updateStats();
    assert.equal(session.videoBaseline.route, 'relayed');
    camera = { connectionState: 'connected' };
    for (const pair of ['first-relay', 'other-relay', 'fast', 'missing']) {
      transport.selectedCandidatePairId = pair;
      await session.updateStats();
      assert.equal(trips, 0, pair);
    }
    transport.selectedCandidatePairId = 'first-relay';
    await session.updateStats();
    assert.equal(trips, 1);
    assert.equal(session.authorized, true);
    assert.equal(track.enabled, true);
  } finally {
    session.close();
  }
});
