import Foundation

private let maxGeneration = (1 << 31) - 1
private let maxJSONSafeInteger = (1 << 53) - 1

private func isBoundedWireText(
    _ value: String,
    maxBytes: Int,
    allowEmpty: Bool = false
) -> Bool {
    guard (allowEmpty || !value.isEmpty),
          value.utf8.count <= maxBytes,
          value == value.precomposedStringWithCanonicalMapping else { return false }
    return value.unicodeScalars.allSatisfy {
        !($0.value <= 0x1f || $0.value == 0x7f)
    }
}

/// The only wire version understood by this scaffold. Bump this intentionally
/// when a backend introduces an incompatible message shape.
public enum PocketStageProtocol {
    public static let version = 1
}

public enum ProtocolValidationError: Error, Equatable, LocalizedError, Sendable {
    case invalidQRCode
    case unsupportedVersion(Int)
    case missingField(String)
    case invalidField(String)
    case invalidEndpoint
    case invalidToken
    case invalidMessage

    public var errorDescription: String? {
        switch self {
        case .invalidQRCode: "This is not a Pocket Stage pairing code."
        case .unsupportedVersion(let version): "Pairing code version \(version) is not supported."
        case .missingField(let field): "The pairing code is missing \(field)."
        case .invalidField(let field): "The pairing code has an invalid \(field)."
        case .invalidEndpoint: "The pairing code must use a secure WebSocket endpoint."
        case .invalidToken: "The pairing code has an invalid pairing token."
        case .invalidMessage: "The server sent an invalid protocol message."
        }
    }
}

public struct StageEndpoint: Codable, Equatable, Hashable, Sendable {
    public let url: URL

    public init(url: URL) throws {
        guard url.scheme?.lowercased() == "wss",
              let host = url.host,
              Self.isRFC1918IPv4(host),
              let port = url.port,
              (1...65_535).contains(port),
              url.path == "/v1/pocket",
              url.query == nil,
              url.user == nil,
              url.password == nil,
              url.fragment == nil else {
            throw ProtocolValidationError.invalidEndpoint
        }
        self.url = url
    }

    private static func isRFC1918IPv4(_ host: String) -> Bool {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return false }
        let octets = parts.compactMap { part -> Int? in
            guard !part.isEmpty,
                  part.count == 1 || part.first != "0",
                  let value = Int(part),
                  (0...255).contains(value) else { return nil }
            return value
        }
        guard octets.count == 4 else { return false }
        return octets[0] == 10
            || (octets[0] == 172 && (16...31).contains(octets[1]))
            || (octets[0] == 192 && octets[1] == 168)
    }

    public init(_ rawValue: String) throws {
        guard let url = URL(string: rawValue) else {
            throw ProtocolValidationError.invalidEndpoint
        }
        try self.init(url: url)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(url.absoluteString)
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        try self.init(try container.decode(String.self))
    }
}

/// SHA-256 fingerprint of the leaf certificate's DER representation. It is
/// carried in the pairing code so a compromised local network cannot redirect
/// the client to a different publicly trusted TLS endpoint.
public struct CertificateFingerprint: Codable, Equatable, Hashable, Sendable {
    public let hex: String

    public init(hex: String) throws {
        let normalized = hex.lowercased()
        let valid = normalized.count == 64 && normalized.unicodeScalars.allSatisfy {
            CharacterSet(charactersIn: "0123456789abcdef").contains($0)
        }
        guard valid else { throw ProtocolValidationError.invalidField("fingerprint") }
        self.hex = normalized
    }

    public var digest: Data {
        Data(stride(from: 0, to: hex.count, by: 2).compactMap {
            UInt8(hex[hex.index(hex.startIndex, offsetBy: $0)..<hex.index(hex.startIndex, offsetBy: $0 + 2)], radix: 16)
        })
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        try self.init(hex: try container.decode(String.self))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(hex)
    }
}

/// A UUID that always encodes in Python's canonical lowercase wire form.
public struct CanonicalUUID: Codable, Equatable, Hashable, Sendable {
    public let value: UUID
    public var string: String { value.uuidString.lowercased() }

