import XCTest
import ArtCompanionCore

final class ArtCompanionRuntimeTests: XCTestCase {
    /// Runs inside the real iOS app process and its ATS policy. The invitation
    /// comes from an ephemeral Python SessionPeerServer on a real RFC1918
    /// interface. No test client, endpoint override, or synthetic room is used.
    @MainActor
    func testProductionJoinObservesPaintAlongAndLeaveClearsRoom() async {
        let model = CompanionModel()
        defer { model.leave() }
        model.join(paste: GeneratedHost.invitation, name: "iOS Runtime Artist")

        let deadline = ContinuousClock.now.advanced(by: .seconds(25))
        while model.phase != .connected && model.phase != .failed && ContinuousClock.now < deadline {
            do { try await Task.sleep(for: .milliseconds(100)) }
            catch {
                XCTFail("The iOS runtime Join probe was cancelled.")
                return
            }
        }
        guard model.phase == .connected, let room = model.room else {
            // Do not include the invitation, framework error, or model dump.
            XCTFail("The iOS app did not establish an authenticated LAN room connection.")
            return
        }
        XCTAssertEqual(room.profile, .art)
        XCTAssertEqual(room.artStart, .paintAlong)
        XCTAssertEqual(room.video, .idle)
        XCTAssertFalse(room.workspaceOffered)

        model.leave()
        XCTAssertEqual(model.phase, .idle)
        XCTAssertNil(model.room)
        XCTAssertNil(model.conversation)
        XCTAssertEqual(model.message, "")

        // Cover the next normal poll interval: a retired Join must not restore
        // room authority after Leave.
        do { try await Task.sleep(for: .milliseconds(1200)) }
        catch {
            XCTFail("The iOS runtime Leave probe was cancelled.")
            return
        }
        XCTAssertEqual(model.phase, .idle)
        XCTAssertNil(model.room)
        XCTAssertNil(model.conversation)
    }
}
