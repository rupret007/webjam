import Foundation
import Combine
import PocketStageProtocol

enum ConnectionPhase: Equatable {
    case unpaired
    case connecting
    case connected
    case failed(String)

    var label: String {
        switch self {
        case .unpaired: "Not paired"
        case .connecting: "Connecting…"
        case .connected: "Live"
        case .failed: "Connection issue"
        }
    }
}

private struct QueuedStageCommand {
    let commandID: CanonicalUUID
    let command: StageCommand
    let arguments: [String: JSONValue]
    let coalescingKey: String?
    let generation: Int
    let expectedRevision: Int
}

private struct InFlightStageCommand {
    let intent: QueuedStageCommand
    let sentAt: ContinuousClock.Instant
    var receiptStatus: CommandStatus?
    var receiptRevision: Int?
}

@MainActor
final class StageConnectionModel: ObservableObject {
    @Published private(set) var phase: ConnectionPhase = .unpaired
    @Published private(set) var sessionTitle = "Pocket Stage"
    @Published private(set) var role = ""
    @Published private(set) var primaryAction = ""
    @Published private(set) var primaryEnabled = false
    @Published private(set) var isLive = false
    @Published private(set) var revision = 0
    @Published private(set) var recordingState = "not_started"
    @Published private(set) var latestReceipt: ReceiptBody?
    @Published private(set) var participants: [StageParticipant] = []
    @Published private(set) var sections: [StageSection] = []
    @Published private(set) var currentSectionOrdinal: Int?
    @Published private(set) var cue = ""
    @Published private(set) var commandIssue: String?
    @Published private(set) var commandStatus: String?
    @Published private(set) var recordingCommandPending = false
    @Published private(set) var controlBusy = false
    @Published private(set) var latestCanceledCommandID: String?

    var canSendControls: Bool {
        phase == .connected && !controlBusy
    }

    var generation: Int { currentGeneration }

    var canControlRecording: Bool {
        phase == .connected
            && role == "host"
            && primaryEnabled
            && !controlBusy
            && !recordingCommandPending
            && ["record", "stop_recording"].contains(primaryAction)
    }

    var recordingActionLabel: String {
        switch primaryAction {
        case "record": "Start Recording"
        case "stop_recording": "Stop Recording"
        default: "Recording Unavailable"
        }
    }

    private var socket: StageSocket?
    private var pendingPairing: PairingPayload?
    private var pairClaimID = CanonicalUUID()
    private var sendTail: Task<Void, Never>?
    private var freshnessTask: Task<Void, Never>?
    private let clock = ContinuousClock()
    private var lastMessageAt: ContinuousClock.Instant?
    private var commandQueue: [QueuedStageCommand] = []
    private var inFlightCommand: InFlightStageCommand?

    var hasActiveConnection: Bool {
        phase == .connecting || phase == .connected
    }

    @discardableResult
    func pair(with rawQRCode: String) -> Bool {
        do {
            let payload = try PairingPayload.parseQRCode(rawQRCode)
            pairClaimID = CanonicalUUID()
            pendingPairing = payload
            sessionTitle = payload.displayName ?? "Pocket Stage"
            connect(using: payload)
            return true
        } catch {
            // An unrelated/invalid QR must never tear down a healthy remote.
            // The Pair UI also disables replacement controls while active.
            if !hasActiveConnection {
                socket?.disconnect()
                socket = nil
                retirePairing(error.localizedDescription)
            }
            return false
        }
    }

    func disconnect() {
        socket?.disconnect()
        socket = nil
        retirePairing("Disconnected — the desktop jam keeps running. Create and scan a new pairing code to return.")
    }

    func interruptForBackground() {
        guard hasActiveConnection else { return }
        socket?.disconnect()
        socket = nil
        retirePairing("Pocket Stage paused when the iPhone left the foreground. The desktop jam keeps running; scan a fresh code to return.")
    }

    func setFader(
        slot: Int,
        value: Int,
        observedGeneration: Int,
        observedRevision: Int
    ) {
        queueCommand(
            .setParticipantFader,
            arguments: ["slot": .integer(slot), "fader_level": .integer(value)],
            coalescingKey: "fader:\(slot)",
            observedGeneration: observedGeneration,
            observedRevision: observedRevision
        )
    }

    func setMute(
        slot: Int,
        value: Bool,
        observedGeneration: Int,
        observedRevision: Int
    ) {
        queueCommand(
            .setParticipantMute,
            arguments: ["slot": .integer(slot), "muted": .bool(value)],
            coalescingKey: "mute:\(slot)",
            observedGeneration: observedGeneration,
            observedRevision: observedRevision
        )
    }

