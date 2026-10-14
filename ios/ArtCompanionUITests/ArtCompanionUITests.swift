import XCTest

final class ArtCompanionUITests: XCTestCase {
    /// Bounded wait for launch, first-responder, and keyboard-avoidance layout.
    /// Fresh simulators in CI are slow; this is not a sleep and not a retry of the test.
    private let ready: TimeInterval = 8

    override func setUpWithError() throws { continueAfterFailure = false }

    @MainActor private func launch(_ fixture: String = "", largeText: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["WEBJAM_ART_LAYOUT_SCENARIO"] = fixture
        if largeText { app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"] }
        app.launch()
        return app
    }

    /// Wait until `format` is true for `element`. Used for exists/hittable/focus, not for sleeping.
    @MainActor private func awaiting(
        _ element: XCUIElement,
        _ format: String,
        timeout: TimeInterval? = nil
    ) -> Bool {
        let expectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: format),
            object: element
        )
        return XCTWaiter().wait(for: [expectation], timeout: timeout ?? ready) == .completed
    }

    @MainActor private func debugName(_ element: XCUIElement) -> String {
        element.identifier.isEmpty ? element.label : element.identifier
    }

    /// Visible, keyboard-unobscured portion of the Join/room scroll view.
    @MainActor private func visibleFormFrame(_ scroll: XCUIElement, in app: XCUIApplication) -> CGRect {
        let frame = scroll.frame
        var bottom = frame.maxY
        if app.keyboards.element.exists {
            bottom = min(bottom, app.keyboards.element.frame.minY)
        }
        if app.buttons["Done"].exists {
            // The keyboard toolbar can sit above app.keyboards and still steal hits.
            bottom = min(bottom, app.buttons["Done"].frame.minY)
        }
        return CGRect(
            x: frame.minX,
            y: frame.minY,
            width: frame.width,
            height: max(bottom - frame.minY, 0)
        )
    }

    /// Drag inside the unobscured form. A center `swipeUp` can hit the keyboard or
    /// a TextEditor and leave invitation off-screen (iPad) or still covered (iPhone).
    @MainActor private func nudgeForm(_ scroll: XCUIElement, in app: XCUIApplication, towardTop: Bool) {
        let frame = scroll.frame
        let visible = visibleFormFrame(scroll, in: app)
        guard frame.height > 1, visible.height > 8 else { return }
        let low = (visible.minY - frame.minY + visible.height * 0.25) / frame.height
        let high = (visible.minY - frame.minY + visible.height * 0.75) / frame.height
        let startY = towardTop ? min(high, 0.92) : max(low, 0.08)
        let endY = towardTop ? max(low, 0.08) : min(high, 0.92)
        let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: startY))
        let end = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: endY))
        start.press(forDuration: 0.05, thenDragTo: end)
    }

    @MainActor private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        XCTAssertTrue(element.waitForExistence(timeout: ready), "missing \(debugName(element))")
        if element.isHittable { return }

        let scroll = app.scrollViews["companion-scroll"]
        XCTAssertTrue(scroll.waitForExistence(timeout: ready), "missing companion-scroll")

        let deadline = Date().addingTimeInterval(ready)
        while Date() < deadline {
            if awaiting(element, "isHittable == true", timeout: 0.4) { return }

            let target = element.frame
            let visible = visibleFormFrame(scroll, in: app)
            // Mid-layout or keyboard animation: the field is already on screen.
            // Swiping here is what sent invitation off-screen on iPad.
            if target != .zero, visible.contains(CGPoint(x: target.midX, y: target.midY)) {
                continue
            }
            if target == .zero || visible.isEmpty {
                continue
            }
            nudgeForm(scroll, in: app, towardTop: target.midY > visible.midY)
        }
        XCTAssertTrue(element.isHittable, "not hittable after scrolling \(debugName(element))")
    }

    /// Tap only after the control is hittable, then type only after it is first responder.
    @MainActor private func typeOnceReady(_ element: XCUIElement, _ text: String, in app: XCUIApplication) {
        reveal(element, in: app)
        XCTAssertTrue(element.isHittable, "not hittable before tap \(debugName(element))")
        element.tap()
        XCTAssertTrue(
            awaiting(element, "hasKeyboardFocus == true"),
            "no keyboard focus after tap \(debugName(element))"
        )
        // Software keyboard toolbar is the recovery path under test.
        XCTAssertTrue(app.buttons["Done"].waitForExistence(timeout: ready), "keyboard toolbar missing")
        element.typeText(text)
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
        let name = app.textFields["guest-name"]
        XCTAssertTrue(join.waitForExistence(timeout: ready))
        XCTAssertTrue(name.waitForExistence(timeout: ready))
        XCTAssertTrue(awaiting(name, "isHittable == true"), "guest-name not hittable after launch")
        XCTAssertFalse(join.isEnabled)
        capture("join-phone-or-tablet", app: app)
        typeOnceReady(name, "Mobile Artist\n", in: app)
        XCTAssertTrue((name.value as? String)?.contains("Mobile Artist") == true)
        let invite = app.textViews["invitation"]
        typeOnceReady(invite, "incomplete invitation", in: app)
        XCTAssertTrue((invite.value as? String)?.contains("incomplete invitation") == true)
        capture("join-keyboard", app: app)
        // A keyboard can cover the lower action; Done always returns the scrollable form.
        let done = app.buttons["Done"]
        XCTAssertTrue(done.waitForExistence(timeout: ready))
        done.tap()
        XCTAssertTrue(
            awaiting(app.keyboards.element, "exists == false"),
            "keyboard still present after Done"
        )
        reveal(join, in: app)
        XCTAssertTrue(join.isEnabled)
        XCTAssertGreaterThanOrEqual(join.frame.height, 44)
        join.tap()
        XCTAssertTrue(
            app.otherElements["join-error"].waitForExistence(timeout: ready)
                || app.staticTexts["join-error"].waitForExistence(timeout: 2)
        )
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
            XCTAssertTrue(app.staticTexts["room-title"].waitForExistence(timeout: ready))
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
        let title = app.staticTexts["room-title"]
        XCTAssertTrue(title.waitForExistence(timeout: ready))
        XCTAssertEqual(title.label, "Follow the Music room")
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
