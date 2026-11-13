import { test, expect } from '@playwright/test';

async function openMicFailure(page, failure) {
  const external = [];
  await page.route('**/*', route => {
    if (new URL(route.request().url()).origin === 'http://127.0.0.1:4174') return route.continue();
    external.push(route.request().url());
    return route.abort();
  });
  await page.addInitScript(failure => {
    window.micFailureContexts = [];
    window.micFailureTracks = [];
    window.micFailureCalls = 0;
    const NativeContext = window.AudioContext;
    window.AudioContext = class extends NativeContext {
      constructor(...args) { super(...args); micFailureContexts.push(this); }
    };
    if (failure === 'meter') {
      NativeContext.prototype.createAnalyser = () => { throw new Error('Synthetic input level failure'); };
    }
    const media = Object.getPrototypeOf(navigator.mediaDevices);
    Object.defineProperty(media, 'enumerateDevices', {
      configurable: true, value: async () => [{ kind: 'audioinput', deviceId: 'synthetic', label: 'Synthetic input' }],
    });
    Object.defineProperty(media, 'getUserMedia', {
      configurable: true, value: async () => {
        micFailureCalls++;
        if (failure !== 'meter') throw new DOMException('Synthetic microphone failure', failure);
        // Fixture source only, never a physical microphone. Keep it distinct
        // from the application-owned contexts observed above.
        const source = new NativeContext();
        const stream = source.createMediaStreamDestination().stream;
        for (const track of stream.getTracks()) {
          const stop = track.stop.bind(track);
          track.stop = () => { stop(); source.close().catch(() => {}); };
          micFailureTracks.push(track);
        }
        return stream;
      },
    });
  }, failure);
  await page.route('**/session.js', route => route.fulfill({
    contentType: 'text/javascript',
    body: `export class WebJoinSession {
      constructor(options) { this.options = options; }
      async join() { this.options.onStatus('Synthetic session connected.'); }
      close() {}
    }`,
  }));
  const expiry = Math.floor(Date.now() / 1000) + 300;
  await page.goto(`/#k=${Buffer.alloc(32, 17).toString('base64url')}&s=${'22'.repeat(32)}&g=1&e=${expiry}`);
  await expect(page.locator('#status')).toHaveText('Synthetic session connected.');
  return external;
}

for (const failure of ['NotAllowedError', 'NotFoundError']) {
  test(`a ${failure} microphone check closes audio before an explicit retry`, async ({ page }) => {
    const external = await openMicFailure(page, failure);
    await page.getByRole('button', { name: 'Use microphone', exact: true }).click();
    await expect(page.locator('#mic-error')).toContainText(failure === 'NotAllowedError'
      ? 'Microphone access was blocked' : 'No microphone found');
    await expect(page.locator('#status')).toHaveText('Synthetic session connected.');
    await expect(page.locator('#join')).toBeHidden();
    await expect.poll(() => page.evaluate(() => micFailureContexts.every(c => c.state !== 'closed'))).toBe(true);
    expect(await page.evaluate(() => micFailureCalls)).toBe(1);
    await page.getByRole('button', { name: 'Try again', exact: true }).click();
    await expect.poll(() => page.evaluate(() => micFailureContexts.every(c => c.state !== 'closed'))).toBe(true);
    expect(await page.evaluate(() => micFailureCalls)).toBe(2);
    expect(await page.evaluate(() => micFailureTracks.length)).toBe(0);
    await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeVisible();
    expect(external).toEqual([]);
  });
}

test('input-meter startup failure stops acquired capture and closes application audio', async ({ page }) => {
  const external = await openMicFailure(page, 'meter');
  await page.getByRole('button', { name: 'Use microphone', exact: true }).click();
  await expect(page.locator('#mic-error')).toContainText('Synthetic input level failure');
  await expect.poll(() => page.evaluate(() => micFailureTracks.map(t => t.readyState))).toEqual(['ended']);
  await expect(page.locator('#status')).toHaveText('Synthetic session connected.');
  await expect(page.locator('#join')).toBeHidden();
  await expect(page.locator('#mic-heard')).toBeHidden();
  await expect(page.locator('#mic-source')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeVisible();
  expect(await page.evaluate(() => micFailureCalls)).toBe(1);
  expect(external).toEqual([]);
});