    public init() { self.value = UUID() }
    public init(_ value: UUID) { self.value = value }
    public init(_ string: String) throws {
        guard let value = UUID(uuidString: string), string == value.uuidString.lowercased(),
              string != "00000000-0000-0000-0000-000000000000" else {
            throw ProtocolValidationError.invalidMessage
        }
        self.value = value
    }
    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        try self.init(try container.decode(String.self))
    }
    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(string)
    }
}

/// Exact desktop QR keys: v, session, endpoint, token, fingerprint,
/// expires, and optional name. The phone creates claim_id separately.
public struct PairingPayload: Codable, Equatable, Sendable {
    public let version: Int
    public let sessionID: UUID
    public let endpoint: StageEndpoint
    public let certificateFingerprint: CertificateFingerprint
    public let token: String
    public let expiresAtUnix: Int64
    public let displayName: String?

    public init(
        version: Int = PocketStageProtocol.version,
        sessionID: UUID,
        endpoint: StageEndpoint,
        certificateFingerprint: CertificateFingerprint,
        token: String,
        expiresAtUnix: Int64,
        displayName: String? = nil
    ) throws {
        guard version == PocketStageProtocol.version else {
            throw ProtocolValidationError.unsupportedVersion(version)
        }
        guard Self.isValidToken(token) else { throw ProtocolValidationError.invalidToken }
        guard expiresAtUnix > 0 else { throw ProtocolValidationError.invalidField("expires") }
        if let displayName,
           !isBoundedWireText(displayName, maxBytes: 64) {
            throw ProtocolValidationError.invalidField("name")
        }
        self.version = version
        self.sessionID = sessionID
        self.endpoint = endpoint
        self.certificateFingerprint = certificateFingerprint
        self.token = token
        self.expiresAtUnix = expiresAtUnix
        self.displayName = displayName
    }

    public static func parseQRCode(_ text: String, nowUnix: Int64 = Int64(Date().timeIntervalSince1970)) throws -> PairingPayload {
        guard let components = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme?.lowercased() == "pocketstage",
              components.host?.lowercased() == "pair" else {
            throw ProtocolValidationError.invalidQRCode
        }

        let permitted = Set(["v", "session", "endpoint", "token", "fingerprint", "expires", "name"])
        let items = components.queryItems ?? []
        guard Set(items.map(\.name)).isSubset(of: permitted),
              Set(items.map(\.name)).count == items.count else {
            throw ProtocolValidationError.invalidQRCode
        }
        func required(_ name: String) throws -> String {
            guard let value = items.first(where: { $0.name == name })?.value, !value.isEmpty else {
                throw ProtocolValidationError.missingField(name)
            }
            return value
        }
        guard let version = Int(try required("v")) else {
            throw ProtocolValidationError.invalidField("v")
        }
        let rawSession = try required("session")
        guard let sessionID = UUID(uuidString: rawSession), rawSession == sessionID.uuidString.lowercased() else {
            throw ProtocolValidationError.invalidField("session")
        }
        let endpoint = try StageEndpoint(try required("endpoint"))
        let certificateFingerprint = try CertificateFingerprint(hex: required("fingerprint"))
        guard let expiresAtUnix = Int64(try required("expires")), expiresAtUnix > nowUnix else {
            throw ProtocolValidationError.invalidField("expires")
        }
        return try PairingPayload(
            version: version,
            sessionID: sessionID,
            endpoint: endpoint,
            certificateFingerprint: certificateFingerprint,
            token: try required("token"),
            expiresAtUnix: expiresAtUnix,
            displayName: items.first(where: { $0.name == "name" })?.value
        )
    }

    public func qrCodeString() -> String {
        var components = URLComponents()
        components.scheme = "pocketstage"
        components.host = "pair"
        components.queryItems = [
            URLQueryItem(name: "v", value: String(version)),
            URLQueryItem(name: "session", value: sessionID.uuidString.lowercased()),
            URLQueryItem(name: "endpoint", value: endpoint.url.absoluteString),
            URLQueryItem(name: "token", value: token),
            URLQueryItem(name: "fingerprint", value: certificateFingerprint.hex),
            URLQueryItem(name: "expires", value: String(expiresAtUnix))
        ] + (displayName.map { [URLQueryItem(name: "name", value: $0)] } ?? [])
        return components.string ?? ""
    }

