import Foundation

public struct RoomSummary: Equatable, Sendable {
    public enum Profile: String, Sendable { case art, music }
    public enum ArtStart: String, Sendable {
        case unspecified = "", makeTogether = "talk_and_make", paintAlong = "paint_along"
        public var title: String {
            switch self {
            case .unspecified: return "Art room"
            case .makeTogether: return "Make together"
            case .paintAlong: return "Paint along"
            }
        }
    }
    public enum Video: String, Sendable { case idle, ready, playing, paused, failed }
    public let profile: Profile
    public let artStart: ArtStart
    public let video: Video
    public let workspaceOffered: Bool
    public let lessonActive: Bool

    public init(profile: Profile, artStart: ArtStart, video: Video,
                workspaceOffered: Bool, lessonActive: Bool) {
        self.profile = profile
        self.artStart = artStart
        self.video = video
        self.workspaceOffered = workspaceOffered
        self.lessonActive = lessonActive
    }

    /// These are the only retained host facts. No file names, digests, addresses,
    /// recording/capture authority, message strings or lesson admission tokens.
    static func decode(_ data: Data, sessionID: String) throws -> RoomSummary {
        struct Wire: Decodable {
            struct VideoState: Decodable { let shared: Bool; let state: String }
            struct Canvas: Decodable { let shared: Bool }
            struct Lesson: Decodable { let availability: String }
            let session_id: String
            let generation: Int
            let creator_profile_key: String
            let art_start_key: String?
            let reference_video: VideoState?
            let shared_canvas: Canvas?
            let lesson_requests: Lesson?
        }
        let fields = try StrictJSON.object(data)
        guard fields["art_start_key"] == nil || fields["art_start_key"] is String else {
            throw CompanionError.invalidResponse
        }
        guard let wire = try? JSONDecoder().decode(Wire.self, from: data),
              wire.session_id.lowercased() == sessionID, Invitation.canonicalUUID(wire.session_id),
              wire.generation >= 0, let profile = Profile(rawValue: wire.creator_profile_key),
              let start = ArtStart(rawValue: wire.art_start_key ?? ""),
              profile == .art || start == .unspecified else { throw CompanionError.invalidResponse }
        // Music has no mobile media/capture path. Ignore all Art media fields there.
        if profile == .music {
            return RoomSummary(profile: .music, artStart: .unspecified, video: .idle,
                               workspaceOffered: false, lessonActive: false)
        }
        var video = Video.idle
        if let state = wire.reference_video {
            guard let known = Video(rawValue: state.state),
                  state.shared || known == .idle || known == .failed else { throw CompanionError.invalidResponse }
            video = state.shared ? known : .idle
        }
        return RoomSummary(profile: profile, artStart: start, video: video,
                           workspaceOffered: wire.shared_canvas?.shared ?? false,
                           lessonActive: wire.lesson_requests?.availability == "active")
    }
}
