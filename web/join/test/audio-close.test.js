import test from 'node:test';
import assert from 'node:assert/strict';
import { closeAudioContext } from '../audio.js';

test('audio teardown waits for suspension acknowledgement before close', async () => {
  const calls = [];
  let acknowledge;
  const suspended = new Promise(resolve => { acknowledge = resolve; });
  const closing = closeAudioContext({
    state: 'running',
    suspend() { calls.push('suspend'); return suspended; },
    async close() { calls.push('close'); },
  });
  assert.deepEqual(calls, ['suspend']);
  acknowledge();
  await closing;
  assert.deepEqual(calls, ['suspend', 'close']);
});

for (const synchronous of [false, true]) {
  test(`suspension ${synchronous ? 'throw' : 'rejection'} still attempts close`, async () => {
    let closed = 0;
    await closeAudioContext({
      state: 'running',
      suspend() {
        if (synchronous) throw new Error('Unavailable native suspension');
        return Promise.reject(new Error('Unavailable native suspension'));
      },
      async close() { closed++; },
    });
    assert.equal(closed, 1);
  });
}

test('a suspension that never acknowledges does not prevent close', { timeout: 2000 }, async () => {
  let closed = 0;
  await closeAudioContext({
    state: 'running',
    suspend() { return new Promise(() => {}); },
    async close() { closed++; },
  });
  assert.equal(closed, 1);
});

test('an already closed context needs no new audio operation', async () => {
  await closeAudioContext(null);
  await closeAudioContext({
    state: 'closed',
    suspend() { assert.fail('An already closed context was suspended'); },
    close() { assert.fail('An already closed context was closed again'); },
  });
});

test('close remains available when suspension is unsupported', async () => {
  let closed = 0;
  await closeAudioContext({ state: 'suspended', async close() { closed++; } });
  assert.equal(closed, 1);
});
