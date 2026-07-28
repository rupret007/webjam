import SwiftUI
import PocketStageProtocol

struct PocketStageTabView: View {
    @State private var selectedTab = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            PairView()
                .tabItem { Label("Pair", systemImage: "qrcode.viewfinder") }
                .tag(0)
            LiveNowView()
                .tabItem { Label("Live / Now", systemImage: "dot.radiowaves.left.and.right") }
                .tag(1)
            BandView()
                .tabItem { Label("Band", systemImage: "person.3") }
                .tag(2)
            MyMixView()
                .tabItem { Label("My Mix", systemImage: "slider.horizontal.3") }
                .tag(3)
            CuesView()
                .tabItem { Label("Cues", systemImage: "text.badge.checkmark") }
                .tag(4)
        }
        .tint(.orange)
        .safeAreaInset(edge: .top) {
            VStack(spacing: 0) {
                if case let .failed(message) = connection.phase,
                   selectedTab != 0 {
                    Button {
                        selectedTab = 0
                    } label: {
                        Label("\(message) Open Pair", systemImage: "wifi.exclamationmark")
                            .font(.footnote)
                            .foregroundStyle(.white)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .frame(maxWidth: .infinity)
                            .background(.red.opacity(0.94))
                    }
                    .accessibilityHint("Opens the Pair tab to reconnect")
                }
                if let issue = connection.commandIssue {
                    Label(issue, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .frame(maxWidth: .infinity)
                        .background(.red.opacity(0.94))
                }
            }
        }
    }

    @EnvironmentObject private var connection: StageConnectionModel
}

private struct ConnectionStatus: View {
    @EnvironmentObject private var connection: StageConnectionModel

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(connection.phase.label).font(.subheadline.weight(.medium))
            Spacer()
        }
        .foregroundStyle(.secondary)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Pocket Stage connection")
        .accessibilityValue(connection.phase.label)
    }

    private var color: Color {
        switch connection.phase {
        case .connected: .green
        case .connecting: .orange
        case .failed: .red
        case .unpaired: .secondary
        }
    }
}

struct PairView: View {
    @EnvironmentObject private var connection: StageConnectionModel
    @State private var payload = ""
    @State private var showsScanner = false
    @State private var showsDeveloperEntry = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Pocket Stage") {
                    WebJamBrandHeader()
                    ConnectionStatus()
                    Text("Pair with a host-issued QR code. The code includes the session endpoint and certificate pin.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Section("Pairing code") {
                    Button {
                        showsScanner = true
                    } label: {
                        Label("Scan Pairing QR", systemImage: "camera.viewfinder")
                    }
                    .disabled(connection.hasActiveConnection)
                    DisclosureGroup(
                        "Developer payload entry",
                        isExpanded: $showsDeveloperEntry
                    ) {
                        Text(
                            "For Simulator and developer-injected test payloads. "
                            + "On an iPhone, restore Camera access and scan the QR."
                        )
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        TextEditor(text: $payload)
                            .font(.caption.monospaced())
                            .frame(minHeight: 110)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .disabled(connection.hasActiveConnection)
                        Button("Pair developer payload") {
                            let submitted = payload
                            payload = ""
                            _ = connection.pair(with: submitted)
                        }
                        .disabled(
                            connection.hasActiveConnection
                                || payload.trimmingCharacters(
                                    in: .whitespacesAndNewlines
                                ).isEmpty
                        )
                    }
                    if connection.hasActiveConnection {
                        Text("Disconnect before pairing with a different desktop.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
                if case let .failed(message) = connection.phase {
                    Section { Label(message, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                }
                if connection.hasActiveConnection {
                    Section {
                        Button("Disconnect", role: .destructive) { connection.disconnect() }
                    }
                }
            }
            .navigationTitle("Pair")
            .sheet(isPresented: $showsScanner) {
                PairingQRScanner { scanned in
                    let accepted = connection.pair(with: scanned)
                    if accepted { payload = "" }
                    return accepted
                }
            }
        }
    }
}

struct LiveNowView: View {
    @EnvironmentObject private var connection: StageConnectionModel
    @State private var recordingHold: RecordingIntent?
    @State private var showsRecordingConfirmation = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        Label(connection.isLive ? "Live now" : "Standing by", systemImage: connection.isLive ? "record.circle.fill" : "pause.circle")
                            .font(.title2.weight(.semibold))
                            .foregroundStyle(connection.isLive ? .red : .secondary)
                        Text(connection.sessionTitle).font(.headline)
                        if let current = connection.currentSectionOrdinal { Text("Section \(current)").font(.title3.monospacedDigit()) }
                    }
                    .padding(.vertical, 8)
                }
                Section("Session") {
                    ConnectionStatus()
                    LabeledContent("Players", value: "\(connection.participants.count)")
                    LabeledContent("Revision", value: "\(connection.revision)")
                    LabeledContent("Recording", value: connection.recordingState)
                    if connection.role == "host" {
                        let action = connection.recordingActionLabel
                        Label("Hold to \(action)", systemImage: "record.circle")
                            .font(.headline)
                            .padding(.vertical, 10)
                            .frame(maxWidth: .infinity)
                            .background(.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 10))
                            .contentShape(Rectangle())
                            .onLongPressGesture(
                                minimumDuration: 1.0,
                                perform: {
                                    if let intent = recordingHold {
                                        sendRecording(intent)
                                    }
                                    recordingHold = nil
                                },
                                onPressingChanged: { pressing in
                                    if pressing {
                                        recordingHold = currentRecordingIntent
                                    }
                                }
                            )
                            .accessibilityAddTraits(.isButton)
                            .accessibilityHint("Press and hold for one second")
                            .accessibilityAction(named: Text("Review \(action)")) {
                                recordingHold = currentRecordingIntent
                                showsRecordingConfirmation = recordingHold != nil
                            }
                            .disabled(!connection.canControlRecording)
                            .confirmationDialog(
                                "Confirm \(action)",
                                isPresented: $showsRecordingConfirmation,
                                titleVisibility: .visible
                            ) {
                                Button(action) {
                                    if let intent = recordingHold {
                                        sendRecording(intent)
                                    }
                                    recordingHold = nil
                                }
                                Button("Cancel", role: .cancel) {
                                    recordingHold = nil
                                }
                            } message: {
                                Text("This sends the displayed recording action to the desktop.")
                            }
                    }
                    if let status = connection.commandStatus {
                        Text(status).font(.footnote).foregroundStyle(.orange)
                    }
                    if let issue = connection.commandIssue {
                        Text(issue).font(.footnote).foregroundStyle(.red)
                    }
                }
                Section("Safety") {
                    Text("This companion only sends explicit controls. It does not record or transmit audio or video.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Live / Now")
        }
    }

    private var currentRecordingIntent: RecordingIntent? {
        guard ["record", "stop_recording"].contains(connection.primaryAction) else {
            return nil
        }
        return RecordingIntent(
            primaryAction: connection.primaryAction,
            generation: connection.generation,
            revision: connection.revision
        )
    }

    private func sendRecording(_ intent: RecordingIntent) {
        connection.requestRecording(
            displayedPrimaryAction: intent.primaryAction,
            observedGeneration: intent.generation,
            observedRevision: intent.revision
        )
    }

}

