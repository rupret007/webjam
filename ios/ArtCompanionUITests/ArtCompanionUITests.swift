import XCTest

final class ArtCompanionUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }

    @MainActor private func launch(_ fixture: String = "", largeText: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["WEBJAM_ART_LAYOUT_SCENARIO"] = fixture
        if largeText { app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"] }
        app.launch()
        return app
    }

    @MainActor private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<12 {
            if element.isHittable { return }
            // Swiping the application can hit the keyboard. Exercise the form's
            // actual scroll view, whose visible bounds follow keyboard avoidance.
            app.scrollViews["companion-scroll"].swipeUp()
        }
        XCTAssertTrue(element.isHittable)
    }

    @MainActor private func capture(_ name: String, app: XCUIApplication) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    @MainActor func testJoinKeyboardAndRecovery() {
        let app = launch()
        let join = app.buttons["join-room"]
        XCTAssertFalse(join.isEnabled)
        capture("join-phone-or-tablet", app: app)
        let name = app.textFields["guest-name"]
        name.tap()
        name.typeText("Mobile Artist\n")
        let invite = app.textViews["invitation"]
        reveal(invite, in: app)
        invite.tap()
        invite.typeText("incomplete invitation")
        capture("join-keyboard", app: app)
        // A keyboard can cover the lower action; Done always returns the scrollable form.
        app.buttons["Done"].tap()
        reveal(join, in: app)
        XCTAssertTrue(join.isEnabled)
        XCTAssertGreaterThanOrEqual(join.frame.height, 44)
        join.tap()
        XCTAssertTrue(app.otherElements["join-error"].exists || app.staticTexts["join-error"].exists)
        XCTAssertEqual(invite.value as? String, "")
        capture("join-recovery", app: app)
        reveal(app.staticTexts["Make together"], in: app)
        reveal(app.staticTexts["Paint along"], in: app)
        capture("art-starts", app: app)
        reveal(app.staticTexts["music-join-honesty"], in: app)
    }

    @MainActor func testArtRoomsAndConversationAtLargeText() {
        for fixture in ["make", "paint", "legacy"] {
            let app = launch(fixture, largeText: true)
            XCTAssertTrue(app.staticTexts["room-title"].exists)
            capture("\(fixture)-large-text-top", app: app)
            reveal(app.staticTexts["canvas-elsewhere"], in: app)
            capture("\(fixture)-canvas-elsewhere", app: app)
            let conversation = app.buttons["open-conversation"]
            reveal(conversation, in: app)
            XCTAssertGreaterThanOrEqual(conversation.frame.height, 44)
            capture("\(fixture)-conversation", app: app)
            reveal(app.buttons["leave-room"], in: app)
            XCTAssertFalse(app.buttons["Host"].exists)
            app.terminate()
        }
    }

    @MainActor func testMusicHonesty() {
        let app = launch("music")
        XCTAssertEqual(app.staticTexts["room-title"].label, "Follow the Music room")
        reveal(app.staticTexts["music-room-honesty"], in: app)
        capture("music-follow-only", app: app)
        reveal(app.buttons["open-conversation"], in: app)
        XCTAssertFalse(app.buttons["Host"].exists)
        XCTAssertFalse(app.buttons["Record"].exists)
        XCTAssertFalse(app.buttons["Play"].exists)
    }

    @MainActor func testMissingConversationOffersAnHonestNextStep() {
        let app = launch("missing")
        reveal(app.staticTexts["missing-conversation"], in: app)
        capture("missing-conversation", app: app)
        XCTAssertFalse(app.buttons["open-conversation"].exists)
        reveal(app.buttons["leave-room"], in: app)
    }
}
