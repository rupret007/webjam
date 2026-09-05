import Foundation
import Testing
import PocketStageProtocol
@testable import PocketStageTransport

@MainActor
private final class MockStageSocket: StageSocketClient, @unchecked Sendable {
    var onEvent: (@MainActor @Sendable (StageSocketEvent) -> Void)?
    private(set) var connectCount = 0
    private(set) var disconnectCount = 0
    private(set) var sentTexts: [String] = []
    var sendError: Error?

    func connect() { connectCount += 1 }
    func disconnect() { disconnectCount += 1 }

    func send(text: String) async throws {
        if let sendError { throw sendError }
        sentTexts.append(text)
    }

    func emit(_ event: StageSocketEvent) { onEvent?(event) }
}

@MainActor
private final class MockSocketFactory {
    private(set) var sockets: [MockStageSocket] = []

    func make(url _: URL, pin _: Data) -> any StageSocketClient {
        let socket = MockStageSocket()
        sockets.append(socket)
        return socket
    }
}

@MainActor
private final class AdjustableStageClock {
    var elapsed: Duration = .zero

    var timing: StageConnectionTiming {
        StageConnectionTiming(
            now: { [weak self] in self?.elapsed ?? .zero },
            sleep: { _ in try await Task.sleep(for: .milliseconds(1)) }
        )
    }
}

@MainActor
private func makeModel(
    factory: MockSocketFactory,
    clock: AdjustableStageClock? = nil
) -> StageConnectionModel {
    let clock = clock ?? AdjustableStageClock()
    return StageConnectionModel(
        socketFactory: { url, pin in factory.make(url: url, pin: pin) },
        timing: clock.timing
    )
}

private func pairingCode(
    tokenCharacter: Character = "A",
    expires: Int64 = 4_102_444_800,
    name: String = "Test Stage"
) throws -> String {
    try PairingPayload(
        sessionID: UUID(),
        endpoint: StageEndpoint("wss://192.168.1.44:9443/v1/pocket"),
        certificateFingerprint: CertificateFingerprint(
            hex: String(repeating: "ab", count: 32)
        ),
        token: String(repeating: String(tokenCharacter), count: 43),
        expiresAtUnix: expires,
        displayName: name
    ).qrCodeString()
}

private func snapshotMessage(
    sequence: Int,
    generation: Int = 7,
    revision: Int = 10,
    role: String = "host",
    phase: String = "live",
    primaryAction: String = "record",
    primaryEnabled: Bool = true,
    recordingState: String = "idle",
    label: String = "Guitar"
) throws -> IncomingMessage {
    let root: [String: Any] = [
        "version": 1,
        "kind": "snapshot",
        "message_id": UUID().uuidString.lowercased(),
        "generation": generation,
        "sequence": sequence,
        "sent_at_unix_ms": 1_700_000_000_000 as Int64,
        "body": [
            "schema": 1,
            "generation": generation,
            "revision": revision,
            "role": role,
            "phase": phase,
            "primary_action": primaryAction,
            "primary_enabled": primaryEnabled,
            "recording_state": recordingState,
            "participants": [[
                "slot": 1,
                "label": label,
                "fader_level": 70,
                "pan": 50,
                "muted": false,
                "solo": false,
                "is_local": true,
                "connection_state": "ready",
            ]],
            "sections": [[
                "ordinal": 1,
                "label": "Verse",
                "start_ms": 0,
                "end_ms": 30_000,
            ]],
            "current_section_ordinal": 1,
            "cue": "Verse",
        ],
    ]
    return try WireCodec.decodeIncomingMessage(
        JSONSerialization.data(withJSONObject: root, options: [.sortedKeys])
    )
}

private func receiptMessage(
    sequence: Int,
    commandID: CanonicalUUID,
    status: String,
    revision: Int,
    reason: String = "none"
) throws -> IncomingMessage {
    let root: [String: Any] = [
        "version": 1,
        "kind": "receipt",
        "message_id": UUID().uuidString.lowercased(),
        "generation": 7,
        "sequence": sequence,
        "sent_at_unix_ms": 1_700_000_000_000 as Int64,
        "body": [
            "command_id": commandID.string,
            "status": status,
            "generation": 7,
            "revision": revision,
            "reason": reason,
        ],
    ]
    return try WireCodec.decodeIncomingMessage(
        JSONSerialization.data(withJSONObject: root, options: [.sortedKeys])
    )
}

private func outgoingMessage(_ text: String) throws -> IncomingMessage {
    try WireCodec.decodeIncomingMessage(Data(text.utf8))
}

@MainActor
private func eventually(
    _ condition: @escaping @MainActor () -> Bool
) async -> Bool {
    for _ in 0..<500 {
        if condition() { return true }
        try? await Task.sleep(for: .milliseconds(2))
    }
    return false
}