private struct RecordingIntent {
    let primaryAction: String
    let generation: Int
    let revision: Int
}

struct BandView: View {
    @EnvironmentObject private var connection: StageConnectionModel

    var body: some View {
        NavigationStack {
            Group {
                if connection.participants.isEmpty {
                    ContentUnavailableView("No band snapshot", systemImage: "person.3", description: Text("Pair with the active stage to see the current roster."))
                } else {
                    List(connection.participants) { member in
                        HStack(spacing: 14) {
                            Image(systemName: member.isLocal ? "person.crop.circle.fill" : "person.crop.circle")
                                .foregroundStyle(member.isLocal ? .orange : .secondary)
                            VStack(alignment: .leading) {
                                Text(member.label).fontWeight(.medium)
                                Text(member.connectionState).font(.subheadline).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if member.solo { Text("Solo").font(.caption).foregroundStyle(.orange) }
                        }
                        .accessibilityLabel("\(member.label), slot \(member.slot), \(member.connectionState)\(member.solo ? ", solo" : "")")
                    }
                }
            }
            .navigationTitle("Band")
        }
    }
}

struct MyMixView: View {
    @EnvironmentObject private var connection: StageConnectionModel

    var body: some View {
        NavigationStack {
            Group {
                if connection.participants.isEmpty {
                    ContentUnavailableView("No mix yet", systemImage: "slider.horizontal.3", description: Text("Your personal mix appears after the stage sends a snapshot."))
                } else {
                    List(connection.participants) { participant in
                        MixChannelRow(
                            participant: participant,
                            renderGeneration: connection.generation,
                            renderRevision: connection.revision
                        )
                    }
                }
            }
            .navigationTitle("My Mix")
        }
    }
}

private struct MixChannelRow: View {
    @EnvironmentObject private var connection: StageConnectionModel
    let participant: StageParticipant
    let renderGeneration: Int
    let renderRevision: Int
    @State private var fader: Double
    @State private var faderIsEditing = false
    @State private var editStartedRevision: Int?
    @State private var editInvalidated = false

