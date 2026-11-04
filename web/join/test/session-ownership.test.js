import test from 'node:test';
import assert from 'node:assert/strict';
import { WebJoinSession } from '../session.js';

test('stats resolving after close cannot tune a retired receiver or publish latency', async () => {
  let resolve, latencies = 0, stops = 0;
  const track = { enabled: true, stop() { stops++; } };
  const session = new WebJoinSession({ origin: 'https://relay.example', invite: {},
    stream: { getAudioTracks: () => [track], getTracks: () => [track] },
    audioContext: { baseLatency: 0.01 }, onLatency: () => { latencies++; } });
  const receiver = { jitterBufferTarget: 20 };
  session._inboundReceiver = receiver;
  session.pc = { getStats: () => new Promise(r => { resolve = r; }), close() {} };
  session.authorized = true;
  const pending = session.updateStats();
  session.close();
  resolve(new Map([
    ['transport', { type: 'transport', bytesSent: 5, bytesReceived: 5 }],
    ['pair', { type: 'candidate-pair', nominated: true, state: 'succeeded', currentRoundTripTime: 1 }],
    ['audio', { type: 'inbound-rtp', kind: 'audio', jitterBufferDelay: 1, jitterBufferEmittedCount: 100 }],
  ]));
  await pending;
  assert.equal(latencies, 0);
  assert.equal(receiver.jitterBufferTarget, 20);
  assert.equal(session.previousBytes, 0);
  assert.equal(stops, 1);
});
