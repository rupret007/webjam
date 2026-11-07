import test from 'node:test';
import assert from 'node:assert/strict';
import { gatherAnswerSDP, WebJoinSession } from '../session.js';
import { offerHasReviewedAllocation } from '../policy.js';

const direct = 'a=candidate:1 1 udp 123 1.1.1.1 42000 typ srflx raddr 192.168.1.4 rport 7000';
const privateHost = 'a=candidate:2 1 udp 124 192.168.1.4 7000 typ host';

function gatheringPeer(...candidates) {
  return Object.assign(new EventTarget(), {
    iceGatheringState: 'gathering',
    localDescription: { sdp: ['v=0', ...candidates, ''].join('\r\n') },
  });
}

test('the gathering deadline keeps vetted candidates and strips private addresses', async () => {
  const pc = gatheringPeer(direct, privateHost);
  const answer = await gatherAnswerSDP(pc, new AbortController().signal, { timeout: 0 });
  assert.equal(pc.iceGatheringState, 'gathering');
  assert.match(answer, /1\.1\.1\.1 42000 typ srflx raddr 0\.0\.0\.0 rport 0/);
  assert.doesNotMatch(answer, /192\.168\.1\.4|typ host/);
});

test('gathering with no vetted candidates or a canceled join cannot produce an answer', async () => {
  await assert.rejects(gatherAnswerSDP(gatheringPeer(privateHost), new AbortController().signal, { timeout: 0 }), /No permitted network path/);
  const controller = new AbortController();
  const answer = gatherAnswerSDP(gatheringPeer(direct), controller.signal);
  controller.abort();
  await assert.rejects(answer, { name: 'AbortError' });
});

test('answers offer the reviewed pools only to a host that advertises one', async () => {
  const relay = 'a=candidate:3 1 udp 125 104.30.148.28 42000 typ relay';
  const answer = gatheringPeer(direct, relay);
  for (const [host, expectRelay] of [[gatheringPeer(direct), false], [gatheringPeer(direct, relay), true]]) {
    const allowReviewedAllocations = offerHasReviewedAllocation(host.localDescription.sdp);
    const sdp = await gatherAnswerSDP(answer, new AbortController().signal, { timeout: 0, allowReviewedAllocations });
    assert.equal(sdp.includes('104.30.148.28'), expectRelay);
  }
});

test('stats resolving after close cannot tune a retired receiver or publish diagnostics', async () => {
  let resolve, latencies = 0, routes = 0, stops = 0;
  const track = { enabled: true, stop() { stops++; } };
  const session = new WebJoinSession({ origin: 'https://relay.example', invite: {},
    stream: { getAudioTracks: () => [track], getTracks: () => [track] },
    audioContext: { baseLatency: 0.01 }, onLatency: () => { latencies++; }, onRoute: () => { routes++; } });
  const receiver = { jitterBufferTarget: 20 };
  session._inboundReceiver = receiver;
  session.pc = { connectionState: 'connected', getStats: () => new Promise(r => { resolve = r; }), close() {} };
  session.authorized = true;
  const pending = session.updateStats();
  session.close();
  resolve(new Map([
    ['transport', { type: 'transport', bytesSent: 5, bytesReceived: 5, selectedCandidatePairId: 'pair' }],
    ['pair', { type: 'candidate-pair', nominated: true, state: 'succeeded', currentRoundTripTime: 1, localCandidateId: 'local' }],
    ['local', { type: 'local-candidate', candidateType: 'relay' }],
    ['audio', { type: 'inbound-rtp', kind: 'audio', jitterBufferDelay: 1, jitterBufferEmittedCount: 100 }],
  ]));
  await pending;
  assert.equal(latencies, 0);
  assert.equal(routes, 0);
  assert.equal(receiver.jitterBufferTarget, 20);
  assert.equal(session.previousBytes, 0);
  assert.equal(stops, 1);
});

test('connection diagnostics use the selected pair and clear when the path is unknown', async () => {
  const track = { enabled: true, stop() {} };
  const routes = [];
  const stats = new Map([
    ['transport', { type: 'transport', bytesSent: 5, bytesReceived: 5, selectedCandidatePairId: 'direct' }],
    ['direct', { type: 'candidate-pair', state: 'succeeded', localCandidateId: 'local', remoteCandidateId: 'remote' }],
    ['relay', { type: 'candidate-pair', state: 'succeeded', nominated: true, localCandidateId: 'local', remoteCandidateId: 'relay-remote' }],
    ['local', { type: 'local-candidate', candidateType: 'srflx' }],
    ['remote', { type: 'remote-candidate', candidateType: 'prflx' }],
    ['relay-remote', { type: 'remote-candidate', candidateType: 'relay' }],
  ]);
  const session = new WebJoinSession({ origin: 'https://relay.example', invite: {},
    stream: { getAudioTracks: () => [track], getTracks: () => [track] },
    audioContext: {}, onRoute: (route) => routes.push(route) });
  session.pc = { connectionState: 'connected', getStats: async () => stats, close() {} };
  session.authorized = true;
  await session.updateStats();
  await session.updateStats();
  stats.delete('remote');
  await session.updateStats();
  stats.get('transport').selectedCandidatePairId = 'relay';
  await session.updateStats();
  session.pc.connectionState = 'disconnected';
  await session.updateStats();
  assert.deepEqual(routes, ['direct', null, 'relayed', null]);
  session.close();
});
