import SwiftUI
import ArtCompanionCore

@main
struct ArtCompanionApp: App {
    var body: some Scene {
        WindowGroup { CompanionView() }
    }
}

struct CompanionView: View {
    @State private var model = CompanionModel()
    @State private var name = ""
    @State private var invitation = ""
    @State private var handoffMessage = ""
    @State private var handoffEpoch = UUID()
    private enum InputField { case name, invitation }
    @FocusState private var focusedInput: InputField?
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let fixture = layoutFixture {
                        // Debug-only, finite layout evidence; no invitation or connection bypass.
                        Text("Layout fixture — no live room").font(.caption).foregroundStyle(.secondary)
                        RoomContent(room: fixture, service: "Webex", conversationAvailable: fixtureConversationAvailable,
                                    openConversation: {}, handoffMessage: "")
                        leaveButton
                    } else if model.phase == .idle || model.phase == .failed {
                        joinForm
                    } else {
                        Label(model.message, systemImage: model.phase == .connected ? "checkmark.circle.fill" : "antenna.radiowaves.left.and.right")
                            .foregroundStyle(model.phase == .connected ? Color.primary : Color.secondary)
                            .accessibilityIdentifier("connection-status")
                        if let room = model.room {
                            RoomContent(room: room, service: model.conversation?.service ?? "Webex",
                                        conversationAvailable: model.conversation != nil,
                                        openConversation: openConversation, handoffMessage: handoffMessage)
                        } else {
                            Text("Room details will appear after the host responds.")
                            conversationCard
                        }
                        leaveButton
                    }
                }
                .frame(maxWidth: 680, alignment: .leading)
                .padding(20)
                .frame(maxWidth: .infinity)
            }
            .accessibilityIdentifier("companion-scroll")
            .scrollDismissesKeyboard(.interactively)
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationTitle("WebJam")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focusedInput = nil }
                }
            }
        }
        .tint(Color(red: 191.0 / 255, green: 87.0 / 255, blue: 0))
        .onChange(of: scenePhase) { _, phase in
            if phase == .background {
                invitation = ""
                focusedInput = nil
                model.pause()
            } else if phase == .active { model.resume() }
        }
    }

    private var joinForm: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Make something together.").font(.largeTitle.bold()).fixedSize(horizontal: false, vertical: true)
            Text("Join your desktop host from an iPhone or iPad on the same Wi-Fi.")
                .foregroundStyle(.secondary)
            SurfaceCard {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Join an Art room").font(.title2.bold())
                    TextField("Your name", text: $name)
                        .textContentType(.nickname)
                        .submitLabel(.next)
                        .onSubmit { focusedInput = .invitation }
                        .textFieldStyle(.roundedBorder)
                        .focused($focusedInput, equals: .name)
                        .accessibilityIdentifier("guest-name")
                    Text("Paste the full invitation").font(.headline)
                    TextEditor(text: $invitation)
                        .frame(minHeight: 112, maxHeight: 144)
                        .scrollContentBackground(.hidden)
                        .padding(8)
                        .background(Color.secondary.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .privacySensitive()
                        .focused($focusedInput, equals: .invitation)
                        .accessibilityLabel("Full WebJam invitation")
                        .accessibilityIdentifier("invitation")
                    if model.phase == .failed {
                        Label(model.message, systemImage: "exclamationmark.circle")
                            .foregroundStyle(.primary)
                            .accessibilityIdentifier("join-error")
                    }
                    Button {
                        let pasted = invitation
                        invitation = ""
                        focusedInput = nil
                        handoffEpoch = UUID()
                        handoffMessage = ""
                        model.join(paste: pasted, name: name)
                    } label: {
                        Text("Join room").frame(maxWidth: .infinity, minHeight: 32)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(invitation.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("join-room")
                    Text("Use a local room invitation. Internet-room links are not supported here yet.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("The local room connection is not encrypted. Join a host and network you trust.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            SurfaceCard {
                VStack(alignment: .leading, spacing: 16) {
                    StartDescription(title: "Make together", symbol: "person.2", detail: "Talk and make in one room. Use your own workspace, or the host’s shared workspace on a compatible computer.")
                    Divider()
                    StartDescription(title: "Paint along", symbol: "paintbrush.pointed", detail: "Follow the host’s process video in Conversation. Your canvas is elsewhere — Procreate, paper, or CSP.")
                }
            }
            Text("Joining Music? Follow the room and use Conversation to listen, talk, or chat. Live Music hosting and jamming stay on desktop.")
                .font(.footnote).foregroundStyle(.secondary)
                .accessibilityIdentifier("music-join-honesty")
        }
    }

    private var conversationCard: some View {
        ConversationContent(service: model.conversation?.service ?? "Webex",
                            available: model.conversation != nil,
                            openConversation: openConversation, message: handoffMessage)
    }

    private func openConversation() {
        guard let conversation = model.conversation else { return }
        let current = handoffEpoch
        openURL(conversation.url) { accepted in
            guard handoffEpoch == current, model.conversation == conversation else { return }
            handoffMessage = accepted
                ? "Opened \(conversation.service). Join and check your microphone there. WebJam cannot confirm meeting membership."
                : "Could not open \(conversation.service). Ask the host to resend the conversation link."
        }
    }

    private var leaveButton: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button("Leave room") {
                handoffEpoch = UUID()
                model.leave()
                handoffMessage = ""
            }
                .buttonStyle(.bordered).controlSize(.large).tint(.primary)
                .accessibilityIdentifier("leave-room")
            Text("Leaving WebJam does not leave the meeting. End or leave it in its own app.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private var layoutFixture: RoomSummary? {
        #if DEBUG
        switch ProcessInfo.processInfo.environment["WEBJAM_ART_LAYOUT_SCENARIO"] {
        case "paint", "missing": return RoomSummary(profile: .art, artStart: .paintAlong, video: .playing, workspaceOffered: false, lessonActive: true)
        case "make": return RoomSummary(profile: .art, artStart: .makeTogether, video: .idle, workspaceOffered: true, lessonActive: false)
        case "music": return RoomSummary(profile: .music, artStart: .unspecified, video: .idle, workspaceOffered: false, lessonActive: false)
        case "legacy": return RoomSummary(profile: .art, artStart: .unspecified, video: .idle, workspaceOffered: false, lessonActive: false)
        default: return nil
        }
        #else
        return nil
        #endif
    }

    private var fixtureConversationAvailable: Bool {
        #if DEBUG
        return ProcessInfo.processInfo.environment["WEBJAM_ART_LAYOUT_SCENARIO"] != "missing"
        #else
        return false
        #endif
    }
}

private struct RoomContent: View {
    let room: RoomSummary
    let service: String
    let conversationAvailable: Bool
    let openConversation: () -> Void
    let handoffMessage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(room.profile == .art ? room.artStart.title : "Follow the Music room")
                .font(.largeTitle.bold()).fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("room-title")
            Text("You’re a guest. The desktop host leads the room.").foregroundStyle(.secondary)
            if room.profile == .art {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        if room.artStart == .paintAlong {
                            Text("Follow the process video").font(.title2.bold())
                            Text("Open \(service) and watch the host’s screen share. Ask the host to pause when you need time.")
                            if room.video != .idle {
                                Text("Host video: \(room.video.rawValue). This is the host’s status; check the shared picture in \(service).")
                                    .font(.footnote).foregroundStyle(.secondary)
                            } else if room.lessonActive {
                                Text("A shared lesson is available. Ask the host to share it in \(service).")
                                    .font(.footnote).foregroundStyle(.secondary)
                            } else {
                                Text("The host hasn’t announced a video here yet. Check with them in Conversation.")
                                    .font(.footnote).foregroundStyle(.secondary)
                            }
                        } else {
                            Text("Talk and make").font(.title2.bold())
                            Text("Open \(service) to talk, share your progress, and stay with the group.")
                        }
                    }
                }
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Your canvas is elsewhere").font(.title2.bold())
                            .accessibilityIdentifier("canvas-elsewhere")
                        Text("Paint in Procreate, on paper, or in CSP. WebJam keeps you with the room while you work in your own space.")
                        if room.workspaceOffered {
                            Text("The host offers a shared workspace. Open it from desktop WebJam on a compatible computer; it isn’t opened on this phone or tablet.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
            } else {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Listen, talk, and chat in \(service)").font(.title2.bold())
                        Text("Ask the host to share sound in the meeting. This companion follows room status; it does not stream Jamulus audio.")
                        Text("Live Music hosting, recording, and low-latency jamming stay on desktop.")
                            .accessibilityIdentifier("music-room-honesty")
                    }
                }
            }
            ConversationContent(service: service, available: conversationAvailable,
                                openConversation: openConversation, message: handoffMessage)
        }
    }
}

private struct ConversationContent: View {
    let service: String
    let available: Bool
    let openConversation: () -> Void
    let message: String
    var body: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 12) {
                Text("Conversation").font(.title2.bold())
                if available {
                    Text("Talk and share in \(service), in its own app or browser.")
                    Button(action: openConversation) {
                        Label("Open \(service)", systemImage: "arrow.up.right.square")
                            .frame(maxWidth: .infinity, minHeight: 32)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("open-conversation")
                } else {
                    Text("Ask the host for the full invitation with a Webex or other Conversation link. Joining this room does not join a meeting.")
                        .accessibilityIdentifier("missing-conversation")
                }
                if !message.isEmpty { Text(message).font(.footnote).foregroundStyle(.secondary) }
                Text("Your microphone, chat, and screen sharing are controlled there.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
    }
}

private struct StartDescription: View {
    let title: String
    let symbol: String
    let detail: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: symbol).font(.title2.bold())
            Text(detail).foregroundStyle(.secondary)
        }
    }
}

private struct SurfaceCard<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        content.frame(maxWidth: .infinity, alignment: .leading)
            .padding(18)
            .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 18))
    }
}