    @discardableResult
    func addMarker(atMS: Int, label: String) -> CanonicalUUID? {
        queueCommand(
            .addMarker,
            arguments: ["at_ms": .integer(atMS), "label": .string(label)]
        )
    }

    func requestRecording(
        displayedPrimaryAction: String,
        observedGeneration: Int,
        observedRevision: Int
    ) {
        guard displayedPrimaryAction == primaryAction,
              observedGeneration == currentGeneration,
              observedRevision == revision else {
            commandIssue = "Recording changed on the desktop. Review the current action and try again."
            return
        }
        guard canControlRecording else { return }
        let command: StageCommand
        let target: String
        if displayedPrimaryAction == "stop_recording" {
            command = .stopRecording
            target = "idle"
        } else if displayedPrimaryAction == "record" {
            command = .startRecording
            target = "recording"
        } else {
            return
        }
        if let commandID = queueCommand(
            command,
            arguments: [:],
            observedGeneration: observedGeneration,
            observedRevision: observedRevision
        ) {
            pendingRecordingCommandID = commandID
            pendingRecordingTarget = target
            recordingCommandPending = true
            commandStatus = command == .stopRecording
                ? "Stopping recording on the desktop…"
                : "Starting recording on the desktop…"
        }
    }

    private func connect(using payload: PairingPayload) {
        socket?.disconnect()
        sendTail?.cancel()
        sendTail = nil
        commandQueue.removeAll()
        inFlightCommand = nil
        freshnessTask?.cancel()
        freshnessTask = nil
        sequence = 0
        expectedServerSequence = 1
        commandIssue = nil
        commandStatus = nil
        controlBusy = false
        latestCanceledCommandID = nil
        phase = .connecting
        let newSocket = StageSocket(url: payload.endpoint.url, certificatePin: payload.certificateFingerprint.digest)
        newSocket.onEvent = { [weak self, weak newSocket] event in
            Task { @MainActor [weak self, weak newSocket] in
                guard let self, let newSocket else { return }
                self.handle(event, from: newSocket)
            }
        }
        socket = newSocket
        lastMessageAt = nil
        newSocket.connect()
        armFreshnessMonitor(for: newSocket)
    }

    private func handle(_ event: StageSocket.Event, from source: StageSocket) {
        guard socket === source else { return }
        switch event {
        case .opened:
            guard sequence == 0, let pendingPairing else { return }
            do {
                let body = try PairBody(capability: pendingPairing.token, claimID: pairClaimID)
                try enqueue(try StageEnvelope(kind: .pair, generation: 0, sequence: 0, body: body), through: source)
                // The bearer is one-use and isn't a reconnect credential. Do
                // not retain or publish it after the pair frame is queued.
                self.pendingPairing = nil
                sequence = 1
            } catch {
                source.disconnect()
                socket = nil
                retirePairing(error.localizedDescription)
            }
        case let .message(.snapshot(envelope)):
            guard acceptServerSequence(envelope.sequence) else { return }
            lastMessageAt = clock.now
            let snapshot = envelope.body
            if phase == .connected && snapshot.generation != currentGeneration {
                source.disconnect()
                socket = nil
                retirePairing("The desktop session changed. Scan a fresh code before sending more controls.")
                return
            }
            phase = .connected
            isLive = [
                "live",
                "recording_starting",
                "recording",
                "recording_stopping",
            ].contains(snapshot.phase)
            role = snapshot.role
            primaryAction = snapshot.primaryAction
            primaryEnabled = snapshot.primaryEnabled
            revision = snapshot.revision
            currentGeneration = snapshot.generation
            recordingState = snapshot.recordingState
            participants = snapshot.participants
            sections = snapshot.sections
            currentSectionOrdinal = snapshot.currentSectionOrdinal
            cue = snapshot.cue
            if snapshot.recordingState != "verifying",
               commandStatus == "Recording stopped. The desktop is verifying the take." {
                commandStatus = nil
            }
            reconcilePendingRecording(with: snapshot.recordingState)
            reconcileInFlightAfterSnapshot()
        case let .message(.receipt(envelope)):
            guard acceptServerSequence(envelope.sequence) else { return }
            lastMessageAt = clock.now
            latestReceipt = envelope.body
            if envelope.body.commandID == pendingRecordingCommandID {
                if envelope.body.status == .rejected {
                    clearPendingRecording()
                    commandStatus = nil
                    commandIssue = "Recording request rejected: \(envelope.body.reason.rawValue.replacingOccurrences(of: "_", with: " "))"
                } else if envelope.body.status == .confirmed {
                    clearPendingRecording()
                    commandStatus = nil
                    commandIssue = nil
                }
            } else if envelope.body.status == .rejected {
                commandIssue = "Command rejected: \(envelope.body.reason.rawValue.replacingOccurrences(of: "_", with: " "))"
            } else if envelope.body.status == .confirmed && !recordingCommandPending {
                commandIssue = nil
            }
            handleCommandReceipt(envelope.body)
        case .message(.pair), .message(.command):
            source.disconnect()
            socket = nil
            retirePairing("The desktop sent an unexpected message. Create and scan a new pairing code.")
        case let .failed(message):
            source.disconnect()
            socket = nil
            retirePairing("\(message) The desktop jam keeps running; create and scan a new pairing code.")
        case .closed:
            if phase == .connecting || phase == .connected {
                socket = nil
                retirePairing("Connection closed. The desktop jam keeps running; create and scan a new pairing code.")
            }
        }
    }

