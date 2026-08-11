from core.meeting_link import (
    GENERIC_MEETING_SERVICE_KEY,
    SUPPORTED_MEETING_SERVICES_TEXT,
    identify_meeting_service,
    is_allowed_meeting_link,
    meeting_handoff_platform_error,
    meeting_link_error,
    meeting_link_hostname,
    meeting_service_label,
)


def test_all_known_services_validate_and_identify():
    cases = {
        "https://myband.webex.com/meet/practice": ("webex", "myband.webex.com"),
        "https://webex.com/meet/practice": ("webex", "webex.com"),
        "https://us02web.zoom.us/j/1234567890?pwd=abc": (
            "zoom",
            "us02web.zoom.us",
        ),
        "https://zoom.us/j/1234567890": ("zoom", "zoom.us"),
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting": (
            "teams",
            "teams.microsoft.com",
        ),
        "https://teams.live.com/meet/9351234567890": (
            "teams",
            "teams.live.com",
        ),
        "https://meet.google.com/abc-defg-hij": (
            "google_meet",
            "meet.google.com",
        ),
        "https://facetime.apple.com/join#v=1&p=abc": (
            "facetime",
            "facetime.apple.com",
        ),
        # Bare domains gain https:// exactly like the Webex-only policy.
        "myband.webex.com/meet/practice": ("webex", "myband.webex.com"),
    }
    for url, (service, hostname) in cases.items():
        assert meeting_link_error(url) is None, url
        assert is_allowed_meeting_link(url), url
        assert identify_meeting_service(url) == service, url
        assert meeting_link_hostname(url) == hostname, url


def test_unrelated_public_https_hosts_use_neutral_generic_provider():
    cases = (
        ("https://meet.jit.si/WebJamBand?config.prejoinPageEnabled=true", "meet.jit.si"),
        ("whereby.com/webjam-band", "whereby.com"),
        ("https://sessions.custom-company.co/rooms/private#join", "sessions.custom-company.co"),
    )
    for url, hostname in cases:
        assert meeting_link_error(url) is None, url
        assert is_allowed_meeting_link(url), url
        assert identify_meeting_service(url) == GENERIC_MEETING_SERVICE_KEY, url
        assert meeting_service_label(identify_meeting_service(url)) == (
            "Meeting service"
        )
        assert meeting_link_hostname(url) == hostname


def test_lookalike_and_hostile_links_are_rejected():
    rejected = (
        "https://zoom.us.evil.example/j/123",
        "https://meet.google.com.evil.example/abc",
        "https://teams.microsoft.com.evil.example/l/x",
        "https://facetime.apple.com.evil.example/join",
        "https://notzoom.us/j/123",
        "https://gmeet.google.com/abc",
        "https://webex-login.evil.com/meet/123",
        "https://teams.evil.com/join/123",
        "https://example.com/meet/band",
        "http://zoom.us/j/123",
        "https://user:pass@zoom.us/j/123",
        "https://zoom.us:8443/j/123",
        "https://%7aoom.us/j/123",
        "https://localhost/meet",
        "https://127.0.0.1/meet",
        "https://10.0.0.8/meet",
        "https://[::1]/meet",
        "https://conference.local/meet",
        "https://conference.internal/meet",
        "https://conference.test/meet",
        "https://xn--wbe-xpa.example.co/meet",
        "https://meet.jit.si:/room",
        "https://meet.jit.si\\@evil.com/room",
        "https://zoom..us/j/123",
        "https://zoom.us/j/1 23",
        "https://meet.google.com/abc\n-defg",
        "",
        "not-a-link",
    )
    for url in rejected:
        assert meeting_link_error(url), url
        assert not is_allowed_meeting_link(url), url
        assert identify_meeting_service(url) is None, url
        assert meeting_link_hostname(url) == "", url


def test_empty_link_help_names_known_services_and_the_generic_option():
    error = meeting_link_error("")
    assert error is not None
    assert SUPPORTED_MEETING_SERVICES_TEXT in error
    for name in ("Webex", "Zoom", "Microsoft Teams", "Google Meet", "FaceTime"):
        assert name in SUPPORTED_MEETING_SERVICES_TEXT, name
    assert "another meeting platform" in SUPPORTED_MEETING_SERVICES_TEXT


