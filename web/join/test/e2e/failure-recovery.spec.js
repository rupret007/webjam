import { test, expect } from '@playwright/test';

async function openInvitation(page, width = 390) {
  await page.setViewportSize({ width, height: 844 });
  await page.clock.install();
  await page.addInitScript(() => {
    window.captureTracks = [];
    const nativeContext = window.AudioContext;
    window.audioContexts = [];
    window.AudioContext = class extends nativeContext {
      constructor(...args) { super(...args); window.audioContexts.push(this); }
    };
    Object.defineProperty(Object.getPrototypeOf(navigator.mediaDevices), 'getUserMedia', {
      configurable: true,
      value: async () => {
        // Synthetic silent audio only; never request the tester's microphone.
        const context = new AudioContext();
        const stream = context.createMediaStreamDestination().stream;
        for (const track of stream.getTracks()) {
          const stop = track.stop.bind(track);
          track.stop = () => { stop(); context.close().catch(() => {}); };
          window.captureTracks.push(track);
        }
        return stream;
      }
    });
  });
  const expires = Math.floor(Date.now() / 1000) + 300;
  // Local test-only capability, with no corresponding host or mailbox.
  await page.goto(`/#k=${Buffer.alloc(32, 17).toString('base64url')}&s=${'22'.repeat(32)}&g=1&e=${expires}`);
  await page.getByRole('checkbox').check();
}

for (const width of [375, 390]) {
  test(`a relay timeout releases audio and permits an explicit retry at ${width}px`, async ({ page }) => {
    let requests = 0;
    await page.route('https://webjam-turn-credentials.jeffstory007.workers.dev/**', async (route) => {
      requests++;
      if (requests > 1) await route.fulfill({ status: 503, body: 'unavailable' });
      // First fetch stalls until the real Mailbox deadline aborts it.
    });
    await openInvitation(page, width);
    const join = page.getByRole('button', { name: 'Join with microphone', exact: true });
    const leave = page.getByRole('button', { name: 'Leave session', exact: true });
    await join.click();
    await expect.poll(() => requests).toBe(1);
    await expect(leave).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.clock.fastForward(13000);
    await expect(page.locator('#status')).toContainText('timed out');
    await expect(join).toBeEnabled();
    await expect(leave).toBeHidden();
    await expect.poll(() => page.evaluate(() => captureTracks.length > 0 && captureTracks.every(track => track.readyState === 'ended'))).toBe(true);
    await expect.poll(() => page.evaluate(() => audioContexts.every(context => context.state === 'closed'))).toBe(true);
    await join.click();
    await expect.poll(() => requests).toBe(2);
    await expect(page.locator('#status')).toContainText('Web Join is unavailable');
    await expect(join).toBeEnabled();
    expect(await page.evaluate(() => captureTracks.every(track => track.readyState === 'ended'))).toBe(true);
  });
}

test('Leave during a stalled fetch stays a deliberate exit, then allows another Join', async ({ page }) => {
  let requests = 0;
  await page.route('https://webjam-turn-credentials.jeffstory007.workers.dev/**', async (route) => {
    requests++;
    if (requests > 1) await route.fulfill({ status: 503, body: 'unavailable' });
  });
  await openInvitation(page);
  const join = page.getByRole('button', { name: 'Join with microphone', exact: true });
  await join.click();
  await expect.poll(() => requests).toBe(1);
  await page.getByRole('button', { name: 'Leave session', exact: true }).click();
  await page.clock.fastForward(13000);
  await expect(page.locator('#status')).toHaveText('You left the session.');
  await expect(join).toBeEnabled();
  expect(requests).toBe(1);
  expect(await page.evaluate(() => captureTracks.every(track => track.readyState === 'ended'))).toBe(true);
  await join.click();
  await expect.poll(() => requests).toBe(2);
  await expect(page.locator('#status')).toContainText('Web Join is unavailable');
});

