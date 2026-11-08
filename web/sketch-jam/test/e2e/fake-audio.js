// Use deterministic native Web Audio tracks in Chromium. Playwright's WebKit
// build enables mock capture devices; native capture also unlocks host ICE.
// RTCPeerConnection, ICE, DTLS, SRTP, encoders, and receivers remain real.
export async function installFakeAudio(context, { nativeMockCapture = false } = {}) {
  await context.addInitScript((useNativeMock) => {
    window.__captureCalls = [];
    window.__captureTracks = [];
    window.__connections = [];
    window.__audioContexts = [];
    const NativePeerConnection = window.RTCPeerConnection;
    window.RTCPeerConnection = class ObservedPeerConnection extends NativePeerConnection {
      constructor(configuration) {
        // Host ICE candidates suffice on loopback. Do not contact public STUN
        // or TURN servers while exercising the actual browser RTC stack.
        super({ ...configuration, iceServers: [] });
        window.__connections.push(this);
      }
    };
    // WebKit can recreate the navigator.mediaDevices wrapper between reads,
    // dropping own-property overrides. Patch its stable prototype instead.
    const mediaPrototype = Object.getPrototypeOf(navigator.mediaDevices);
    const nativeCapture = mediaPrototype.getUserMedia;
    Object.defineProperty(mediaPrototype, "getUserMedia", {
      configurable: true,
      value: async (constraints) => {
        window.__captureCalls.push(constraints);
        if (useNativeMock) {
          const stream = await nativeCapture.call(navigator.mediaDevices, constraints);
          window.__captureTracks.push(...stream.getTracks());
          return stream;
        }
        const audio = new AudioContext({ sampleRate: 48000 });
        window.__audioContexts.push(audio);
        await audio.resume();
        const oscillator = audio.createOscillator();
        const gain = audio.createGain();
        const output = audio.createMediaStreamDestination();
        oscillator.frequency.value = 440;
        gain.gain.value = 0.05;
        oscillator.connect(gain).connect(output);
        oscillator.start();
        window.__captureTracks.push(...output.stream.getTracks());
        return output.stream;
      },
    });
  }, nativeMockCapture);
}
