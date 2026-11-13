import { test, expect } from '@playwright/test';
import { JOIN_SIGNAL } from '../../errors.js';

// UI lifecycle only: no provider request, real microphone, peer or audio device.
async function invitation(page) {
  await page.clock.install();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/*', route => new URL(route.request().url()).origin === 'http://127.0.0.1:4174' ? route.continue() : route.abort());
  await page.addInitScript(() => {
    window.accessFixtureTracks = [];
    window.accessFixtureContexts = [];
    window.accessFixtureLifetime = 95000;
    window.AudioContext = class {
      constructor() { this.state = 'suspended'; window.accessFixtureContexts.push(this); }
      async resume() { this.state = 'running'; }
      async close() { this.state = 'closed'; }
    };
    for (const name of ['getUserMedia', 'getDisplayMedia']) if (navigator.mediaDevices?.[name]) {
      navigator.mediaDevices[name] = async () => { throw new Error('Real media excluded from this UI fixture'); };
    }
  });
  await page.route('**/audio.js', route => route.fulfill({contentType:'text/javascript',body:`
    // Keep real teardown while replacing only capture. The query bypasses this
    // exact mock route and the loopback server serves the original module.
    export { closeAudioContext } from './audio.js?access-window-cleanup';
    export async function acquireMusicStream() {
      const track = Object.assign(new EventTarget(), {
        readyState:'live', enabled:true, kind:'audio',
        getSettings:()=>({sampleRate:48000}), stop(){this.readyState='ended';}
      });
      window.accessFixtureTracks.push(track);
      return {stream:{getTracks:()=>[track],getAudioTracks:()=>[track]},warnings:[]};
    }`
  }));
  await page.route('**/session.js', route => route.fulfill({contentType:'text/javascript',body:`
    export class WebJoinSession {
      constructor(options) { this.options=options; this.accessExpiresAt=null; }
      async join() {
        this.accessExpiresAt=Date.now()+window.accessFixtureLifetime;
        window.endAccessFixture=message=>this.options.onError(new Error(message));
        this.options.onStatus('Synthetic authenticated session connected.');
      }
      close() {this.accessExpiresAt=null;(this.options.stream?.getTracks() ?? []).forEach(track=>track.stop());}
    }`
  }));
  const expires = Math.floor(Date.now()/1000)+300;
  await page.goto(`/#k=${Buffer.alloc(32,17).toString('base64url')}&s=${'22'.repeat(32)}&g=1&e=${expires}`);
  await expect(page.locator('#status')).toHaveText('Synthetic authenticated session connected.');
  await expect(page.locator('#heading')).toHaveText("You're listening.");
  await expect(page.locator('#join')).toBeHidden();
}

async function openAudioDetails(page) {
  const details = page.locator('#audio-details');
  if (await details.getAttribute('open') === null) {
    await page.getByText('Audio details', { exact: true }).click();
  }
}

test('access time is visible, warns once near expiry, and stops updating on Leave', async ({ page }, testInfo) => {
  await invitation(page);
  await expect(page.locator('#access-window')).toBeHidden();
  await expect(page.locator('#join')).toBeHidden();
  const leave = page.getByRole('button',{name:'Leave session',exact:true});
  await expect(leave).toBeVisible();
  await expect(leave).toHaveClass(/quiet/);
  await expect(leave).not.toBeFocused();
  await openAudioDetails(page);
  await expect(page.getByRole('timer')).toHaveText('Browser access ends in 1:35.');
  await expect(page.getByRole('timer')).toHaveAttribute('aria-live','off');
  await expect(page.locator('#access-warning')).toBeHidden();
  await page.clock.runFor(35000);
  await expect(page.getByRole('timer')).toHaveText('Browser access ends in 1:00.');
  await expect(page.locator('#access-warning')).toHaveText('Less than a minute of browser access remains. Ask your host for a fresh invite to continue.');
  await page.evaluate(()=>{
    window.warningUpdates=0;
    new MutationObserver(()=>window.warningUpdates++).observe(document.getElementById('access-warning'),{childList:true});
  });
  await page.clock.runFor(5000);
  await expect(page.getByRole('timer')).toHaveText('Browser access ends in 0:55.');
  expect(await page.evaluate(()=>window.warningUpdates)).toBe(0);
  for(const width of [375,390]){
    await page.setViewportSize({width,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await expect(page.getByRole('button',{name:'Leave session',exact:true})).toBeVisible();
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:testInfo.outputPath('access-window-'+width+'.png'),fullPage:true});
  }
  await page.getByRole('button',{name:'Leave session',exact:true}).click();
  await expect(page.locator('#access-window')).toBeHidden();
  await expect(page.getByRole('button',{name:'Join',exact:true})).toBeEnabled();
  await page.evaluate(()=>{
    window.timeUpdatesAfterLeave=0;
    new MutationObserver(()=>window.timeUpdatesAfterLeave++).observe(document.getElementById('access-time'),{childList:true});
  });
  await page.clock.runFor(20000);
  expect(await page.locator('#access-time').textContent()).toBe('');
  expect(await page.evaluate(()=>window.timeUpdatesAfterLeave)).toBe(0);
  expect(await page.evaluate(()=>accessFixtureTracks.every(t=>t.readyState==='ended')&&accessFixtureContexts.every(c=>c.state==='closed'))).toBe(true);
});

test('a fresh attempt has its own access window and a host-end error clears it', async ({ page }) => {
  await invitation(page);
  await openAudioDetails(page);
  await page.clock.runFor(40000);
  await expect(page.locator('#access-warning')).toBeVisible();
  await page.getByRole('button',{name:'Leave session',exact:true}).click();
  await page.evaluate(()=>{window.accessFixtureLifetime=150000;});
  await page.getByRole('button',{name:'Join',exact:true}).click();
  await openAudioDetails(page);
  await expect(page.getByRole('timer')).toHaveText('Browser access ends in 2:30.');
  await expect(page.locator('#access-warning')).toBeHidden();
  await page.clock.runFor(5000);
  await expect(page.getByRole('timer')).toHaveText('Browser access ends in 2:25.');
  await page.evaluate(message=>window.endAccessFixture(message), JOIN_SIGNAL.SESSION_ENDED);
  await expect(page.locator('#access-window')).toBeHidden();
  await expect(page.locator('#error-hint')).toContainText('new browser invite');
  await page.clock.runFor(120000);
  await expect(page.locator('#access-warning')).toBeHidden();
  expect(await page.locator('#access-time').textContent()).toBe('');
});