def test_known_service_lookalikes_never_receive_a_branded_identity():
    for url in (
        "https://zoom.us.evil.com/j/123",
        "https://notzoom.us/j/123",
        "https://gmeet.google.com/abc",
        "https://webex-login.evil.com/meet/123",
    ):
        assert meeting_link_error(url)
        assert identify_meeting_service(url) is None
        assert meeting_service_label(identify_meeting_service(url)) == (
            "meeting service"
        )


def test_service_labels_are_human_readable():
    assert meeting_service_label("webex") == "Webex"
    assert meeting_service_label("zoom") == "Zoom"
    assert meeting_service_label("teams") == "Microsoft Teams"
    assert meeting_service_label("google_meet") == "Google Meet"
    assert meeting_service_label("facetime") == "FaceTime"
    assert meeting_service_label(GENERIC_MEETING_SERVICE_KEY) == "Meeting service"
    assert meeting_service_label(None) == "meeting service"
    assert meeting_service_label("unknown") == "meeting service"


def test_facetime_handoff_is_macos_only_and_the_error_is_honest():
    facetime = "https://facetime.apple.com/join#v=1&p=abc"
    assert meeting_handoff_platform_error(facetime, platform="darwin") is None
    for platform in ("win32", "linux"):
        error = meeting_handoff_platform_error(facetime, platform=platform)
        assert error is not None and "Mac" in error
        assert "another browser-capable meeting link" in error
    # Non-FaceTime services carry no platform restriction anywhere.
    for url in (
        "https://myband.webex.com/meet/practice",
        "https://zoom.us/j/123",
        "https://teams.microsoft.com/l/meetup-join/19",
        "https://meet.google.com/abc-defg-hij",
        "https://meet.jit.si/webjam-band",
    ):
        for platform in ("darwin", "win32", "linux"):
            assert (
                meeting_handoff_platform_error(url, platform=platform) is None
            ), (url, platform)
    # An invalid link is a validation problem, not a platform problem.
    assert (
        meeting_handoff_platform_error("https://example.com/x", platform="win32")
        is None
    )


def test_meeting_links_redact_to_origin_only_in_logs_and_mappings():
    from core.redaction import redact_mapping, redact_meeting_url, redact_text

    reductions = {
        "https://us02web.zoom.us/j/123?pwd=secret": "https://us02web.zoom.us/[redacted]",
        "https://teams.microsoft.com/l/meetup-join/19%3aroom": (
            "https://teams.microsoft.com/[redacted]"
        ),
        "https://teams.live.com/meet/935123": "https://teams.live.com/[redacted]",
        "https://meet.google.com/abc-defg-hij": "https://meet.google.com/[redacted]",
        "https://facetime.apple.com/join#v=1&p=priv": (
            "https://facetime.apple.com/[redacted]"
        ),
    }
    for url, origin_only in reductions.items():
        assert redact_meeting_url(url) == origin_only, url
        redacted_line = redact_text(f"opening {url} now")
        assert origin_only in redacted_line, url
        for private in ("pwd=secret", "abc-defg-hij", "meetup-join", "p=priv"):
            assert private not in redacted_line

    # Lookalike hosts never earn a trusted origin.
    assert redact_meeting_url("https://zoom.us.evil.example/j/1") == "[redacted]"
    hostile = redact_text("see https://zoom.us.evil.example/j/1?pwd=x now")
    assert "https://zoom.us/" not in hostile.replace("zoom.us.evil", "")
    # The historical webex_url settings field may hold any supported link.
    mapping = redact_mapping({"webex_url": "https://zoom.us/j/123?pwd=s"})
    assert mapping["webex_url"] == "https://zoom.us/[redacted]"

    # Generic hosts may reveal a private company/community name. Diagnostics
    # retain neither that host nor its room, query, or fragment.
    generic = "https://sessions.custom-company.co/private-room?token=x#join"
    assert redact_meeting_url(generic) == "[redacted]"
    assert redact_mapping({"webex_url": generic})["webex_url"] == "[redacted]"
    redacted_generic = redact_text(f"opening {generic} now")
    assert redacted_generic.startswith("opening [redacted]")
    for private in ("custom-company", "private-room", "token=x", "join"):
        assert private not in redacted_generic
    assert redact_text("opening http://zoom.us/private-room?token=x") == (
        "opening [redacted]"
    )