    static func isValidToken(_ token: String) -> Bool {
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        return token.utf8.count == 43 && token.unicodeScalars.allSatisfy(allowed.contains)
    }
}

public enum EnvelopeKind: String, Codable, Sendable { case pair, snapshot, command, receipt }
public enum StageCommand: String, Codable, Sendable {
    case addMarker = "add_marker"
    case goToSection = "go_to_section"
    case setParticipantMute = "set_participant_mute"
    case setParticipantFader = "set_participant_fader"
    case setParticipantPan = "set_participant_pan"
    case startRecording = "start_recording"
    case stopRecording = "stop_recording"
}

/// Exact v1 envelope. All network messages use these snake_case wire keys.
public struct StageEnvelope<Body: Codable & Sendable>: Codable, Sendable {
    public let version: Int
    public let kind: EnvelopeKind
    public let messageID: CanonicalUUID
    public let generation: Int
    public let sequence: Int
    public let sentAtUnixMS: Int64
    public let body: Body

    private enum CodingKeys: String, CodingKey {
        case version, kind, messageID = "message_id", generation, sequence, sentAtUnixMS = "sent_at_unix_ms", body
    }

    public init(kind: EnvelopeKind, generation: Int, sequence: Int, body: Body, messageID: CanonicalUUID = CanonicalUUID(), sentAtUnixMS: Int64 = Int64(Date().timeIntervalSince1970 * 1_000)) throws {
        guard generation >= 0, sequence >= 0 else { throw ProtocolValidationError.invalidMessage }
        self.version = PocketStageProtocol.version
        self.kind = kind
        self.messageID = messageID
        self.generation = generation
        self.sequence = sequence
        self.sentAtUnixMS = sentAtUnixMS
        self.body = body
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let version = try c.decode(Int.self, forKey: .version)
        guard version == PocketStageProtocol.version else { throw ProtocolValidationError.unsupportedVersion(version) }
        let generation = try c.decode(Int.self, forKey: .generation)
        let sequence = try c.decode(Int.self, forKey: .sequence)
        guard generation >= 0, sequence >= 0 else { throw ProtocolValidationError.invalidMessage }
        self.version = version
        self.kind = try c.decode(EnvelopeKind.self, forKey: .kind)
        self.messageID = try c.decode(CanonicalUUID.self, forKey: .messageID)
        self.generation = generation
        self.sequence = sequence
        self.sentAtUnixMS = try c.decode(Int64.self, forKey: .sentAtUnixMS)
        self.body = try c.decode(Body.self, forKey: .body)
    }
}

public struct PairBody: Codable, Equatable, Sendable {
    public let capability: String
    public let claimID: CanonicalUUID
    private enum CodingKeys: String, CodingKey { case capability, claimID = "claim_id" }
    public init(capability: String, claimID: CanonicalUUID) throws {
        guard PairingPayload.isValidToken(capability) else { throw ProtocolValidationError.invalidMessage }
        self.capability = capability
        self.claimID = claimID
    }

    public func validate() throws {
        guard PairingPayload.isValidToken(capability) else { throw ProtocolValidationError.invalidMessage }
    }
}

public struct StageParticipant: Codable, Equatable, Identifiable, Sendable {
    public let slot: Int
    public let label: String
    public let faderLevel: Int
    public let pan: Int
    public let muted: Bool
    public let solo: Bool
    public let isLocal: Bool
    public let connectionState: String
    public var id: Int { slot }
    private enum CodingKeys: String, CodingKey {
        case slot, label, faderLevel = "fader_level", pan, muted, solo, isLocal = "is_local", connectionState = "connection_state"
    }
    public func validate() throws {
        let states = Set(["unknown", "connecting", "ready", "degraded", "disconnected"])
        guard (1...64).contains(slot),
              isBoundedWireText(label, maxBytes: 80),
              (0...100).contains(faderLevel), (0...100).contains(pan),
              states.contains(connectionState) else {
            throw ProtocolValidationError.invalidMessage
        }
    }
}

