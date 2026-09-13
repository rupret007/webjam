import Foundation
import Observation

@MainActor @Observable
public final class CompanionModel {
    public enum Phase: Equatable { case idle, joining, connected, reconnecting, paused, failed }
    public private(set) var phase = Phase.idle
    public private(set) var room: RoomSummary?
    public private(set) var message = ""
    public private(set) var conversation: Conversation?
    private var invitation: Invitation?
    private var installationID = ""
    private var displayName = ""
    private var acceptedProfile: RoomSummary.Profile?
    private var task: Task<Void, Never>?
    private var client: (any RoomClient)?
    private var epoch = UUID()
    private let makeClient: @Sendable (Invitation) -> any RoomClient
    private let pollDelay: Duration
    private let retryLimit: Duration

    public convenience init() { self.init(makeClient: { LANClient(invitation: $0) }) }

    init(makeClient: @escaping @Sendable (Invitation) -> any RoomClient,
         pollDelay: Duration = .seconds(1), retryLimit: Duration = .seconds(30)) {
        self.makeClient = makeClient
        self.pollDelay = pollDelay
        self.retryLimit = retryLimit
    }

    public func join(paste: String, name: String) {
        leave()
        do {
            let parsed = try Invitation.parse(paste)
            let normalized = name.split(whereSeparator: \.isWhitespace).joined(separator: " ")
            guard !normalized.isEmpty, normalized.count <= 80,
                  !normalized.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else {
                phase = .failed
                message = "Add your name so the host can recognize you (up to 80 characters)."
                return
            }
            invitation = parsed
            conversation = parsed.conversation
            displayName = normalized
            installationID = UUID().uuidString.lowercased()
            connect(parsed)
        } catch let error as CompanionError { fail(error) }
        catch { fail(.invalidInvitation) }
    }

    private func stopRequests() {
        epoch = UUID()
        task?.cancel()
        task = nil
        client?.close()
        client = nil
        room = nil
    }

    public func leave() {
        stopRequests()
        invitation = nil
        conversation = nil
        displayName = ""
        acceptedProfile = nil
        installationID = ""
        phase = .idle
        message = ""
    }

    public func pause() {
        guard invitation != nil else { return }
        stopRequests()
        phase = .paused
        message = "Room updates are paused while this app is in the background."
    }

    public func resume() {
        guard phase == .paused, let invitation else { return }
        connect(invitation)
    }

    private func fail(_ error: CompanionError) {
        leave()
        phase = .failed
        message = error.description
    }

    private func connect(_ invitation: Invitation) {
        let client = makeClient(invitation)
        self.client = client
        let current = epoch
        phase = .joining
        message = "Connecting to the host…"
        task = Task { [weak self] in
            guard let self else { return }
            defer { client.close() }
            var enrollment: Enrollment?
            var unavailableSince: ContinuousClock.Instant?
            while !Task.isCancelled && self.epoch == current {
                do {
                    if enrollment == nil {
                        enrollment = try await client.enroll(installationID: self.installationID, displayName: self.displayName)
                    }
                    guard !Task.isCancelled, self.epoch == current, let enrollment else { return }
                    let summary = try await client.read(enrollment)
                    guard !Task.isCancelled, self.epoch == current else { return }
                    if let acceptedProfile = self.acceptedProfile, acceptedProfile != summary.profile { throw CompanionError.invalidResponse }
                    self.acceptedProfile = summary.profile
                    self.room = summary
                    self.phase = .connected
                    self.message = "Connected to the host"
                    unavailableSince = nil
                } catch {
                    guard !Task.isCancelled, self.epoch == current else { return }
                    let safe = (error as? CompanionError) ?? .unavailable
                    guard safe == .unavailable else { self.fail(safe); return }
                    let since = unavailableSince ?? ContinuousClock.now
                    unavailableSince = since
                    guard since.duration(to: .now) < self.retryLimit else { self.fail(.unavailable); return }
                    self.room = nil
                    self.phase = .reconnecting
                    self.message = "Can’t reach the host. Retrying on the same Wi-Fi…"
                }
                do { try await Task.sleep(for: self.pollDelay) } catch { return }
            }
        }
    }
}
