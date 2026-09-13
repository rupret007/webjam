import Foundation
import Testing
@testable import ArtCompanionCore

func roomData(_ additions: [String: Any] = [:]) throws -> Data {
    var values: [String: Any] = ["session_id": testSessionID, "generation": 0, "creator_profile_key": "art"]
    values.merge(additions) { _, new in new }
    return try JSONSerialization.data(withJSONObject: values)
}

@Test func legacyHostDoesNotInventPaintAlong() throws {
    let summary = try RoomSummary.decode(roomData(), sessionID: testSessionID)
    #expect(summary.artStart == .unspecified)
    #expect(summary.video == .idle)
    #expect(!summary.lessonActive)
}

@Test func hostFactsAreFiniteAndPrivatePayloadsAreDiscarded() throws {
    let summary = try RoomSummary.decode(roomData([
        "art_start_key": "paint_along",
        "reference_video": ["shared": true, "state": "playing", "source_display_name": "PRIVATE.mov", "identity_digest": "private-digest"],
        "shared_canvas": ["shared": true, "join_url": "drawpile://private.example/password", "session_label": "PRIVATE"],
        "lesson_requests": ["availability": "active", "context_id": "private-context", "admission_id": "private-admission"],
        "message": "private-message", "capture_arm": ["take_id": "private-take"]
    ]), sessionID: testSessionID)
    #expect(summary.artStart == .paintAlong)
    #expect(summary.video == .playing)
    #expect(summary.workspaceOffered)
    #expect(summary.lessonActive)
    #expect(!String(reflecting: summary).lowercased().contains("private"))
}

@Test func musicNeverInheritsArtOrCaptureAuthority() throws {
    let summary = try RoomSummary.decode(roomData([
        "creator_profile_key": "music", "signal": "recording",
        "shared_canvas": ["shared": true], "reference_video": ["shared": true, "state": "playing"],
        "lesson_requests": ["availability": "active"], "capture_arm": ["capture_enabled": true]
    ]), sessionID: testSessionID)
    #expect(summary.profile == .music)
    #expect(summary.video == .idle && !summary.workspaceOffered && !summary.lessonActive)
}

@Test func invalidOrForeignHostResponsesFailClosed() throws {
    for values: [String: Any] in [
        ["session_id": UUID().uuidString], ["generation": -1], ["generation": true],
        ["creator_profile_key": "future-profile"], ["art_start_key": "host"],
        ["creator_profile_key": "music", "art_start_key": "paint_along"],
        ["reference_video": ["shared": "yes", "state": "playing"]],
        ["reference_video": ["shared": false, "state": "playing"]],
        ["reference_video": ["shared": true, "state": "unknown"]]
    ] {
        #expect(throws: CompanionError.self) { try RoomSummary.decode(roomData(values), sessionID: testSessionID) }
    }
    #expect(throws: CompanionError.oversizedResponse) {
        try RoomSummary.decode(Data(repeating: 32, count: 65537), sessionID: testSessionID)
    }
}
