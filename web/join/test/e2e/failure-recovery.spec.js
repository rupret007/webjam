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
  await page.getByRole('button', { name: 'Skip check' }).click();
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
    const retry = page.getByRole('button', { name: 'Retry', exact: true });
    await expect(retry).toBeEnabled();
    await expect(join).toBeHidden();
    await expect(leave).toBeHidden();
    await expect.poll(() => page.evaluate(() => captureTracks.length > 0 && captureTracks.every(track => track.readyState === 'ended'))).toBe(true);
    await expect.poll(() => page.evaluate(() => audioContexts.every(context => context.state === 'closed'))).toBe(true);
    await retry.click();
    await expect.poll(() => requests).toBe(2);
    await expect(page.locator('#status')).toContainText('Web Join is unavailable');
    await expect(retry).toBeEnabled();
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
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled();
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

for (const outcome of ['rejoining', 'failed']) {
  test(`retired enrollment callbacks preserve the ${outcome} attempt`, async ({ page }) => {
    await page.route('**/session.js', route => route.fulfill({ contentType: 'text/javascript', body: `
      export class WebJoinSession {
        constructor(options) { this.options = options; this.id = (window.pendingSessions ??= []).push(this); }
        join() {
          this.options.onStatus('Attempt ' + this.id + ' pending.');
          return new Promise(resolve => { this.finish = () => {
            this.options.onStatus('Stale completed enrollment.'); this.options.onLatency(999);
            this.options.onError(new Error('Stale enrollment error.')); resolve();
          }; });
        }
        close() { this.closed = true; this.options.stream.getTracks().forEach(t => t.stop()); }
      }` }));
    await openInvitation(page);
    const join = page.getByRole('button', { name: 'Join with microphone', exact: true });
    const leave = page.getByRole('button', { name: 'Leave session', exact: true });
    await join.click(); await expect(page.locator('#status')).toHaveText('Attempt 1 pending.');
    await leave.click(); await join.click();
    await expect(page.locator('#status')).toHaveText('Attempt 2 pending.');
    if (outcome === 'failed') await page.evaluate(() => pendingSessions[1].options.onError(new Error('Current enrollment failed.')));
    await page.evaluate(() => pendingSessions[0].finish());
    await expect(page.locator('#status')).toHaveText(outcome === 'failed' ? 'Current enrollment failed.' : 'Attempt 2 pending.');
    await expect(page.locator('#latency')).not.toContainText('999');
    if (outcome === 'failed') await expect(page.locator('#retry')).toBeVisible();
    else { expect(await page.evaluate(() => !!pendingSessions[1].closed)).toBe(false); await leave.click(); }
    expect(await page.evaluate(() => captureTracks.every(track => track.readyState === 'ended'))).toBe(true);
  });
}

test('a retired initial resume cannot enable Join while the next resume is pending', async ({ page }) => {
  await openInvitation(page);
  await page.evaluate(() => {
    window.pendingResumes = [];
    const nativeResume = AudioContext.prototype.resume;
    AudioContext.prototype.resume = function () {
      // Delay the JS completion, preserving WebKit's real context lifecycle.
      const resumed = nativeResume.call(this);
      resumed.catch(() => {});
      return new Promise((resolve, reject) => pendingResumes.push(() => resumed.then(resolve, reject)));
    };
  });
  const join = page.getByRole('button', { name: 'Join with microphone', exact: true });
  await join.click(); await expect.poll(() => page.evaluate(() => pendingResumes.length)).toBe(1);
  await expect(page.locator('#check-mic')).toBeDisabled();
  await page.getByRole('button', { name: 'Leave session', exact: true }).click();
  await join.click(); await expect.poll(() => page.evaluate(() => pendingResumes.length)).toBe(2);
  await page.evaluate(() => pendingResumes[0]());
  await expect(join).toBeDisabled();
  await page.getByRole('button', { name: 'Leave session', exact: true }).click();
  await page.evaluate(() => pendingResumes[1]());
  await expect.poll(() => page.evaluate(() => audioContexts.every(context => context.state === 'closed'))).toBe(true);
});

test('capture arriving after Leave releases only its own tracks during the next Join', async ({ page }) => {
  await openInvitation(page);
  await page.evaluate(() => {
    window.pendingCaptures = [];
    const capture = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async () => {
      const stream = await capture();
      return new Promise(resolve => pendingCaptures.push(() => resolve(stream)));
    };
  });
  const join = page.getByRole('button', { name: 'Join with microphone', exact: true });
  const leave = page.getByRole('button', { name: 'Leave session', exact: true });
  await join.click(); await expect.poll(() => page.evaluate(() => pendingCaptures.length)).toBe(1);
  await leave.click(); await join.click();
  await expect.poll(() => page.evaluate(() => pendingCaptures.length)).toBe(2);
  await page.evaluate(() => pendingCaptures[0]());
  await expect.poll(() => page.evaluate(() => captureTracks[0].readyState)).toBe('ended');
  expect(await page.evaluate(() => captureTracks[1].readyState)).toBe('live');
  await expect(join).toBeDisabled();
  await expect(page.locator('#status')).toHaveText('Allow microphone access to play with your host.');
  await leave.click(); await page.evaluate(() => pendingCaptures[1]());
  await expect.poll(() => page.evaluate(() => captureTracks.every(track => track.readyState === 'ended'))).toBe(true);
  await expect.poll(() => page.evaluate(() => audioContexts.every(context => context.state === 'closed'))).toBe(true);
});
