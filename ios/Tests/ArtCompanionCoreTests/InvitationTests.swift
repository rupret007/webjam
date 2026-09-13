import Foundation
import Testing
@testable import ArtCompanionCore

let testSessionID = "12345678-1234-4321-8123-123456789abc"
let testInvite = "webjam://join?v=2&host=192.168.1.10&port=22124&session=Art+room&sid=\(testSessionID)&peer=45678&token=\(String(repeating: "a", count: 43))"

@Test func completeForwardedInvitation() throws {
    let paste = """
    > WebJam invitation
    > <\(testInvite)>
    > Optional Webex conversation and work sharing (studio.webex.com):
    > <https://studio.webex.com/meet/artist?private=meeting-context>
    Unrelated signature https://github.com
    """
    let invite = try Invitation.parse(paste)
    #expect(invite.sessionID == testSessionID)
    #expect(invite.endpoint.absoluteString == "http://192.168.1.10:45678/")
    #expect(invite.conversation?.service == "Webex")
    #expect(!String(reflecting: invite).contains("192.168"))
    #expect(!String(reflecting: invite).contains(String(repeating: "a", count: 43)))
    #expect(!String(reflecting: invite.conversation!).contains("meeting-context"))
}

@Test(arguments: ["8.8.8.8", "127.0.0.1", "localhost", "192.168.001.10", "169.254.1.1", "172.32.0.1",
                  "192.167.1.1", "10.0.0.256", "0x0a000001", "[::1]", "10.0.0.1.evil.com"])
func invitationsCannotMoveBearerOffThePrivateLAN(host: String) {
    #expect(throws: CompanionError.self) { try Invitation.parse(testInvite.replacingOccurrences(of: "192.168.1.10", with: host)) }
}

@Test(arguments: ["&token=duplicate", "&sid=duplicate", "&extra=1", "&v=3", "&", "#fragment"])
func ambiguousInvitationRejected(suffix: String) {
    #expect(throws: CompanionError.self) { try Invitation.parse(testInvite + suffix) }
}

@Test(arguments: ["10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.255.254"])
func allRFC1918RangesSupported(host: String) throws {
    _ = try Invitation.parse(testInvite.replacingOccurrences(of: "192.168.1.10", with: host))
}

@Test func malformedAndUnsupportedInvitations() {
    for value in ["", String(repeating: "a", count: 8193), testInvite + " " + testInvite.replacingOccurrences(of: "peer=45678", with: "peer=12345"),
                  testInvite.replacingOccurrences(of: "join?", with: "join:80?"),
                  testInvite.replacingOccurrences(of: "join?", with: "join:?"),
                  testInvite.replacingOccurrences(of: "join?", with: "user@join?"),
                  testInvite.replacingOccurrences(of: "v=2", with: "v=1"),
                  testInvite.replacingOccurrences(of: "v=2", with: "v=3"),
                  testInvite.replacingOccurrences(of: "peer=45678", with: "peer=0"),
                  testInvite.replacingOccurrences(of: "peer=45678", with: "peer=65536"),
                  testInvite.replacingOccurrences(of: "session=Art+room", with: "session=bad%ZZ"),
                  testInvite.replacingOccurrences(of: testSessionID, with: "not-a-uuid")] {
        #expect(throws: CompanionError.self) { try Invitation.parse(value) }
    }
}

@Test(arguments: ["http://studio.webex.com/meet/x", "https://user@studio.webex.com/meet/x",
                  "https://studio.webex.com:443/meet/x", "https://studio.webex.com:/meet/x",
                  "https://%73tudio.webex.com/meet/x", "https://webex.com.evil.net/meet/x",
                  "https://notwebex.com/meet/x", "https://teams.foo.net/meet/x", "https://192.168.1.1/",
                  "https://live.com.evil.net/meeting",
                  "https://meeting.local/", "https://meet.example.com/", "https://xn--example-9db.com/",
                  "https://meet.jit.si/a/../b", "https://meet.jit.si/a\\b", "https://meet.jit.si/a\tb"])
func unsafeConversationRejected(url: String) {
    #expect(throws: CompanionError.self) { try Conversation.validate(url) }
}

@Test func meetingLabelsAndMissingConversation() throws {
    #expect(try Invitation.parse(testInvite).conversation == nil)
    for (url, service) in [("https://us02web.zoom.us/j/123", "Zoom"),
                           ("https://teams.microsoft.com/l/meetup", "Microsoft Teams"),
                           ("https://meet.google.com/abc", "Google Meet"),
                           ("https://facetime.apple.com/join", "FaceTime"),
                           ("https://meet.jit.si/Room", "meet.jit.si")] {
        #expect(try Conversation.validate(url).0.service == service)
    }
    #expect(throws: CompanionError.self) {
        try Invitation.parse(testInvite + "\nOptional Zoom conversation and work sharing (studio.webex.com):\nhttps://studio.webex.com/meet/x")
    }
    #expect(throws: CompanionError.self) {
        try Invitation.parse(testInvite + "\nOptional video chat (studio.webex.com):\nhttps://studio.webex.com/meet/x\nOptional video chat (studio.webex.com):\nhttps://studio.webex.com/meet/y")
    }
}