@MainActor
private func connect(
    _ model: StageConnectionModel,
    factory: MockSocketFactory,
    code: String,
    snapshot: IncomingMessage
) async throws -> MockStageSocket {
    guard model.pair(with: code), let socket = factory.sockets.last else {
        throw ProtocolValidationError.invalidMessage
    }
    socket.emit(.opened)
    guard await eventually({ socket.sentTexts.count == 1 }) else {
        throw ProtocolValidationError.invalidMessage
    }
    socket.emit(.message(snapshot))
    guard await eventually({ model.phase == .connected }) else {
        throw ProtocolValidationError.invalidMessage
    }
    return socket
}

@Test
@MainActor
func invalidAndExpiredCodesNeverCreateASocketOrLeakTheirToken() throws {
    let factory = MockSocketFactory()
    let model = makeModel(factory: factory)
    let secret = String(repeating: "S", count: 43)

    #expect(!model.pair(with: "not-a-code-\(secret)"))
    #expect(factory.sockets.isEmpty)
    guard case let .failed(message) = model.phase else {
        Issue.record("Invalid input did not produce a bounded failure state.")
        return
    }
    #expect(!message.contains(secret))

    #expect(!model.pair(with: try pairingCode(expires: 1)))
    #expect(factory.sockets.isEmpty)
}

@Test
@MainActor
func replacementRetiresOldSocketAndSendsOneClaim() async throws {
    let factory = MockSocketFactory()
    let model = makeModel(factory: factory)
    #expect(model.pair(with: try pairingCode(tokenCharacter: "A")))
    let first = try #require(factory.sockets.first)

    #expect(model.pair(with: try pairingCode(tokenCharacter: "B")))
    let second = try #require(factory.sockets.last)
    #expect(first !== second)
    #expect(first.disconnectCount == 1)

    first.emit(.opened)
    first.emit(.failed("Old socket failure"))
    await Task.yield()
    #expect(first.sentTexts.isEmpty)
    #expect(model.phase == .connecting)

    second.emit(.opened)
    #expect(await eventually { second.sentTexts.count == 1 })
    guard case let .pair(pair) = try outgoingMessage(second.sentTexts[0]) else {
        Issue.record("The first outgoing frame was not a pair claim.")
        return
    }
    #expect(pair.body.capability == String(repeating: "B", count: 43))

    second.emit(.message(try snapshotMessage(sequence: 1)))
    #expect(await eventually { model.phase == .connected })
    #expect(second.sentTexts.count == 1)
}

@Test
@MainActor
func authoritativeSnapshotsUpdateStateAndRejectRevisionRollback() async throws {
    let factory = MockSocketFactory()
    let model = makeModel(factory: factory)
    let socket = try await connect(
        model,
        factory: factory,
        code: pairingCode(),
        snapshot: snapshotMessage(sequence: 1, revision: 10)
    )

    #expect(model.revision == 10)
    #expect(model.participants.first?.label == "Guitar")
    #expect(model.cue == "Verse")

    socket.emit(.message(try snapshotMessage(
        sequence: 2,
        revision: 11,
        label: "Bass"
    )))
    #expect(await eventually { model.revision == 11 })
    #expect(model.participants.first?.label == "Bass")

    socket.emit(.message(try snapshotMessage(sequence: 3, revision: 10)))
    #expect(await eventually {
        if case .failed = model.phase { return true }
        return false
    })
    #expect(socket.disconnectCount == 1)
    guard case let .failed(message) = model.phase else { return }
    #expect(message.contains("moved backward"))
    #expect(model.participants.isEmpty)
}

@Test
@MainActor
func commandsStaySingleFlightUntilReceiptAndSnapshotAgree() async throws {
    let factory = MockSocketFactory()
    let model = makeModel(factory: factory)
    let socket = try await connect(
        model,
        factory: factory,
        code: pairingCode(),
        snapshot: snapshotMessage(sequence: 1, revision: 10)
    )

    model.setMute(slot: 1, value: true, observedGeneration: 7, observedRevision: 10)
    #expect(await eventually { socket.sentTexts.count == 2 })
    guard case let .command(command) = try outgoingMessage(socket.sentTexts[1]) else {
        Issue.record("Expected a command frame.")
        return
    }
    #expect(model.controlBusy)

    model.setFader(slot: 1, value: 20, observedGeneration: 7, observedRevision: 10)
    #expect(socket.sentTexts.count == 2)
    #expect(model.commandIssue?.contains("Wait") == true)

    socket.emit(.message(try receiptMessage(
        sequence: 2,
        commandID: command.body.commandID,
        status: "accepted",
        revision: 11
    )))
    await Task.yield()
    #expect(model.controlBusy)

    socket.emit(.message(try snapshotMessage(sequence: 3, revision: 11)))
    #expect(await eventually { !model.controlBusy })

    model.setMute(slot: 1, value: false, observedGeneration: 7, observedRevision: 11)
    #expect(await eventually { socket.sentTexts.count == 3 })
    guard case let .command(rejected) = try outgoingMessage(socket.sentTexts[2]) else {
        Issue.record("Expected the second command frame.")
        return
    }
    socket.emit(.message(try receiptMessage(
        sequence: 4,
        commandID: rejected.body.commandID,
        status: "rejected",
        revision: 12,
        reason: "stale_revision"
    )))
    await Task.yield()
    #expect(model.controlBusy)
    #expect(model.commandIssue?.contains("stale revision") == true)

    socket.emit(.message(try snapshotMessage(sequence: 5, revision: 12)))
    #expect(await eventually { !model.controlBusy })
}