public struct StageSection: Codable, Equatable, Identifiable, Sendable {
    public let ordinal: Int
    public let label: String
    public let startMS: Int
    public let endMS: Int
    public var id: Int { ordinal }
    private enum CodingKeys: String, CodingKey { case ordinal, label, startMS = "start_ms", endMS = "end_ms" }
    public func validate() throws {
        guard (1...256).contains(ordinal),
              isBoundedWireText(label, maxBytes: 80),
              (0...86_400_000).contains(startMS), endMS > startMS, endMS <= 86_400_000 else {
            throw ProtocolValidationError.invalidMessage
        }
    }
}

public indirect enum JSONValue: Codable, Equatable, Sendable {
    case string(String), integer(Int), bool(Bool), array([JSONValue]), object([String: JSONValue]), null
    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let value = try? c.decode(Bool.self) { self = .bool(value) }
        else if let value = try? c.decode(Int.self) { self = .integer(value) }
        else if let value = try? c.decode(String.self) { self = .string(value) }
        else if let value = try? c.decode([JSONValue].self) { self = .array(value) }
        else if let value = try? c.decode([String: JSONValue].self) { self = .object(value) }
        else { throw ProtocolValidationError.invalidMessage }
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case let .string(value): try c.encode(value)
        case let .integer(value): try c.encode(value)
        case let .bool(value): try c.encode(value)
        case let .array(value): try c.encode(value)
        case let .object(value): try c.encode(value)
        case .null: try c.encodeNil()
        }
    }
    public var displayText: String {
        switch self {
        case let .string(value): value
        case let .integer(value): String(value)
        case let .bool(value): value ? "true" : "false"
        case .null: ""
        case let .array(value): value.map(\.displayText).joined(separator: ", ")
        case let .object(value): value["label"]?.displayText ?? value["title"]?.displayText ?? "Cue received"
        }
    }
}

public struct SnapshotBody: Codable, Equatable, Sendable {
    public let schema: Int
    public let generation: Int
    public let revision: Int
    public let role: String
    public let phase: String
    public let primaryAction: String
    public let primaryEnabled: Bool
    public let recordingState: String
    public let participants: [StageParticipant]
    public let sections: [StageSection]
    public let currentSectionOrdinal: Int?
    public let cue: String
    private enum CodingKeys: String, CodingKey {
        case schema, generation, revision, role, phase, primaryAction = "primary_action", primaryEnabled = "primary_enabled", recordingState = "recording_state", participants, sections, currentSectionOrdinal = "current_section_ordinal", cue
    }
    public func validate() throws {
        let roles = Set(["host", "guest", "practice"])
        let phases = Set([
            "idle", "confirming_identity_and_sound", "band_check_required",
            "band_check_in_progress", "ready_to_start", "starting_host",
            "waiting_for_host_readiness", "invite_ready", "joining", "connected",
            "reconnecting", "live", "recording_starting", "recording",
            "recording_stopping", "take_validating", "guest_media_transferring",
            "take_ready", "take_needs_attention", "reviewing", "exporting",
            "ending", "ended", "blocked", "failed", "indeterminate",
        ])
        let primaryActions = Set([
            "none", "continue", "confirm_sound", "run_band_check", "start_session",
            "copy_invite", "reset_invite", "open_audio_settings",
            "add_conversation", "save_conversation", "enter_jam", "retry_setup",
            "wait", "try_reconnect", "record", "stop_recording", "review_take",
            "select_take", "export_tracks", "end_session", "open_details",
            "check_session",
        ])
        let recordingStates = Set([
            "idle", "starting", "recording", "stopping", "verifying", "ready",
            "needs_attention",
        ])
        guard schema == 1,
              (0...maxGeneration).contains(generation),
              (0...maxJSONSafeInteger).contains(revision),
              roles.contains(role), phases.contains(phase),
              primaryActions.contains(primaryAction),
              recordingStates.contains(recordingState),
              participants.count <= 64, sections.count <= 256 else {
            throw ProtocolValidationError.invalidMessage
        }
        try participants.forEach { try $0.validate() }
        try sections.forEach { try $0.validate() }
        let slots = participants.map(\.slot)
        guard slots == Array(Set(slots)).sorted() else {
            throw ProtocolValidationError.invalidMessage
        }
        let ordinals = sections.map(\.ordinal)
        guard ordinals == Array(Set(ordinals)).sorted() else {
            throw ProtocolValidationError.invalidMessage
        }
        for (previous, current) in zip(sections, sections.dropFirst())
            where current.startMS < previous.endMS {
            throw ProtocolValidationError.invalidMessage
        }
        if !isBoundedWireText(cue, maxBytes: 512, allowEmpty: true) {
            throw ProtocolValidationError.invalidMessage
        }
        if let currentSectionOrdinal, !sections.contains(where: { $0.ordinal == currentSectionOrdinal }) {
            throw ProtocolValidationError.invalidMessage
        }
    }
}