    init(
        participant: StageParticipant,
        renderGeneration: Int,
        renderRevision: Int
    ) {
        self.participant = participant
        self.renderGeneration = renderGeneration
        self.renderRevision = renderRevision
        _fader = State(initialValue: Double(participant.faderLevel))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(participant.label).fontWeight(.medium)
                Spacer()
                Toggle(
                    "Mute \(participant.label)",
                    isOn: Binding(
                        get: { participant.muted },
                        set: { updateMute($0) }
                    )
                    )
                        .labelsHidden()
                        .tint(.red)
                    .accessibilityLabel("Mute \(participant.label)")
                    .accessibilityValue(participant.muted ? "On" : "Off")
                    .disabled(!connection.canSendControls)
            }
            HStack(spacing: 10) {
                Image(systemName: "speaker.wave.1")
                    .accessibilityHidden(true)
                Slider(value: $fader, in: 0...100) { editing in
                    if editing {
                        if !faderIsEditing {
                            editStartedRevision = connection.revision
                            editInvalidated = false
                        }
                        faderIsEditing = true
                    } else {
                        let canSend = faderIsEditing
                            && !editInvalidated
                            && editStartedRevision == connection.revision
                            && connection.phase == .connected
                        faderIsEditing = false
                        editStartedRevision = nil
                        editInvalidated = false
                        if canSend {
                            updateFader()
                        } else {
                            fader = Double(participant.faderLevel)
                        }
                    }
                }
                .accessibilityLabel("\(participant.label) monitor level")
                .accessibilityValue("\(Int(fader.rounded())) percent")
                .disabled(!connection.canSendControls)
                Text(Int(fader), format: .number)
                    .font(.caption.monospacedDigit())
                    .frame(minWidth: 42)
                    .accessibilityHidden(true)
            }
        }
        .padding(.vertical, 4)
        .onChange(of: participant.faderLevel) { _, value in
            if !faderIsEditing { fader = Double(value) }
        }
        .onChange(of: participant.label) { _, _ in
            // A session-local slot may be reused after roster churn. Never
            // carry the previous musician's draft fader into the new row.
            editInvalidated = faderIsEditing
            if !faderIsEditing { fader = Double(participant.faderLevel) }
        }
        .onChange(of: connection.revision) { _, _ in
            // Any authoritative revision can include same-label roster
            // replacement. Cancel a draft instead of re-targeting it.
            if faderIsEditing {
                editInvalidated = true
            } else {
                fader = Double(participant.faderLevel)
            }
        }
        .onChange(of: connection.phase) { _, phase in
            if phase != .connected {
                editInvalidated = faderIsEditing
                if !faderIsEditing { fader = Double(participant.faderLevel) }
            }
        }
        .onChange(of: connection.latestReceipt?.commandID.string) { _, _ in
            if connection.latestReceipt?.status == .rejected {
                faderIsEditing = false
                fader = Double(participant.faderLevel)
            }
        }
    }

    private func updateMute(_ value: Bool) {
        connection.setMute(
            slot: participant.slot,
            value: value,
            observedGeneration: renderGeneration,
            observedRevision: renderRevision
        )
    }
    private func updateFader() {
        connection.setFader(
            slot: participant.slot,
            value: Int(fader.rounded()),
            observedGeneration: renderGeneration,
            observedRevision: renderRevision
        )
    }
}

struct CuesView: View {
    @EnvironmentObject private var connection: StageConnectionModel
    @State private var markerLabel = ""
    @State private var pendingMarkerID: String?

    var body: some View {
        NavigationStack {
            List {
                if connection.cue.isEmpty {
                    Section("Current cue") { Text("No cue").foregroundStyle(.secondary) }
                } else {
                    Section("Current cue") { Text(connection.cue) }
                }
                Section {
                    TextField("Marker label (optional)", text: $markerLabel)
                    Button("Mark Now") {
                        // The desktop stamps its authoritative session time;
                        // the required wire field is only a hint.
                        pendingMarkerID = connection.addMarker(
                            atMS: 0,
                            label: markerLabel
                        )?.string
                    }
                    .buttonStyle(.bordered)
                    .disabled(
                        connection.phase != .connected
                            || connection.controlBusy
                            || pendingMarkerID != nil
                    )
                }
            }
            .navigationTitle("Cues")
            .onChange(of: connection.latestReceipt?.commandID.string) { _, value in
                guard value == pendingMarkerID,
                      let receipt = connection.latestReceipt else { return }
                if receipt.status == .accepted || receipt.status == .confirmed {
                    markerLabel = ""
                }
                if receipt.status == .confirmed || receipt.status == .rejected {
                    pendingMarkerID = nil
                }
            }
            .onChange(of: connection.phase) { _, phase in
                if phase != .connected { pendingMarkerID = nil }
            }
            .onChange(of: connection.latestCanceledCommandID) { _, value in
                if value == pendingMarkerID { pendingMarkerID = nil }
            }
        }
    }
}
