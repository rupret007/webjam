import Foundation
import Testing
@testable import ArtCompanionCore

@Test(arguments: [
    #"{"creator_profile_key":"art","creator_profile_key":"music"}"#,
    #"{"key":1,"\u006bey":2}"#,
    #"{"ke\u0079":1,"key":2}"#,
    #"{"nested":{"a":1,"\u0061":2}}"#,
    #"{"list":[{"x":1,"x":2}]}"#,
    #"{"\uD83C\uDFA8":1,"🎨":2}"#,
    #"{"escaped\"key":1,"escaped\u0022key":2}"#
])
func ambiguousJSONKeysAreRejected(json: String) {
    #expect(throws: CompanionError.invalidResponse) {
        try StrictJSON.object(Data(json.utf8))
    }
}

@Test func independentObjectsAndEscapedValuesRemainValid() throws {
    let object = try StrictJSON.object(Data(#"{"first":{"name":"one"},"second":{"name":"two"},"escaped":"\"{[]}\\","number":-1.2e+3,"values":[true,false,null]}"#.utf8))
    #expect(object.count == 5)
    #expect((object["first"] as? [String: Any])?["name"] as? String == "one")
}

@Test(arguments: [
    "", "[]", "null", "true", "1", #""text""#,
    #"{"a":1,}"#, #"{"a":[1,]}"#, #"{"a":1 "b":2}"#,
    #"{"a":01}"#, #"{"a":+1}"#, #"{"a":1.}"#, #"{"a":NaN}"#,
    #"{"a":tru}"#, #"{"a":}"#, #"{"a":"\x41"}"#,
    #"{"a":"\uD800"}"#, #"{"a":"\u12"}"#,
    #"{"a":"unterminated}"#, #"{} trailing"#, "{\"a\":\"line\nbreak\"}"
])
func malformedAndNonObjectJSONAreRejected(json: String) {
    #expect(throws: CompanionError.invalidResponse) {
        try StrictJSON.object(Data(json.utf8))
    }
}

@Test func malformedUTF8IsRejectedWithoutRetainingInput() {
    let data = Data([0x7B, 0x22, 0x61, 0x22, 0x3A, 0x22, 0xFF, 0x22, 0x7D])
    #expect(throws: CompanionError.invalidResponse) { try StrictJSON.object(data) }
}

@Test func JSONDepthAndByteBudgetsAreEnforced() throws {
    func nested(_ arrays: Int) -> Data {
        Data(("{\"nested\":" + String(repeating: "[", count: arrays) + "0"
              + String(repeating: "]", count: arrays) + "}").utf8)
    }
    _ = try StrictJSON.object(nested(31))
    #expect(throws: CompanionError.invalidResponse) { try StrictJSON.object(nested(32)) }

    let overhead = Data("{\"padding\":\"\"}".utf8).count
    let accepted = Data(("{\"padding\":\"" + String(repeating: "x", count: 65536 - overhead) + "\"}").utf8)
    _ = try StrictJSON.object(accepted)
    #expect(throws: CompanionError.oversizedResponse) { try StrictJSON.object(accepted + Data([0x20])) }
}

@Test func roomDecoderRejectsDuplicateTruthAndExplicitNullStart() throws {
    let duplicate = "{\"session_id\":\"\(testSessionID)\",\"generation\":0,"
        + "\"creator_profile_key\":\"art\",\"creator_profile_key\":\"music\"}"
    #expect(throws: CompanionError.invalidResponse) {
        try RoomSummary.decode(Data(duplicate.utf8), sessionID: testSessionID)
    }
    #expect(throws: CompanionError.invalidResponse) {
        try RoomSummary.decode(roomData(["art_start_key": NSNull()]), sessionID: testSessionID)
    }
    let legacy = try RoomSummary.decode(roomData(), sessionID: testSessionID)
    #expect(legacy.artStart == .unspecified)
}

@Test func roomDecoderRejectsUnsupportedEndedVideoState() {
    #expect(throws: CompanionError.invalidResponse) {
        try RoomSummary.decode(roomData([
            "reference_video": ["shared": true, "state": "ended"]
        ]), sessionID: testSessionID)
    }
}

private final class DuplicateEnrollmentProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let body = Data(("{\"participant_id\":\"\(testSessionID)\","
            + "\"participant_id\":\"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\","
            + "\"installation_id\":\"\(testSessionID)\","
            + "\"participant_token\":\"\(String(repeating: "b", count: 43))\"}").utf8)
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json", "Content-Length": String(body.count)])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@Test func enrollmentDecoderRejectsDuplicateIdentity() async throws {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [DuplicateEnrollmentProtocol.self]
    let client = LANClient(invitation: try Invitation.parse(testInvite), configuration: configuration)
    defer { client.close() }
    await #expect(throws: CompanionError.invalidResponse) {
        try await client.enroll(installationID: testSessionID, displayName: "Artist")
    }
}