public struct CommandBody: Codable, Equatable, Sendable {
    public let commandID: CanonicalUUID
    public let command: StageCommand
    public let generation: Int
    public let expectedRevision: Int
    public let arguments: [String: JSONValue]
    private enum CodingKeys: String, CodingKey { case commandID = "command_id", command, generation, expectedRevision = "expected_revision", arguments }
    public init(command: StageCommand, generation: Int, expectedRevision: Int, arguments: [String: JSONValue], commandID: CanonicalUUID = CanonicalUUID()) throws {
        guard generation >= 0, expectedRevision >= 0 else { throw ProtocolValidationError.invalidMessage }
        self.commandID = commandID
        self.command = command
        self.generation = generation
        self.expectedRevision = expectedRevision
        self.arguments = arguments
        try validate()
    }
    public func validate() throws {
        guard generation >= 0, expectedRevision >= 0 else { throw ProtocolValidationError.invalidMessage }
        let keys = Set(arguments.keys)
        switch command {
        case .addMarker:
            guard keys == ["at_ms", "label"],
                  case let .integer(atMS)? = arguments["at_ms"], (0...86_400_000).contains(atMS),
                  case let .string(label)? = arguments["label"],
                  isBoundedWireText(label, maxBytes: 80, allowEmpty: true) else { throw ProtocolValidationError.invalidMessage }
        case .goToSection:
            guard keys == ["ordinal"], case let .integer(ordinal)? = arguments["ordinal"], (1...256).contains(ordinal) else { throw ProtocolValidationError.invalidMessage }
        case .setParticipantMute:
            guard keys == ["slot", "muted"], case let .integer(slot)? = arguments["slot"], (1...64).contains(slot), case .bool? = arguments["muted"] else { throw ProtocolValidationError.invalidMessage }
        case .setParticipantFader:
            guard keys == ["slot", "fader_level"], case let .integer(slot)? = arguments["slot"], (1...64).contains(slot), case let .integer(level)? = arguments["fader_level"], (0...100).contains(level) else { throw ProtocolValidationError.invalidMessage }
        case .setParticipantPan:
            guard keys == ["slot", "pan"], case let .integer(slot)? = arguments["slot"], (1...64).contains(slot), case let .integer(pan)? = arguments["pan"], (0...100).contains(pan) else { throw ProtocolValidationError.invalidMessage }
        case .startRecording, .stopRecording:
            guard keys.isEmpty else { throw ProtocolValidationError.invalidMessage }
        }
    }
}

public enum CommandStatus: String, Codable, Sendable { case accepted, pending, confirmed, rejected }
public enum CommandRejectionReason: String, Codable, Sendable {
    case none, unauthorized
    case staleGeneration = "stale_generation"
    case staleRevision = "stale_revision"
    case unsupported, unavailable
    case invalidState = "invalid_state"
    case rateLimited = "rate_limited"
    case internalFailure = "internal_failure"
}

