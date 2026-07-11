from __future__ import annotations

from core.webex_url import is_allowed_webex_url, normalize_webex_url, webex_url_error


def test_normalizes_missing_scheme_to_https():
    assert normalize_webex_url("org.webex.com/meet/band") == (
        "https://org.webex.com/meet/band"
    )


def test_allows_https_webex_hosts():
    assert is_allowed_webex_url("https://org.webex.com/meet/band")
    assert is_allowed_webex_url("https://webex.com/meet/band")


def test_rejects_non_https_and_non_webex_hosts():
    for url in (
        "http://org.webex.com/meet/band",
        "file:///tmp/webex.html",
        "javascript:alert(1)",
        "https://localhost/meet/band",
        "https://example.com/meet/band",
        "https://org.webex.com.evil.test/meet/band",
    ):
        assert webex_url_error(url), url


def test_rejects_spaces_and_dot_dot():
    assert webex_url_error("https://org webex.com/meet/band")
    assert webex_url_error("https://org..webex.com/meet/band")


def test_rejects_userinfo_and_explicit_ports():
    assert webex_url_error(
        "https://user:secret@org.webex.com/meet/band"
    ) == "Webex links must not include a username or password"
    assert webex_url_error(
        "https://org.webex.com:443/meet/band"
    ) == "Webex links must not include a custom port"


def test_malformed_port_is_reported_instead_of_raising():
    assert webex_url_error("https://org.webex.com:notaport/meet/band")
