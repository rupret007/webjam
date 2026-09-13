import Foundation
import Testing
@testable import ArtCompanionCore

private enum LiveLANProbeFailure: String, Error {
    case configuration, loopbackParser, checkpoint, enrollment, state, authentication, hostLoss
}

private struct LiveLANCheckpoints {
    let directory: URL

    func mark(_ name: String) throws {
        do { try Data().write(to: directory.appendingPathComponent(name), options: .atomic) }
        catch { throw LiveLANProbeFailure.checkpoint }
    }

    func waitFor(_ name: String) async throws {
        let deadline = ContinuousClock.now + .seconds(10)
        while !FileManager.default.fileExists(atPath: directory.appendingPathComponent(name).path) {
            guard ContinuousClock.now < deadline else { throw LiveLANProbeFailure.checkpoint }
            try await Task.sleep(for: .milliseconds(10))
        }
    }
}

@Test(
    .enabled(
        if: ProcessInfo.processInfo.environment["WEBJAM_RUN_SWIFT_ART_COMPANION_INTEGRATION"] == "1",
        "Requires the opt-in live Python LAN Art room harness"
    )
)
func liveLANArtCompanion() async {
    do { try await runLiveLANArtCompanion() }
    catch let failure as LiveLANProbeFailure {
        // The failure vocabulary is fixed; never include an invitation,
        // enrollment, URLSession error, environment or decoded host payload.
        Issue.record("ART_LAN_PROBE_FAILURE: \(failure.rawValue)")
    } catch {
        Issue.record("ART_LAN_PROBE_FAILURE: unexpected")
    }
}

private func runLiveLANArtCompanion() async throws {
    let environment = ProcessInfo.processInfo.environment
    guard let rawEndpoint = environment["WEBJAM_ART_COMPANION_TEST_ENDPOINT"],
          let endpoint = URL(string: rawEndpoint), endpoint.scheme == "http",
          endpoint.host == "127.0.0.1", endpoint.user == nil, endpoint.password == nil,
          endpoint.query == nil, endpoint.fragment == nil, endpoint.path == "/",
          let port = endpoint.port, (1...65535).contains(port),
          let sessionID = environment["WEBJAM_ART_COMPANION_TEST_SESSION"],
          Invitation.canonicalUUID(sessionID),
          let token = environment["WEBJAM_ART_COMPANION_TEST_TOKEN"], Invitation.validToken(token),
          let checkpointPath = environment["WEBJAM_ART_COMPANION_TEST_CHECKPOINTS"],
          let profile = environment["WEBJAM_ART_COMPANION_TEST_PROFILE"],
          let expectedProfile = RoomSummary.Profile(rawValue: profile),
          let start = environment["WEBJAM_ART_COMPANION_TEST_START"],
          let expectedStart = RoomSummary.ArtStart(rawValue: start) else {
        throw LiveLANProbeFailure.configuration
    }
    let loopbackPaste = "webjam://join?v=2&host=127.0.0.1&port=22124&session=Art"
        + "&sid=\(sessionID)&peer=\(port)&token=\(token)"
    var parserRejectedLoopback = false
    do { _ = try Invitation.parse(loopbackPaste) }
    catch let error as CompanionError { parserRejectedLoopback = error == .invalidInvitation }
    guard parserRejectedLoopback else { throw LiveLANProbeFailure.loopbackParser }

    // This internal initializer is accessible through @testable only. No
    // production parser, configuration flag or endpoint policy admits loopback.
    let invitation = Invitation(endpoint: endpoint, sessionID: sessionID, token: token, conversation: nil)
    let client = LANClient(invitation: invitation)
    defer { client.close() }
    let gates = LiveLANCheckpoints(directory: URL(fileURLWithPath: checkpointPath, isDirectory: true))
    let enrollment: Enrollment
    do {
        enrollment = try await client.enroll(
            installationID: UUID().uuidString.lowercased(), displayName: "Mobile probe"
        )
    } catch { throw LiveLANProbeFailure.enrollment }
    try gates.mark("enrolled")
    try await gates.waitFor("may-read-state")

    let summary: RoomSummary
    do { summary = try await client.read(enrollment) }
    catch { throw LiveLANProbeFailure.state }
    guard summary.profile == expectedProfile, summary.artStart == expectedStart,
          summary.video == .idle, !summary.workspaceOffered, !summary.lessonActive else {
        throw LiveLANProbeFailure.state
    }
    try gates.mark("state-read")
    try await gates.waitFor("may-reject-auth")

    let wrongCredential = Enrollment(participantID: enrollment.participantID, token: String(repeating: "z", count: 43))
    var authenticationRejected = false
    do { _ = try await client.read(wrongCredential) }
    catch let error as CompanionError { authenticationRejected = error == .authentication }
    guard authenticationRejected else { throw LiveLANProbeFailure.authentication }
    try gates.mark("auth-rejected")
    try await gates.waitFor("host-stopped")

    var hostUnavailable = false
    do { _ = try await client.read(enrollment) }
    catch let error as CompanionError { hostUnavailable = error == .unavailable }
    guard hostUnavailable else { throw LiveLANProbeFailure.hostLoss }
    try gates.mark("host-loss-confirmed")
}
