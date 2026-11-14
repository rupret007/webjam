import { Mailbox, delay } from "./mailbox.js";
import { hkdf, fromHex, DOMAIN, mailboxId, seal, open, toBase64Url, turnToken } from "./crypto.js";
import { validateVideoCredentials } from "./policy.js";

export class VideoConflict extends Error {}

export class VideoMailbox extends Mailbox {
  constructor(origin, invite, options) {
    super(origin, invite, { interval: 0, timeout: 4000, ...options });
  }

  async credentials() {
    const result = await this.request("/turn-credentials?client=web-video", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_id: this.invite.session, invite_token: await turnToken(this.invite), client: "web-video" }),
    });
    if (result.status !== 200) throw new Error("Video relay unavailable");
    return validateVideoCredentials(result.body);
  }

  async videoPath(round, kind, index) {
    if (round === "direct") throw new Error("Invalid video round");
    return `/video-mailbox/${await mailboxId(this.invite, kind, index, round)}/${kind}?client=web`;
  }

  async write(round, kind, value, nonce) {
    const plaintext = round === "video-refresh-abort" ? value : { round, kind, value };
    const bytes = await seal(this.invite, plaintext, nonce);
    const count = Math.ceil(bytes.length / 2998);
    for (const index of [...Array.from({ length: count - 1 }, (_, i) => i + 1), 0]) {
      const path = await this.videoPath(round, kind, index);
      const payload = toBase64Url(new Uint8Array([index, count, ...bytes.subarray(index * 2998, (index + 1) * 2998)]));
      const response = await this.request(path, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ payload }),
      });
      if (response.status === 201) continue;
      if (response.status === 409) {
        const stored = await this.request(path);
        if (stored.status === 200 && stored.body?.payload === payload) continue;
        throw new VideoConflict("Camera signaling conflict");
      }
      throw new Error("Camera signaling unavailable");
    }
  }

  async read(round, kind) {
    const result = await this.request(await this.videoPath(round, kind, 0));
    if (result.status === 204 || result.status === 410) return null;
    const first = this.chunk(result, 0);
    const parts = [...first.subarray(2)];
    for (let i = 1; i < first[1]; i++) {
      parts.push(...this.chunk(await this.request(await this.videoPath(round, kind, i)), i, first[1]).subarray(2));
    }
    const decoded = await open(this.invite, Uint8Array.from(parts));
    if (round === "video-refresh-abort" && kind === "offer" && decoded.v === 1 && decoded.abort === "refresh") return decoded;
    if (decoded.round !== round || decoded.kind !== kind) throw new Error("Camera signaling did not match this round");
    return decoded.value;
  }

  async wait(round, kind, until, interval) {
    while (Date.now() < until && !this.signal.aborted) {
      const value = await this.read(round, kind);
      if (Date.now() < until && value) return value;
      await delay(Math.min(interval, Math.max(0, until - Date.now())), this.signal);
    }
    return null;
  }

  async abortRefresh() {
    const root = await hkdf(this.invite.capability, fromHex(this.invite.session), DOMAIN);
    const nonce = (await hkdf(root, new Uint8Array(), "webjam/v3/video-refresh-abort/nonce")).slice(0, 12);
    await this.write("video-refresh-abort", "offer", { v: 1, abort: "refresh" }, nonce);
  }
}
