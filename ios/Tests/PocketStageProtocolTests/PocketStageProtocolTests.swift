import Testing
import Foundation
@testable import PocketStageProtocol

private let sessionID = UUID(uuidString: "0E6F9698-93BB-4272-92CD-43C760D1F793")!
private let claimID = "33333333-3333-4333-8333-333333333333"
private let pairingToken = String(repeating: "A", count: 43)
private let fingerprint = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

private func goldenSnapshot(
    mutating mutate: (inout [String: Any]) -> Void
) throws -> Data {
    let fixtureURL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("Fixtures/pocket_stage_v1_golden.json")
    let root = try JSONSerialization.jsonObject(
        with: Data(contentsOf: fixtureURL)
    ) as! [String: Any]
    var envelope = root["snapshot"] as! [String: Any]
    var body = envelope["body"] as! [String: Any]
    mutate(&body)
    envelope["body"] = body
    return try JSONSerialization.data(withJSONObject: envelope)
}

@Test func validPairingQRRoundTrips() throws {
    let payload = try PairingPayload(
        sessionID: sessionID,
        endpoint: try StageEndpoint("wss://192.168.1.10:18443/v1/pocket"),
        certificateFingerprint: try CertificateFingerprint(hex: fingerprint),
        token: pairingToken,
        expiresAtUnix: 2_000_000_000,
        displayName: "Pocket Stage"
    )
    #expect(try PairingPayload.parseQRCode(payload.qrCodeString()) == payload)
}

@Test func pairingRejectsInsecureEndpoint() {
    #expect(throws: ProtocolValidationError.invalidEndpoint) {
        try PairingPayload.parseQRCode("pocketstage://pair?v=1&session=0e6f9698-93bb-4272-92cd-43c760d1f793&endpoint=ws%3A%2F%2F192.168.1.10%3A18443%2Fv1%2Fpocket&token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&fingerprint=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef&expires=2000000000")
    }
}

@Test func pairingRejectsUnknownOrDuplicatedFields() {
    let base = "pocketstage://pair?v=1&session=0e6f9698-93bb-4272-92cd-43c760d1f793&endpoint=wss%3A%2F%2F192.168.1.10%3A18443%2Fv1%2Fpocket&token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&fingerprint=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef&expires=2000000000"
    #expect(throws: ProtocolValidationError.self) { try PairingPayload.parseQRCode(base + "&admin=true") }
    #expect(throws: ProtocolValidationError.self) { try PairingPayload.parseQRCode(base + "&token=abcdefghijklmnop") }
}

@Test func endpointRequiresExactPrivateGatewayShape() throws {
    let rejected = [
        "wss://stage.example.com:18443/v1/pocket",
        "wss://8.8.8.8:18443/v1/pocket",
        "wss://127.0.0.1:18443/v1/pocket",
        "wss://192.168.1.10/v1/pocket",
        "wss://192.168.1.10:18443/other",
        "wss://192.168.1.10:18443/v1/pocket?admin=true",
        "wss://user@192.168.1.10:18443/v1/pocket",
        "ws://192.168.1.10:18443/v1/pocket",
    ]
    for value in rejected {
        #expect(throws: ProtocolValidationError.self) {
            try StageEndpoint(value)
        }
    }
    #expect(
        try StageEndpoint("wss://10.0.0.5:443/v1/pocket").url.host == "10.0.0.5"
    )
    #expect(
        try StageEndpoint("wss://172.31.255.1:65535/v1/pocket").url.port == 65_535
    )
}

@Test func commandEnvelopeUsesExactSnakeCaseWireKeys() throws {
    let body = try CommandBody(command: .setParticipantFader, generation: 4, expectedRevision: 9, arguments: ["slot": .integer(2), "fader_level": .integer(83)])
    let original = try StageEnvelope(kind: .command, generation: 4, sequence: 7, body: body, messageID: CanonicalUUID(sessionID), sentAtUnixMS: 123)
    let json = try JSONSerialization.jsonObject(with: WireCodec.encode(original)) as! [String: Any]
    #expect(json["message_id"] as? String == sessionID.uuidString.lowercased())
    #expect(json["sent_at_unix_ms"] as? Int == 123)
    let decoded = try WireCodec.decodeIncomingMessage(WireCodec.encode(original))
    guard case let .command(envelope) = decoded else { Issue.record("Expected command envelope"); return }
    #expect(envelope.body.command == StageCommand.setParticipantFader)
    #expect(envelope.body.arguments["fader_level"] == JSONValue.integer(83))
}

