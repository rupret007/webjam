import Foundation
import Testing
@testable import ArtCompanionCore

// A deliberately cancellation-resistant reader proves generation retirement,
// rather than assuming that cancelling a task makes its callbacks disappear.
final class ControlledRoomClient: RoomClient, @unchecked Sendable {
    private let lock = NSLock()
    private var waiter: CheckedContinuation<RoomSummary, Error>?
    private var closed = false
    private var enrollmentIDs: [String] = []
    func enroll(installationID: String, displayName: String) async throws -> Enrollment {
        lock.withLock { enrollmentIDs.append(installationID) }
        return Enrollment(participantID: testSessionID, token: String(repeating: "b", count: 43))
    }
    func read(_ enrollment: Enrollment) async throws -> RoomSummary {
        try await withCheckedThrowingContinuation { continuation in lock.withLock { waiter = continuation } }
    }
    var waiting: Bool { lock.withLock { waiter != nil } }
    var isClosed: Bool { lock.withLock { closed } }
    var joinedAs: [String] { lock.withLock { enrollmentIDs } }
    func deliver(_ result: Result<RoomSummary, Error>) {
        let continuation = lock.withLock { let saved = waiter; waiter = nil; return saved }
        continuation?.resume(with: result)
    }
    func close() { lock.withLock { closed = true } }
}

private final class ControlledClientSequence: @unchecked Sendable {
    private let lock = NSLock()
    private var clients: [ControlledRoomClient]
    init(_ clients: [ControlledRoomClient]) { self.clients = clients }
    func next() -> ControlledRoomClient { lock.withLock { clients.removeFirst() } }
}

@MainActor
func eventually(_ predicate: @MainActor () -> Bool) async throws {
    for _ in 0..<200 {
        if predicate() { return }
        try await Task.sleep(for: .milliseconds(5))
    }
    Issue.record("The expected connection transition did not arrive.")
}

@Test @MainActor func enrollmentAloneIsNotConnectedAndLeaveRetiresLateReads() async throws {
    let client = ControlledRoomClient()
    let model = CompanionModel(makeClient: { _ in client })
    model.join(paste: testInvite, name: "Artist")
    try await eventually { client.waiting }
    #expect(model.phase == .joining && model.room == nil)
    model.leave()
    client.deliver(.success(try RoomSummary.decode(roomData(["art_start_key": "paint_along"]), sessionID: testSessionID)))
    try await Task.sleep(for: .milliseconds(20))
    #expect(client.isClosed && model.phase == .idle && model.room == nil && model.conversation == nil)
}

@Test @MainActor func hostLossRemovesConnectionBeforeRetryAndAuthFailureRetiresEverything() async throws {
    let client = ControlledRoomClient()
    let model = CompanionModel(makeClient: { _ in client }, pollDelay: .milliseconds(1))
    model.join(paste: testInvite, name: "Artist")
    try await eventually { client.waiting }
    client.deliver(.success(try RoomSummary.decode(roomData(), sessionID: testSessionID)))
    try await eventually { model.phase == .connected && client.waiting }
    client.deliver(.failure(CompanionError.unavailable))
    try await eventually { model.phase == .reconnecting && client.waiting }
    #expect(model.room == nil)
    client.deliver(.failure(CompanionError.authentication))
    try await eventually { model.phase == .failed }
    #expect(client.isClosed && model.room == nil && model.conversation == nil)
    #expect(model.message == CompanionError.authentication.description)
}

@Test @MainActor func backgroundAndReplacementRetireOldRoom() async throws {
    let old = ControlledRoomClient()
    let model = CompanionModel(makeClient: { _ in old })
    model.join(paste: testInvite, name: "Artist")
    try await eventually { old.waiting }
    model.pause()
    #expect(model.phase == .paused && model.room == nil && old.isClosed)
    old.deliver(.success(try RoomSummary.decode(roomData(), sessionID: testSessionID)))
    try await Task.sleep(for: .milliseconds(20))
    #expect(model.phase == .paused && model.room == nil)
    model.join(paste: "invalid", name: "Artist")
    #expect(model.phase == .failed && model.room == nil)
    model.resume()
    #expect(model.phase == .failed)
}

@Test @MainActor func resumeKeepsParticipantIdentityAndRejectsReplacedProfile() async throws {
    let first = ControlledRoomClient()
    let resumed = ControlledRoomClient()
    let clients = ControlledClientSequence([first, resumed])
    let model = CompanionModel(makeClient: { _ in clients.next() }, pollDelay: .milliseconds(1))
    model.join(paste: testInvite, name: "Artist")
    try await eventually { first.waiting }
    first.deliver(.success(try RoomSummary.decode(roomData(), sessionID: testSessionID)))
    try await eventually { model.phase == .connected && first.waiting }
    model.pause()
    model.resume()
    try await eventually { resumed.waiting }
    #expect(first.isClosed && first.joinedAs == resumed.joinedAs && first.joinedAs.count == 1)
    first.deliver(.success(try RoomSummary.decode(roomData(), sessionID: testSessionID)))
    resumed.deliver(.success(try RoomSummary.decode(roomData(["creator_profile_key": "music"]), sessionID: testSessionID)))
    try await eventually { model.phase == .failed }
    #expect(model.room == nil && model.conversation == nil && resumed.isClosed)
    #expect(model.message == CompanionError.invalidResponse.description)
}