@Test
@MainActor
func recordingReceiptDoesNotClaimRecorderSuccess() async throws {
    let factory = MockSocketFactory()
    let model = makeModel(factory: factory)
    let socket = try await connect(
        model,
        factory: factory,
        code: pairingCode(),
        snapshot: snapshotMessage(sequence: 1, revision: 10)
    )

    model.requestRecording(
        displayedPrimaryAction: "record",
        observedGeneration: 7,
        observedRevision: 10
    )
    #expect(await eventually { socket.sentTexts.count == 2 })
    guard case let .command(command) = try outgoingMessage(socket.sentTexts[1]) else {
        Issue.record("Expected a recording command frame.")
        return
    }
    #expect(model.recordingCommandPending)

    socket.emit(.message(try receiptMessage(
        sequence: 2,
        commandID: command.body.commandID,
        status: "confirmed",
        revision: 10
    )))
    #expect(await eventually { !model.controlBusy })
    #expect(model.recordingCommandPending)
    #expect(!model.canControlRecording)

    socket.emit(.message(try snapshotMessage(
        sequence: 3,
        revision: 11,
        primaryAction: "stop_recording",
        recordingState: "recording"
    )))
    #expect(await eventually { !model.recordingCommandPending })
    #expect(model.recordingState == "recording")
}

@Test
@MainActor
func injectedClockExpiresAStuckCommandWithoutDroppingTheJam() async throws {
    let factory = MockSocketFactory()
    let clock = AdjustableStageClock()
    let model = makeModel(factory: factory, clock: clock)
    let socket = try await connect(
        model,
        factory: factory,
        code: pairingCode(),
        snapshot: snapshotMessage(sequence: 1, revision: 10)
    )

    model.setMute(slot: 1, value: true, observedGeneration: 7, observedRevision: 10)
    #expect(await eventually { socket.sentTexts.count == 2 })
    guard case let .command(command) = try outgoingMessage(socket.sentTexts[1]) else {
        Issue.record("Expected a command frame.")
        return
    }

    clock.elapsed = .seconds(4)
    socket.emit(.message(try snapshotMessage(sequence: 2, revision: 11)))
    #expect(await eventually { model.revision == 11 })
    clock.elapsed = .seconds(9)

    #expect(await eventually { model.latestCanceledCommandID == command.body.commandID.string })
    #expect(model.phase == .connected)
    #expect(!model.controlBusy)
    #expect(model.commandIssue?.contains("did not finish") == true)
}

@Test
@MainActor
func backgroundingRetiresControlsAndRequiresFreshPairing() async throws {
    let factory = MockSocketFactory()
    let model = makeModel(factory: factory)
    let socket = try await connect(
        model,
        factory: factory,
        code: pairingCode(),
        snapshot: snapshotMessage(sequence: 1)
    )

    model.setMute(slot: 1, value: true, observedGeneration: 7, observedRevision: 10)
    #expect(await eventually { model.controlBusy })
    model.interruptForBackground()

    guard case let .failed(message) = model.phase else {
        Issue.record("Backgrounding did not retire the connection.")
        return
    }
    #expect(message.contains("fresh code"))
    #expect(socket.disconnectCount == 1)
    #expect(!model.canSendControls)
    #expect(!model.controlBusy)
    #expect(model.participants.isEmpty)

    socket.emit(.message(try snapshotMessage(sequence: 2, revision: 11)))
    await Task.yield()
    #expect(model.participants.isEmpty)

    #expect(model.pair(with: try pairingCode(tokenCharacter: "C")))
    #expect(factory.sockets.count == 2)
}


@Test
@MainActor
func desktopRecoverySnapshotsNeverEnablePhoneRecordingCommands() async throws {
    for action in ["paste_new_invite", "close_setup"] {
        let factory = MockSocketFactory()
        let model = makeModel(factory: factory)
        let socket = try await connect(
            model,
            factory: factory,
            code: pairingCode(),
            snapshot: snapshotMessage(sequence: 1, phase: "indeterminate", primaryAction: action)
        )
        #expect(model.phase == .connected)
        #expect(model.primaryAction == action)
        #expect(!model.canControlRecording)
        model.requestRecording(
            displayedPrimaryAction: action,
            observedGeneration: 7,
            observedRevision: 10
        )
        #expect(socket.sentTexts.count == 1)
        model.disconnect()
    }
}
