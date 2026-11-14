import { connectionRoute } from "./audio.js";
import { VIDEO_BYTES } from "./policy.js";

export function median(values) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function videoPeerOpen(peer) {
  return !!peer && peer.signalingState !== "closed" && peer.connectionState !== "closed" && peer.iceConnectionState !== "closed";
}

export function musicSample(stats, pairId, latency, targetMs) {
  const pair = stats.get(pairId);
  const confirmed = pair?.type === "candidate-pair" && pair.state === "succeeded" && pair.nominated === true;
  const audio = [...stats.values()].find(row => row.type === "inbound-rtp" && (row.kind ?? row.mediaType) === "audio");
  const received = audio?.packetsReceived;
  const lost = audio?.packetsLost;
  const loss = received > 0 && Number.isFinite(lost) ? Math.max(0, lost) / (received + Math.max(0, lost)) : null;
  return {
    latency, targetMs, received, loss, pairId: confirmed ? pairId : null,
    rttMs: confirmed && Number.isFinite(pair.currentRoundTripTime) && pair.currentRoundTripTime >= 0 ? pair.currentRoundTripTime * 1000 : null,
    route: confirmed ? connectionRoute(stats, pairId) : null,
  };
}

export function musicVideoTrip(peer, current, baseline, latencyWindow, previousPath = baseline) {
  if (!videoPeerOpen(peer) || !baseline) return false;
  const metrics = current();
  const movedOntoRelay = !!metrics.pairId && metrics.route === "relayed" &&
    (previousPath.route === "direct" ||
     (!previousPath.route && !!previousPath.pairId && previousPath.pairId !== metrics.pairId));
  return (latencyWindow.length >= 5 && median(latencyWindow) > baseline.latency + 10) ||
    movedOntoRelay ||
    (!!metrics.pairId && !!baseline.pairId && metrics.pairId !== baseline.pairId &&
      Number.isFinite(metrics.rttMs) && Number.isFinite(baseline.rttMs) && metrics.rttMs > baseline.rttMs + 10) ||
    (Number.isFinite(metrics.loss) && Number.isFinite(baseline.loss) && metrics.received >= 50 && metrics.loss > baseline.loss + 0.01) ||
    metrics.targetMs > baseline.targetMs;
}

export function videoRelayBudget(stats, state) {
  const transports = [...stats.values()].filter(row => row.type === "transport");
  if (transports.length !== 1) return null;
  const { selectedCandidatePairId, bytesSent, bytesReceived } = transports[0];
  const pair = stats.get(selectedCandidatePairId);
  if (pair?.nominated !== true || pair.state !== "succeeded") return null;
  const route = connectionRoute(stats, selectedCandidatePairId);
  if (!route) return null;
  const total = bytesSent + bytesReceived;
  if (![bytesSent, bytesReceived, total].every(n => Number.isSafeInteger(n) && n >= 0) || total < state.watermark) {
    return route === "relayed" ? "stop" : null;
  }
  if (route === "relayed") state.counted += total - state.watermark;
  state.watermark = total;
  if (state.counted >= VIDEO_BYTES * 0.9) return "stop";
  return state.counted >= VIDEO_BYTES * 0.75 ? "warning" : null;
}