    private var sequence = 0
    private var expectedServerSequence = 1
    private func nextSequence() -> Int { defer { sequence += 1 }; return sequence }

    @discardableResult
    private func queueCommand(
        _ command: StageCommand,
        arguments: [String: JSONValue],
        coalescingKey: String? = nil,
        observedGeneration: Int? = nil,
        observedRevision: Int? = nil
    ) -> CanonicalUUID? {
        guard phase == .connected, socket != nil else { return nil }
        guard !controlBusy, inFlightCommand == nil, commandQueue.isEmpty else {
            commandIssue = "Wait for the current desktop control to finish."
            return nil
        }
        let commandGeneration = observedGeneration ?? currentGeneration
        let commandRevision = observedRevision ?? revision
        guard commandGeneration == currentGeneration,
              commandRevision == revision else {
            commandIssue = "The stage changed before that control was sent. Review it and try again."
            return nil
        }
        let commandID = CanonicalUUID()
        do {
            _ = try CommandBody(
                command: command,
                generation: commandGeneration,
                expectedRevision: commandRevision,
                arguments: arguments,
                commandID: commandID
            )
        } catch {
            commandIssue = error.localizedDescription
            return nil
        }
        let intent = QueuedStageCommand(
            commandID: commandID,
            command: command,
            arguments: arguments,
            coalescingKey: coalescingKey,
            generation: commandGeneration,
            expectedRevision: commandRevision
        )
        commandIssue = nil
        if let coalescingKey,
           let index = commandQueue.lastIndex(where: {
               $0.coalescingKey == coalescingKey
           }) {
            commandQueue[index] = intent
        } else {
            guard commandQueue.count < 32 else {
                commandIssue = "Too many controls are waiting for the desktop."
                return nil
            }
            commandQueue.append(intent)
        }
        dispatchNextCommandIfReady()
        return intent.commandID
    }

    private func dispatchNextCommandIfReady() {
        guard inFlightCommand == nil,
              phase == .connected,
              let socket,
              !commandQueue.isEmpty else { return }
        let intent = commandQueue.removeFirst()
        do {
            let body = try CommandBody(
                command: intent.command,
                generation: intent.generation,
                expectedRevision: intent.expectedRevision,
                arguments: intent.arguments,
                commandID: intent.commandID
            )
            try enqueue(
                try StageEnvelope(
                    kind: .command,
                    generation: intent.generation,
                    sequence: nextSequence(),
                    body: body
                ),
                through: socket
            )
            inFlightCommand = InFlightStageCommand(
                intent: intent,
                sentAt: clock.now
            )
            controlBusy = true
        } catch {
            commandIssue = error.localizedDescription
            dispatchNextCommandIfReady()
        }
    }

    private func handleCommandReceipt(_ receipt: ReceiptBody) {
        guard var inFlight = inFlightCommand,
              inFlight.intent.commandID == receipt.commandID else { return }
        inFlight.receiptStatus = receipt.status
        inFlight.receiptRevision = receipt.revision
        inFlightCommand = inFlight

        if receipt.status == .accepted || receipt.status == .pending {
            reconcileInFlightAfterSnapshot()
            return
        }
        if revision >= receipt.revision {
            finishInFlightCommand(receipt.commandID)
        }
    }

    private func reconcileInFlightAfterSnapshot() {
        guard let inFlight = inFlightCommand,
              let status = inFlight.receiptStatus,
              let receiptRevision = inFlight.receiptRevision else { return }
        if status == .accepted && revision >= receiptRevision {
            finishInFlightCommand(inFlight.intent.commandID)
        } else if (status == .confirmed || status == .rejected)
                    && revision >= receiptRevision {
            finishInFlightCommand(inFlight.intent.commandID)
        }
    }

    private func finishInFlightCommand(_ commandID: CanonicalUUID) {
        guard inFlightCommand?.intent.commandID == commandID else { return }
        inFlightCommand = nil
        controlBusy = false
        dispatchNextCommandIfReady()
    }

