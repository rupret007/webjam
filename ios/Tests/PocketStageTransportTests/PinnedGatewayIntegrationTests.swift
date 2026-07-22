import Foundation
import Testing
import PocketStageProtocol
@testable import PocketStageTransport

@Test(
    .enabled(
        if: ProcessInfo.processInfo.environment["WEBJAM_RUN_SWIFT_POCKET_STAGE_INTEGRATION"] == "1",
        "Requires the opt-in live Python gateway harness"
    )
)
@MainActor
func livePinnedGatewayPairing() async throws {
    let environment = ProcessInfo.processInfo.environment
    guard let rawPairingCode = environment["WEBJAM_POCKET_STAGE_PAIRING_CODE"] else {
        Issue.record("The live integration gate did not provide a pairing code.")
        return
    }

    let payload = try PairingPayload.parseQRCode(rawPairingCode)
    let socket = StageSocket(
        url: payload.endpoint.url,
        certificatePin: payload.certificateFingerprint.digest
    )
    var streamContinuation: AsyncStream<StageSocket.Event>.Continuation!
    let events = AsyncStream<StageSocket.Event> { streamContinuation = $0 }
    socket.onEvent = { streamContinuation.yield($0) }
    socket.connect()
    defer {
        socket.disconnect()
        streamContinuation.finish()
    }

    for await event in events {
        switch event {
        case .opened:
            let pairBody = try PairBody(
                capability: payload.token,
                claimID: CanonicalUUID()
            )
            let pair = try StageEnvelope(
                kind: .pair,
                generation: 0,
                sequence: 0,
                body: pairBody
            )
            do {
                try await socket.send(text: WireCodec.encodeText(pair))
            } catch {
                Issue.record("The pinned WebSocket opened but the pair frame failed.")
                return
            }
        case let .message(.snapshot(snapshot)):
            #expect(snapshot.body.schema == 1)
            #expect(snapshot.sequence == 1)
            return
        case .message:
            Issue.record("The desktop sent a non-snapshot message before pairing completed.")
            return
        case let .failed(message):
            Issue.record("Pinned WebSocket failed: \(message)")
            return
        case .closed:
            Issue.record("Pinned WebSocket closed before the first snapshot.")
            return
        }
    }
}
