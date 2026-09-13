import Foundation

public enum CompanionError: Error, Equatable, LocalizedError, CustomStringConvertible {
    case invalidInvitation, unsupportedInvitation, invalidConversation
    case authentication, unavailable, invalidResponse, oversizedResponse

    public var description: String {
        switch self {
        case .invalidInvitation:
            return "Paste one complete invitation from your desktop host."
        case .unsupportedInvitation:
            return "This companion needs a same-network WebJam invitation. Ask your host for a local room invitation."
        case .invalidConversation:
            return "The conversation link is incomplete or unclear. Ask the host to copy the full invitation again."
        case .authentication:
            return "This invitation is no longer accepted. Ask the host for a new invitation."
        case .unavailable:
            return "The host is unavailable. Check the same Wi-Fi and local-network permission, then join again."
        case .invalidResponse:
            return "The host returned an unsupported room. Ask for a new invitation."
        case .oversizedResponse:
            return "The host returned too much room data. Ask the host to restart the room."
        }
    }
    public var errorDescription: String? { description }
}

/// Private invitation context. Never persisted, placed in an OS URL handler, or logged.
public struct Invitation: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    let endpoint: URL
    let sessionID: String
    let token: String
    public let conversation: Conversation?
    public var description: String { "Invitation(private: redacted)" }
    public var debugDescription: String { description }

    public static func parse(_ paste: String) throws -> Invitation {
        guard paste.utf8.count <= 8192 else { throw CompanionError.invalidInvitation }
        let wrappers = CharacterSet(charactersIn: "<>\"'`,;.!?()[]{}")
        let candidates = Set(paste.split(whereSeparator: \.isWhitespace).map {
            String($0).trimmingCharacters(in: wrappers)
        }.filter { $0.lowercased().hasPrefix("webjam://") })
        guard candidates.count == 1, let link = candidates.first,
              !link.contains("\\"), !link.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
              let parts = URLComponents(string: link), parts.scheme?.lowercased() == "webjam",
              parts.host?.lowercased() == "join",
              String(link.dropFirst(9).prefix(while: { !"/?#".contains($0) })).lowercased() == "join",
              parts.user == nil, parts.password == nil,
              parts.port == nil, parts.fragment == nil, ["", "/"].contains(parts.path),
              let query = parts.percentEncodedQuery,
              query == link.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false).last.map(String.init) else {
            throw CompanionError.invalidInvitation
        }
        // Parse once, reject duplicate/unknown parameters and malformed percent escapes.
        var fields: [String: String] = [:]
        for pair in query.split(separator: "&", omittingEmptySubsequences: false) {
            let values = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard values.count == 2,
                  let key = String(values[0]).replacingOccurrences(of: "+", with: " ").removingPercentEncoding,
                  let value = String(values[1]).replacingOccurrences(of: "+", with: " ").removingPercentEncoding,
                  fields[key] == nil else { throw CompanionError.invalidInvitation }
            fields[key] = value
        }
        guard fields["v"] == "2" else { throw CompanionError.unsupportedInvitation }
        guard Set(fields.keys) == ["v", "host", "port", "session", "sid", "peer", "token"],
              let host = fields["host"], isPrivateIPv4(host),
              validPort(fields["port"]), validPort(fields["peer"]),
              let sid = fields["sid"], canonicalUUID(sid),
              let token = fields["token"], validToken(token),
              let endpoint = URL(string: "http://\(host):\(fields["peer"]!)/") else {
            throw CompanionError.invalidInvitation
        }
        return Invitation(endpoint: endpoint, sessionID: sid.lowercased(), token: token,
                          conversation: try Conversation.fromInvitation(paste))
    }

    static func isPrivateIPv4(_ text: String) -> Bool {
        let pieces = text.split(separator: ".", omittingEmptySubsequences: false)
        guard pieces.count == 4 else { return false }
        let numbers = pieces.compactMap { part -> Int? in
            guard part.allSatisfy({ $0.isASCII && $0.isNumber }),
                  let value = Int(part), (0...255).contains(value), String(value) == part else { return nil }
            return value
        }
        guard numbers.count == 4 else { return false }
        return numbers[0] == 10 || (numbers[0] == 172 && (16...31).contains(numbers[1]))
            || (numbers[0] == 192 && numbers[1] == 168)
    }
    static func canonicalUUID(_ value: String) -> Bool {
        UUID(uuidString: value)?.uuidString.lowercased() == value.lowercased() && value.count == 36
    }
    static func validToken(_ value: String) -> Bool {
        (32...256).contains(value.utf8.count)
            && value.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }
    }
    private static func validPort(_ value: String?) -> Bool {
        guard let value, !value.isEmpty, value.allSatisfy({ $0.isASCII && $0.isNumber }),
              let number = Int(value) else { return false }
        return (1...65535).contains(number)
    }
}