@Test func pairEnvelopeHasOneTokenAndPhoneGeneratedCanonicalClaim() throws {
    let claim = try CanonicalUUID(claimID)
    let body = try PairBody(capability: pairingToken, claimID: claim)
    let envelope = try StageEnvelope(kind: .pair, generation: 0, sequence: 0, body: body, messageID: CanonicalUUID(sessionID), sentAtUnixMS: 123)
    let json = try JSONSerialization.jsonObject(with: WireCodec.encode(envelope)) as! [String: Any]
    let wireBody = json["body"] as! [String: Any]
    #expect(wireBody["capability"] as? String == pairingToken)
    #expect(wireBody["claim_id"] as? String == claimID)
    #expect(Set(wireBody.keys) == ["capability", "claim_id"])
}

@Test func wireCodecProducesUTF8TextFrames() throws {
    let body = try PairBody(capability: pairingToken, claimID: CanonicalUUID(claimID))
    let envelope = try StageEnvelope(kind: .pair, generation: 0, sequence: 0, body: body, messageID: CanonicalUUID(sessionID), sentAtUnixMS: 123)
    let text = try WireCodec.encodeText(envelope)
    #expect(text.first == "{")
    #expect(text.contains("\"kind\":\"pair\""))
    guard case let .pair(decoded) = try WireCodec.decodeIncomingMessage(Data(text.utf8)) else {
        Issue.record("Expected pair envelope from UTF-8 text")
        return
    }
    #expect(decoded.body == body)
}

@Test func addMarkerRequiresExactArguments() throws {
    #expect(throws: ProtocolValidationError.invalidMessage) {
        try CommandBody(command: .addMarker, generation: 1, expectedRevision: 1, arguments: [:])
    }
    _ = try CommandBody(command: .addMarker, generation: 1, expectedRevision: 1, arguments: ["at_ms": .integer(900), "label": .string("Chorus")])
}

@Test func goldenWireFixtureDecodesAndReencodesExactly() throws {
    let fixtureURL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("Fixtures/pocket_stage_v1_golden.json")
    let root = try JSONSerialization.jsonObject(with: Data(contentsOf: fixtureURL)) as! [String: Any]
    #expect(Set(root.keys) == ["pair", "snapshot", "fader_command", "confirmed_receipt"])

    for key in ["pair", "snapshot", "fader_command", "confirmed_receipt"] {
        let original = root[key] as! [String: Any]
        let data = try JSONSerialization.data(withJSONObject: original, options: [.sortedKeys])
        let decoded = try WireCodec.decodeIncomingMessage(data)
        let encoded: Data
        switch decoded {
        case let .pair(message): encoded = try WireCodec.encode(message)
        case let .snapshot(message):
            #expect(message.body.participants.first?.label == "Lead Guitar")
            encoded = try WireCodec.encode(message)
        case let .command(message): encoded = try WireCodec.encode(message)
        case let .receipt(message): encoded = try WireCodec.encode(message)
        }
        let roundTrip = try JSONSerialization.jsonObject(with: encoded) as! NSDictionary
        #expect(roundTrip == original as NSDictionary)
    }
}

@Test func snapshotRejectsUnsafeOrAmbiguousCollectionsAndStates() throws {
    let unknownState = try goldenSnapshot { body in
        body["recording_state"] = "mystery"
    }
    let controlText = try goldenSnapshot { body in
        var participants = body["participants"] as! [[String: Any]]
        participants[0]["label"] = "Lead\nGuitar"
        body["participants"] = participants
    }
    let duplicateSlot = try goldenSnapshot { body in
        var participants = body["participants"] as! [[String: Any]]
        participants.append(participants[0])
        body["participants"] = participants
    }
    let unsortedSlots = try goldenSnapshot { body in
        var participants = body["participants"] as! [[String: Any]]
        var second = participants[0]
        second["slot"] = 2
        participants = [second, participants[0]]
        body["participants"] = participants
    }
    let overlappingSections = try goldenSnapshot { body in
        var sections = body["sections"] as! [[String: Any]]
        var second = sections[0]
        second["ordinal"] = 2
        second["start_ms"] = 9_000
        second["end_ms"] = 20_000
        sections.append(second)
        body["sections"] = sections
    }
    let tooManyParticipants = try goldenSnapshot { body in
        let first = (body["participants"] as! [[String: Any]])[0]
        body["participants"] = (1...65).map { slot in
            var participant = first
            participant["slot"] = slot
            return participant
        }
    }

    for data in [
        unknownState,
        controlText,
        duplicateSlot,
        unsortedSlots,
        overlappingSections,
        tooManyParticipants,
    ] {
        #expect(throws: ProtocolValidationError.self) {
            try WireCodec.decodeIncomingMessage(data)
        }
    }
}