for (const scenario of ['left', 'rejoined', 'active']) {
  const outcome = { left: 'a deliberate Leave', rejoined: 'the next Join', active: 'cleanup for the active Join' }[scenario];
  test(`a late playback rejection preserves ${outcome}`, async ({ page }) => {
    // Isolate the page's media-promise ownership from enrollment, which the
    // separate real Go/Pion cases exercise. No hosted credentials or mic.
    await page.route('**/session.js', route => route.fulfill({
      contentType: 'application/javascript',
      body: `export class WebJoinSession {
        constructor(options) {
          this.options = options; this.closed = false;
          (window.testSessions ??= []).push(this);
        }
        async join() {
          this.options.onStatus('Synthetic session connected.');
          this.options.onTrack(new MediaStream());
        }
        close() {
          this.closed = true;
          this.options.stream.getTracks().forEach(track => track.stop());
        }
      }`
    }));
    await page.addInitScript(() => {
      window.rejectPlayback = [];
      HTMLMediaElement.prototype.play = function () {
        return new Promise((resolve, reject) => window.rejectPlayback.push(reject));
      };
    });
    await openInvitation(page);
    const join = page.getByRole('button', { name: 'Join with microphone', exact: true });
    const leave = page.getByRole('button', { name: 'Leave session', exact: true });
    await join.click();
    await expect.poll(() => page.evaluate(() => rejectPlayback.length)).toBe(1);
    if (scenario !== 'active') await leave.click();
    if (scenario === 'rejoined') {
      await join.click();
      await expect.poll(() => page.evaluate(() => testSessions.length)).toBe(2);
      await expect(page.locator('#status')).toHaveText('Synthetic session connected.');
    }
    await page.evaluate(() => rejectPlayback[0](new DOMException('Test-only interrupted playback', 'AbortError')));
    const status = { left: 'You left the session.', rejoined: 'Synthetic session connected.', active: 'Playback could not start. Tap Join again.' }[scenario];
    await expect(page.locator('#status')).toHaveText(status);
    if (scenario === 'rejoined') {
      await expect(join).toBeDisabled();
      await expect(leave).toBeVisible();
      expect(await page.evaluate(() => testSessions[1].closed)).toBe(false);
      expect(await page.evaluate(() => testSessions[1].options.stream.getTracks().every(track => track.readyState === 'live'))).toBe(true);
      await leave.click();
    } else {
      await expect(join).toBeEnabled();
      await expect(leave).toBeHidden();
    }
    expect(await page.evaluate(() => captureTracks.every(track => track.readyState === 'ended'))).toBe(true);
  });
}

for (const scenario of ['left', 'rejoined', 'active']) {
  const outcome = { left: 'a deliberate Leave', rejoined: 'the next Join', active: 'cleanup for the active Join' }[scenario];
  test(`a late audio-resume rejection preserves ${outcome}`, async ({ page }) => {
    await page.route('**/session.js', route => route.fulfill({
      contentType: 'application/javascript',
      body: `export class WebJoinSession {
        constructor(options) {
          this.options = options; this.closed = false;
          (window.testSessions ??= []).push(this);
        }
        async join() { this.options.onStatus('Synthetic session connected.'); }
        close() {
          this.closed = true;
          this.options.stream.getTracks().forEach(track => track.stop());
        }
      }`
    }));
    await openInvitation(page);
    const join = page.getByRole('button', { name: 'Join with microphone', exact: true });
    const leave = page.getByRole('button', { name: 'Leave session', exact: true });
    await join.click();
    await expect(page.locator('#status')).toHaveText('Synthetic session connected.');
    await page.evaluate(async () => {
      const audio = testSessions[0].options.audioContext;
      await audio.suspend();
      audio.resume = () => new Promise((resolve, reject) => { window.rejectResume = reject; });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect.poll(() => page.evaluate(() => typeof rejectResume)).toBe('function');
    if (scenario !== 'active') await leave.click();
    if (scenario === 'rejoined') {
      await join.click();
      await expect.poll(() => page.evaluate(() => testSessions.length)).toBe(2);
      await expect(page.locator('#status')).toHaveText('Synthetic session connected.');
    }
    await page.evaluate(() => rejectResume(new DOMException('Test-only interrupted resume', 'InvalidStateError')));
    const status = { left: 'You left the session.', rejoined: 'Synthetic session connected.', active: 'Tap Join again to resume audio.' }[scenario];
    await expect(page.locator('#status')).toHaveText(status, { timeout: 2000 });
    if (scenario === 'rejoined') {
      await expect(join).toBeDisabled();
      await expect(leave).toBeVisible();
      expect(await page.evaluate(() => testSessions[1].closed)).toBe(false);
      expect(await page.evaluate(() => testSessions[1].options.stream.getTracks().every(track => track.readyState === 'live'))).toBe(true);
      await leave.click();
    } else {
      await expect(join).toBeEnabled();
      await expect(leave).toBeHidden();
    }
    expect(await page.evaluate(() => captureTracks.every(track => track.readyState === 'ended'))).toBe(true);
  });
}