/// A validated, explicitly labeled user paste; never evidence of meeting membership.
public struct Conversation: Sendable, Equatable, CustomStringConvertible, CustomDebugStringConvertible {
    public let url: URL
    public let service: String
    public var description: String { "Conversation(private: redacted)" }
    public var debugDescription: String { description }

    static func fromInvitation(_ text: String) throws -> Conversation? {
        let lines = text.components(separatedBy: .newlines).map { line in
            var value = line.trimmingCharacters(in: .whitespaces)
            while value.hasPrefix(">") { value = String(value.dropFirst()).trimmingCharacters(in: .whitespaces) }
            return value
        }
        var result: Conversation?
        for (index, label) in lines.enumerated() where label.hasPrefix("Optional ")
            && (label.contains("conversation and work sharing") || label.contains("video chat")) {
            guard index + 1 < lines.count else { throw CompanionError.invalidConversation }
            var raw = lines[index + 1]
            for (left, right) in [("<", ">"), ("\"", "\""), ("'", "'"), ("`", "`"), ("(", ")"), ("[", "]")] {
                if raw.hasPrefix(left) {
                    if let last = raw.last, ".,;!".contains(last) { raw.removeLast() }
                    if raw.hasSuffix(right) { raw = String(raw.dropFirst().dropLast()) }
                    break
                }
            }
            let (conversation, host) = try validate(raw)
            guard label == "Optional video chat (\(host)):"
                    || label == "Optional \(conversation.service) conversation and work sharing (\(host)):" else {
                throw CompanionError.invalidConversation
            }
            if let result, result != conversation { throw CompanionError.invalidConversation }
            result = conversation
        }
        return result
    }

    static func validate(_ raw: String) throws -> (Conversation, String) {
        // Foundation versions differ in whether percentEncodedHost preserves
        // the original escapes. Validate the original authority before parsing.
        let authority = raw.dropFirst(8).prefix(while: { !"/?#".contains($0) })
        guard raw.hasPrefix("https://"), raw.utf8.count <= 4096,
              !authority.isEmpty, authority.allSatisfy({
                  $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "." || $0 == "-")
              }), !raw.contains(".."), !raw.contains("\\"),
              !raw.unicodeScalars.contains(where: { $0.value < 33 || $0.value == 127 || CharacterSet.whitespacesAndNewlines.contains($0) }),
              let parts = URLComponents(string: raw), parts.scheme == "https",
              parts.user == nil, parts.password == nil, parts.port == nil,
              let encodedHost = parts.percentEncodedHost, !encodedHost.contains("%"),
              let originalHost = parts.host else {
            throw CompanionError.invalidConversation
        }
        let host = originalHost.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        let special = ["localhost", "local", "localdomain", "internal", "lan", "home", "arpa", "test",
                       "invalid", "example", "example.com", "example.net", "example.org", "onion"]
        guard host.count <= 253, labels.count > 1, originalHost.first != ".",
              labels.allSatisfy({ label in
                  !label.isEmpty && label.count <= 63 && !label.hasPrefix("xn--")
                      && label.first != "-" && label.last != "-"
                      && label.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-") }
              }), !labels.last!.allSatisfy(\.isNumber),
              !special.contains(where: { host == $0 || host.hasSuffix("." + $0) }) else {
            throw CompanionError.invalidConversation
        }
        let service: String
        if host == "webex.com" || host.hasSuffix(".webex.com") { service = "Webex" }
        else if host == "zoom.us" || host.hasSuffix(".zoom.us") { service = "Zoom" }
        else if ["teams.microsoft.com", "teams.live.com"].contains(host) { service = "Microsoft Teams" }
        else if host == "meet.google.com" { service = "Google Meet" }
        else if host == "facetime.apple.com" { service = "FaceTime" }
        else {
            let brands = ["webex", "zoom", "teams", "microsoft", "google", "facetime", "apple"]
            guard !brands.contains(where: host.contains), host != "live.com", !host.hasSuffix(".live.com"),
                  !("." + host + ".").contains(".live.com.") else { throw CompanionError.invalidConversation }
            service = host
        }
        guard let url = parts.url else { throw CompanionError.invalidConversation }
        return (Conversation(url: url, service: service), host)
    }
}