    private var currentGeneration: Int = 0
    private var pendingRecordingCommandID: CanonicalUUID?
    private var pendingRecordingTarget: String?

    private func armFreshnessMonitor(for source: StageSocket) {
        freshnessTask?.cancel()
        let connectionStartedAt = clock.now
        freshnessTask = Task { @MainActor [weak self, weak source] in
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 1_000_000_000) }
                catch { return }
                guard let self, let source, self.socket === source else { return }
                guard let lastMessageAt = self.lastMessageAt else {
                    // Leave enough time for a musician to read and approve
                    // iOS's first-run Local Network permission sheet.
                    if connectionStartedAt.duration(to: self.clock.now) > .seconds(60) {
                        source.disconnect()
                        self.socket = nil
                        self.retirePairing("The desktop did not answer. Confirm both devices use the same private Wi-Fi, then scan a fresh code.")
                        return
                    }
                    continue
                }
                if lastMessageAt.duration(to: self.clock.now) > .seconds(5) {
                    source.disconnect()
                    self.socket = nil
                    self.retirePairing("The iPhone connection became stale. The desktop jam keeps running; scan a fresh code to return.")
                    return
                }
                if let inFlight = self.inFlightCommand {
                    let age = inFlight.sentAt.duration(to: self.clock.now)
                    let limit: Duration = inFlight.receiptStatus == .pending
                        ? .seconds(30)
                        : .seconds(8)
                    if age > limit {
                        let canceledIDs = Set(
                            [inFlight.intent.commandID] + commandQueue.map(\.commandID)
                        )
                        let commandID = inFlight.intent.commandID
                        self.inFlightCommand = nil
                        self.commandQueue.removeAll()
                        self.controlBusy = false
                        self.latestCanceledCommandID = commandID.string
                        if let pendingRecordingCommandID = self.pendingRecordingCommandID,
                           canceledIDs.contains(pendingRecordingCommandID) {
                            self.clearPendingRecording()
                            self.commandStatus = nil
                        }
                        self.commandIssue = "A desktop control did not finish. Check WebJam at the computer before trying again."
                    }
                }
            }
        }
    }

    private func reconcilePendingRecording(with state: String) {
        guard let target = pendingRecordingTarget else { return }
        let reachedTarget = target == "recording"
            ? state == "recording"
            : ["idle", "ready", "verifying"].contains(state)
        if reachedTarget {
            let commandID = pendingRecordingCommandID
            clearPendingRecording()
            commandStatus = state == "verifying"
                ? "Recording stopped. The desktop is verifying the take."
                : nil
            commandIssue = nil
            if let commandID { finishInFlightCommand(commandID) }
        } else if state == "needs_attention" {
            let commandID = pendingRecordingCommandID
            clearPendingRecording()
            commandStatus = nil
            commandIssue = "Recording needs attention on the desktop."
            if let commandID { finishInFlightCommand(commandID) }
        }
    }

    private func clearPendingRecording() {
        pendingRecordingCommandID = nil
        pendingRecordingTarget = nil
        recordingCommandPending = false
    }

    private func acceptServerSequence(_ received: Int) -> Bool {
        guard received == expectedServerSequence else {
            socket?.disconnect()
            socket = nil
            retirePairing("The desktop message sequence was invalid. Create and scan a new pairing code.")
            return false
        }
        expectedServerSequence += 1
        return true
    }

    private func retirePairing(_ message: String) {
        pendingPairing = nil
        sendTail?.cancel()
        sendTail = nil
        freshnessTask?.cancel()
        freshnessTask = nil
        phase = .failed(message)
        currentGeneration = 0
        revision = 0
        role = ""
        primaryAction = ""
        primaryEnabled = false
        isLive = false
        recordingState = "idle"
        participants = []
        sections = []
        currentSectionOrdinal = nil
        cue = ""
        latestReceipt = nil
        commandQueue.removeAll()
        inFlightCommand = nil
        controlBusy = false
        latestCanceledCommandID = nil
        lastMessageAt = nil
        commandStatus = nil
        clearPendingRecording()
    }

    private func enqueue<T: Encodable>(_ message: T, through source: StageSocket) throws {
        guard socket === source else { throw URLError(.cancelled) }
        let text = try WireCodec.encodeText(message)
        let previous = sendTail
        let queued = Task { @MainActor [weak self, weak source] in
            _ = await previous?.result
            guard !Task.isCancelled else { return }
            guard let self, let source else { return }
            guard self.socket === source else { return }
            do { try await source.send(text: text) }
            catch {
                guard self.socket === source else { return }
                source.disconnect()
                self.socket = nil
                self.retirePairing("\(error.localizedDescription) The desktop jam keeps running; create and scan a new pairing code.")
            }
        }
        sendTail = queued
    }
}
