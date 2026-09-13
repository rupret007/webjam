import Foundation
import Testing
@testable import ArtCompanionCore

private enum HTTPPolicyProbeFailure: String, Error {
    case configuration, loopbackParser, wrongError, unexpectedError, checkpoint, deadline
}

@Test(
    .enabled(
        if: ProcessInfo.processInfo.environment["WEBJAM_RUN_SWIFT_ART_COMPANION_INTEGRATION"] == "1",
        "Requires the opt-in live Python HTTP policy harness"
    )
)
func liveArtCompanionHTTPPolicy() async {
    do { try await runHTTPPolicyProbe() }
    catch let failure as HTTPPolicyProbeFailure {
        // Never render framework errors, response bodies, credentials or URLs.
        Issue.record("ART_HTTP_POLICY_FAILURE: \(failure.rawValue)")
    } catch {
        Issue.record("ART_HTTP_POLICY_FAILURE: unexpectedError")
    }
}

private func runHTTPPolicyProbe() async throws {
    let environment = ProcessInfo.processInfo.environment
    guard let rawEndpoint = environment["WEBJAM_ART_HTTP_TEST_ENDPOINT"],
          let endpoint = URL(string: rawEndpoint), endpoint.scheme == "http",
          endpoint.host == "127.0.0.1", endpoint.user == nil, endpoint.password == nil,
          endpoint.query == nil, endpoint.fragment == nil, endpoint.path == "/",
          let port = endpoint.port, (1...65535).contains(port),
          let sessionID = environment["WEBJAM_ART_HTTP_TEST_SESSION"],
          Invitation.canonicalUUID(sessionID),
          let token = environment["WEBJAM_ART_HTTP_TEST_TOKEN"], Invitation.validToken(token),
          let scenario = environment["WEBJAM_ART_HTTP_TEST_CASE"],
          let checkpointPath = environment["WEBJAM_ART_HTTP_TEST_CHECKPOINT"] else {
        throw HTTPPolicyProbeFailure.configuration
    }
    let expected: CompanionError?
    switch scenario {
    case "redirect": expected = .invalidResponse
    case "declared-oversize", "streamed-oversize": expected = .oversizedResponse
    case "unauthorized-oversize", "forbidden-oversize", "unauthorized-stall", "forbidden-stall":
        expected = .authentication
    case "cancel-stall", "close-stall": expected = nil
    case "declared-opaque-stall", "unauthorized-opaque-stall": expected = .unavailable
    default: throw HTTPPolicyProbeFailure.configuration
    }

    let loopbackPaste = "webjam://join?v=2&host=127.0.0.1&port=22124&session=Art"
        + "&sid=\(sessionID)&peer=\(port)&token=\(token)"
    var parserRejectedLoopback = false
    do { _ = try Invitation.parse(loopbackPaste) }
    catch let error as CompanionError { parserRejectedLoopback = error == .invalidInvitation }
    guard parserRejectedLoopback else { throw HTTPPolicyProbeFailure.loopbackParser }

    // The production parser remains private-LAN-only. This internal initializer
    // is available solely through @testable for the loopback HTTP fixture.
    let invitation = Invitation(endpoint: endpoint, sessionID: sessionID, token: token, conversation: nil)
    let client = LANClient(invitation: invitation)
    defer { client.close() }
    if expected == nil {
        try await verifyStalledRequestRetirement(client, scenario: scenario, sessionID: sessionID,
                                               token: token, checkpointPath: checkpointPath)
        do { try Data().write(to: URL(fileURLWithPath: checkpointPath), options: .atomic) }
        catch { throw HTTPPolicyProbeFailure.checkpoint }
        return
    }
    var observed: CompanionError?
    let deadline = ContinuousClock.now + .seconds(8)
    do {
        if scenario == "redirect" {
            _ = try await client.enroll(installationID: UUID().uuidString.lowercased(), displayName: "Policy probe")
        } else {
            _ = try await client.read(Enrollment(participantID: sessionID, token: token))
        }
    } catch let error as CompanionError {
        observed = error
    } catch {
        throw HTTPPolicyProbeFailure.unexpectedError
    }
    if scenario.contains("-opaque-"), ContinuousClock.now >= deadline {
        throw HTTPPolicyProbeFailure.deadline
    }
    guard observed == expected else {
        let category: String
        switch observed {
        case .authentication?: category = "authentication"
        case .unavailable?: category = "unavailable"
        case .invalidResponse?: category = "invalid-response"
        case .oversizedResponse?: category = "oversized-response"
        case nil: category = "accepted-response"
        default: category = "other-fixed-error"
        }
        // Only empty files with a finite name cross the diagnostic boundary.
        try? Data().write(to: URL(fileURLWithPath: checkpointPath + "." + category), options: .atomic)
        throw HTTPPolicyProbeFailure.wrongError
    }
    do { try Data().write(to: URL(fileURLWithPath: checkpointPath), options: .atomic) }
    catch { throw HTTPPolicyProbeFailure.checkpoint }
}

private func verifyStalledRequestRetirement(_ client: LANClient, scenario: String, sessionID: String,
                                           token: String, checkpointPath: String) async throws {
    let enrollment = Enrollment(participantID: sessionID, token: token)
    let reading = Task { try await client.read(enrollment) }
    defer { reading.cancel() }
    let deadline = ContinuousClock.now + .seconds(5)
    while !FileManager.default.fileExists(atPath: checkpointPath + ".headers") {
        guard ContinuousClock.now < deadline else { throw HTTPPolicyProbeFailure.checkpoint }
        try await Task.sleep(for: .milliseconds(10))
    }
    if scenario == "cancel-stall" { reading.cancel() }
    else { client.close() }
    var cancelled = false
    do { _ = try await reading.value }
    catch is CancellationError { cancelled = true }
    catch { throw HTTPPolicyProbeFailure.unexpectedError }
    guard cancelled else { throw HTTPPolicyProbeFailure.wrongError }
    if scenario == "close-stall" {
        // A retired client cannot create a later request or retain a continuation.
        var retired = false
        do { _ = try await client.read(enrollment) }
        catch is CancellationError { retired = true }
        catch { throw HTTPPolicyProbeFailure.unexpectedError }
        guard retired else { throw HTTPPolicyProbeFailure.wrongError }
    }
}