def test_app_identity_registry_matches_the_live_webex_contract():
    from core.meeting_link import MEETING_APP_IDENTITIES, meeting_app_identity
    from services.webex_app import WEBEX_MAC_BUNDLE_ID, WEBEX_MAC_TEAM_ID

    # Every link-policy service has an identity entry and vice versa.
    assert set(MEETING_APP_IDENTITIES) == {
        "webex",
        "zoom",
        "teams",
        "google_meet",
        "facetime",
    }
    # The registry must never drift from the one identity WebJam actually
    # verifies today.
    webex = meeting_app_identity("webex")
    assert webex is not None
    assert webex["macos_bundle_ids"] == (WEBEX_MAC_BUNDLE_ID,)
    assert webex["macos_team_id"] == WEBEX_MAC_TEAM_ID

    zoom = meeting_app_identity("zoom")
    assert zoom["macos_bundle_ids"] == ("us.zoom.xos",)
    assert zoom["macos_team_id"] == "BJ4HAAB9B3"
    teams = meeting_app_identity("teams")
    assert teams["macos_bundle_ids"][0] == "com.microsoft.teams2"
    assert teams["macos_team_id"] == "UBF8T346G9"
    # Google Meet is honestly browser-only; FaceTime is Apple system software.
    assert meeting_app_identity("google_meet")["browser_only"] is True
    assert meeting_app_identity("google_meet")["macos_bundle_ids"] == ()
    facetime = meeting_app_identity("facetime")
    assert facetime["apple_system"] is True
    assert facetime["macos_team_id"] is None
    # Multi-platform facts: Authenticode publisher names where a Windows
    # app exists, and honest Linux availability everywhere.
    assert zoom["windows_publisher_cn"] == "Zoom Video Communications, Inc."
    assert teams["windows_publisher_cn"] == "Microsoft Corporation"
    assert webex["windows_publisher_cn"] == "Cisco Systems, Inc."
    assert facetime["windows_publisher_cn"] is None
    assert {i["linux"] for i in MEETING_APP_IDENTITIES.values()} <= {
        "native",
        "browser",
        "unavailable",
    }
    assert meeting_app_identity("facetime")["linux"] == "unavailable"
    assert meeting_app_identity("teams")["linux"] == "browser"
    # Unknown services return None, and callers get copies, not the registry.
    assert meeting_app_identity("skype") is None
    copy = meeting_app_identity("zoom")
    copy["macos_team_id"] = "tampered"
    assert meeting_app_identity("zoom")["macos_team_id"] == "BJ4HAAB9B3"


def test_meeting_provider_adapter_carries_recognition_facts_only():
    from core.meeting_link import meeting_provider_for_link

    zoom = meeting_provider_for_link("https://us02web.zoom.us/j/123?pwd=x")
    assert zoom is not None
    assert (zoom.key, zoom.label) == ("zoom", "Zoom")
    assert zoom.link_hostname == "us02web.zoom.us"
    assert zoom.platform_error == ""
    assert zoom.native_detection_supported is False

    webex = meeting_provider_for_link("https://band.webex.com/meet/us")
    assert webex.native_detection_supported is True

    facetime_on_windows = meeting_provider_for_link(
        "https://facetime.apple.com/join#v=1&p=a", platform="win32"
    )
    assert "Mac" in facetime_on_windows.platform_error

    generic = meeting_provider_for_link(
        "https://meet.jit.si/WebJamBand?private=1#join"
    )
    assert generic is not None
    assert (generic.key, generic.label) == (
        GENERIC_MEETING_SERVICE_KEY,
        "Meeting service",
    )
    assert generic.link_hostname == "meet.jit.si"
    assert generic.native_detection_supported is False
    assert generic.platform_error == ""

    assert meeting_provider_for_link("https://example.com/x") is None
    assert meeting_provider_for_link("") is None
