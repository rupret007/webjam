from __future__ import annotations

from core.webex_url import (
    is_allowed_webex_url,
    normalize_webex_url,
    webex_site_hostname,
    webex_url_error,
)


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


def test_rejects_ascii_control_characters_without_normalizing_them_away():
    for url in (
        "\nhttps://org.webex.com/meet/band",
        "https://org.webex.com/meet/band\r",
        "https://org.webex.com/meet/\tband",
        "https://org.webex.com/meet/\x7fband",
    ):
        assert webex_url_error(url) == (
            "Webex links must not include control characters"
        )
        assert not is_allowed_webex_url(url)


def test_rejects_percent_encoded_hostname_labels_but_allows_encoded_path():
    assert webex_url_error("https://%6frg.webex.com/meet/band") == (
        "Webex link domains must not use percent encoding"
    )
    assert not is_allowed_webex_url("https://org%2ewebex.com/meet/band")
    assert is_allowed_webex_url("https://org.webex.com/meet/band%20practice")


def test_site_hostname_exposes_only_valid_origin():
    assert (
        webex_site_hostname(
            "https://Team.Webex.com/meet/private-room?token=private#lobby"
        )
        == "team.webex.com"
    )
    assert webex_site_hostname("https://example.com/meet/private-room") == ""
    assert webex_site_hostname("https://%74eam.webex.com/meet/private-room") == ""
    assert webex_site_hostname("https://team.webex.com/meet/\nprivate-room") == ""
    assert webex_site_hostname("not-a-link") == ""