public struct ReceiptBody: Codable, Equatable, Sendable {
    public let commandID: CanonicalUUID
    public let status: CommandStatus
    public let generation: Int
    public let revision: Int
    public let reason: CommandRejectionReason
    private enum CodingKeys: String, CodingKey { case commandID = "command_id", status, generation, revision, reason }
    public func validate() throws {
        guard generation >= 0, revision >= 0,
              (status == .rejected ? reason != .none : reason == .none) else { throw ProtocolValidationError.invalidMessage }
    }
}

public enum IncomingMessage: Sendable {
    case pair(StageEnvelope<PairBody>)
    case snapshot(StageEnvelope<SnapshotBody>)
    case command(StageEnvelope<CommandBody>)
    case receipt(StageEnvelope<ReceiptBody>)
}

public enum WireCodec {
    public static func encode<T: Encodable>(_ message: T) throws -> Data { try JSONEncoder().encode(message) }
    public static func encodeText<T: Encodable>(_ message: T) throws -> String {
        guard let text = String(data: try encode(message), encoding: .utf8) else {
            throw ProtocolValidationError.invalidMessage
        }
        return text
    }

    public static func decodeIncomingMessage(_ data: Data) throws -> IncomingMessage {
        let decoder = JSONDecoder()
        do {
            let kind = try validateExactWireShape(data)
            switch kind {
            case .pair:
                let message = try decoder.decode(StageEnvelope<PairBody>.self, from: data)
                guard message.generation == 0, message.sequence == 0 else { throw ProtocolValidationError.invalidMessage }
                try message.body.validate(); return .pair(message)
            case .snapshot:
                let message = try decoder.decode(StageEnvelope<SnapshotBody>.self, from: data)
                guard message.generation == message.body.generation else { throw ProtocolValidationError.invalidMessage }
                try message.body.validate(); return .snapshot(message)
            case .command:
                let message = try decoder.decode(StageEnvelope<CommandBody>.self, from: data)
                guard message.generation == message.body.generation else { throw ProtocolValidationError.invalidMessage }
                try message.body.validate(); return .command(message)
            case .receipt:
                let message = try decoder.decode(StageEnvelope<ReceiptBody>.self, from: data)
                guard message.generation == message.body.generation else { throw ProtocolValidationError.invalidMessage }
                try message.body.validate(); return .receipt(message)
            }
        } catch let error as ProtocolValidationError { throw error }
        catch { throw ProtocolValidationError.invalidMessage }
    }

    private static func validateExactWireShape(_ data: Data) throws -> EnvelopeKind {
        guard data.count <= 64 * 1_024,
              let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(root.keys) == ["version", "kind", "message_id", "generation", "sequence", "sent_at_unix_ms", "body"],
              let rawKind = root["kind"] as? String,
              let kind = EnvelopeKind(rawValue: rawKind),
              let body = root["body"] as? [String: Any] else { throw ProtocolValidationError.invalidMessage }
        let required: Set<String>
        switch kind {
        case .pair:
            required = ["capability", "claim_id"]
        case .snapshot:
            required = ["schema", "generation", "revision", "role", "phase", "primary_action", "primary_enabled", "recording_state", "participants", "sections", "current_section_ordinal", "cue"]
            guard let participants = body["participants"] as? [[String: Any]],
                  participants.allSatisfy({ Set($0.keys) == ["slot", "label", "fader_level", "pan", "muted", "solo", "is_local", "connection_state"] }),
                  let sections = body["sections"] as? [[String: Any]],
                  sections.allSatisfy({ Set($0.keys) == ["ordinal", "label", "start_ms", "end_ms"] }) else {
                throw ProtocolValidationError.invalidMessage
            }
        case .command:
            required = ["command_id", "command", "generation", "expected_revision", "arguments"]
        case .receipt:
            required = ["command_id", "status", "generation", "revision", "reason"]
        }
        guard Set(body.keys) == required else { throw ProtocolValidationError.invalidMessage }
        return kind
    }
}
