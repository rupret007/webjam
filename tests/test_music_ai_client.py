"""Music AI client behaviour, proven against a fake transport.

No test in this file opens a socket. The transport is the injection seam, and
one of the assertions below is that the client never reaches the network
without a caller having chosen a specific file.
"""

from __future__ import annotations

import json

import pytest

from core.music_ai_client import (
    API_BASE_URL,
    DOCUMENTED_STEMS_WORKFLOW,
    MusicAIAuthError,
    MusicAIClient,
    MusicAIConfigurationError,
    MusicAIJob,
    MusicAIJobError,
    MusicAIRequestError,
    MusicAIResponse,
    MusicAITransportError,
    UrllibMusicAITransport,
    guess_content_type,
    missing_key_message,
    validate_music_ai_url,
)

API_KEY = "test-key-abc123"


class FakeTransport:
    """Records every request and replays scripted responses."""

    def __init__(self, routes: dict[tuple[str, str], object] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[dict[str, object]] = []

    def request(self, method, url, *, headers, body=None, timeout=30.0):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        key = (method.upper(), url)
        response = self.routes.get(key)
        if response is None:
            for (route_method, route_url), candidate in self.routes.items():
                if route_method == method.upper() and url.startswith(route_url):
                    response = candidate
                    break
        if response is None:
            return MusicAIResponse(status=404, body=b"{}")
        if callable(response):
            response = response(len(self.calls))
        return response


def _json_response(payload, status=200) -> MusicAIResponse:
    return MusicAIResponse(status=status, body=json.dumps(payload).encode())


def _client(transport: FakeTransport, **kwargs) -> MusicAIClient:
    kwargs.setdefault("sleep", lambda _seconds: None)
    return MusicAIClient(API_KEY, transport=transport, **kwargs)


# ----------------------------------------------------------------------
# Credentials
# ----------------------------------------------------------------------
@pytest.mark.parametrize("value", ["", "   ", None])
def test_client_refuses_to_exist_without_a_key(value):
    """Fail closed at construction rather than at an unpredictable later call."""

    with pytest.raises(MusicAIConfigurationError):
        MusicAIClient(value, transport=FakeTransport())


def test_missing_key_copy_names_the_console_and_rejects_moises_logins():
    message = missing_key_message()
    assert "music.ai/dash" in message
    assert "Moises" in message
    assert "password" not in message.lower()


def test_authorization_header_is_the_bare_key_with_no_bearer_prefix():
    """The docs specify a verbatim key; a Bearer prefix is the usual 401."""

    transport = FakeTransport(
        {("GET", f"{API_BASE_URL}/application"): _json_response(
            {"id": "app-1", "name": "WebJam"}
        )}
    )
    application = _client(transport).application()

    assert application.name == "WebJam"
    assert transport.calls[0]["headers"]["Authorization"] == API_KEY
    assert not str(transport.calls[0]["headers"]["Authorization"]).startswith("Bearer")


def test_rejected_key_reports_how_to_fix_it():
    transport = FakeTransport(
        {("GET", f"{API_BASE_URL}/application"): MusicAIResponse(401, b"{}")}
    )
    with pytest.raises(MusicAIAuthError) as excinfo:
        _client(transport).application()
    assert "music.ai/dash" in str(excinfo.value)


# ----------------------------------------------------------------------
# Workflow discovery
# ----------------------------------------------------------------------
def test_workflow_discovery_follows_pagination_and_drops_duplicates():
    page_one = {
        "workflows": [
            {"id": str(index), "name": f"W{index}", "slug": f"slug-{index}"}
            for index in range(100)
        ]
    }
    page_two = {
        "workflows": [
            {"id": "100", "name": "Last", "slug": "slug-99"},
            {"id": "101", "name": "New", "slug": "slug-100"},
        ]
    }
    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/workflow?page=0&size=100"): _json_response(
                page_one
            ),
            ("GET", f"{API_BASE_URL}/workflow?page=1&size=100"): _json_response(
                page_two
            ),
        }
    )

    workflows = _client(transport).list_workflows()

    assert len(workflows) == 101
    assert len({item.slug for item in workflows}) == 101
    assert workflows[-1].slug == "slug-100"


def test_workflow_discovery_stops_on_an_empty_page():
    transport = FakeTransport(
        {("GET", f"{API_BASE_URL}/workflow"): _json_response({"workflows": []})}
    )
    assert _client(transport).list_workflows() == ()
    assert len(transport.calls) == 1


def test_account_specific_slugs_are_returned_verbatim():
    """The docs' own example slug is opaque, so nothing may be normalised."""

    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/workflow"): _json_response(
                {
                    "workflows": [
                        {
                            "id": "2362a51f",
                            "name": "Extract Beat map and BPM",
                            "slug": "untitled-workflow-e78c2e",
                            "description": "Transcribe song BPM and beats.",
                        }
                    ]
                }
            )
        }
    )
    workflow = _client(transport).list_workflows()[0]
    assert workflow.slug == "untitled-workflow-e78c2e"


# ----------------------------------------------------------------------
# Upload
# ----------------------------------------------------------------------
def test_upload_puts_bytes_to_the_signed_url_without_leaking_the_api_key(tmp_path):
    """The signed URL authorises itself; sending the key there leaks it."""

    source = tmp_path / "take.wav"
    source.write_bytes(b"RIFF" + b"0" * 2048)
    upload_url = "https://storage.googleapis.com/upload/abc"
    download_url = "https://storage.googleapis.com/download/abc"
    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/upload"): _json_response(
                {"uploadUrl": upload_url, "downloadUrl": download_url}
            ),
            ("PUT", upload_url): MusicAIResponse(200, b""),
        }
    )
    client = _client(transport)

    returned = client.upload_file(source, client.prepare_upload())

    assert returned == download_url
    put = transport.calls[1]
    assert put["method"] == "PUT"
    assert put["body"] == source.read_bytes()
    assert "Authorization" not in put["headers"]
    assert put["headers"]["Content-Type"] == "audio/x-wav"


def test_nothing_is_uploaded_without_a_caller_chosen_file(tmp_path):
    """There is no discovery path: a missing or empty file simply refuses."""

    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/upload"): _json_response(
                {
                    "uploadUrl": "https://storage.googleapis.com/upload/a",
                    "downloadUrl": "https://storage.googleapis.com/download/a",
                }
            )
        }
    )
    client = _client(transport)
    target = client.prepare_upload()
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")

    with pytest.raises(MusicAIRequestError):
        client.upload_file(tmp_path / "missing.wav", target)
    with pytest.raises(MusicAIRequestError):
        client.upload_file(empty, target)

    assert [call["method"] for call in transport.calls] == ["GET"]


def test_upload_target_outside_the_published_hosts_is_refused():
    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/upload"): _json_response(
                {
                    "uploadUrl": "https://attacker.example.com/upload",
                    "downloadUrl": "https://storage.googleapis.com/download/a",
                }
            )
        }
    )
    with pytest.raises(MusicAIRequestError):
        _client(transport).prepare_upload()


@pytest.mark.parametrize(
    "url",
    [
        "http://api.music.ai/v1/job",          # downgraded scheme
        "https://api.music.ai:8443/v1/job",    # unexpected port
        "https://user:pw@api.music.ai/v1/job",  # embedded credentials
        "https://api.music.ai.evil.test/v1",   # lookalike host
        "https://cdn.music.ai.evil.test/x",    # lookalike CDN
        "",
    ],
)
def test_url_policy_rejects_unsafe_endpoints(url):
    with pytest.raises(MusicAIRequestError):
        validate_music_ai_url(url)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("song.mp3", "audio/mpeg"),
        ("song.flac", "audio/flac"),
        ("song.bin", "application/octet-stream"),
        ("song", "application/octet-stream"),
    ],
)
def test_content_type_guess_only_claims_media_types(name, expected):
    assert guess_content_type(name) == expected


@pytest.mark.parametrize("platform_guess", ["audio/flac", "audio/x-flac"])
def test_flac_upload_type_is_canonical_across_platform_mime_tables(
    monkeypatch, platform_guess
):
    """The signed upload contract must not change between Linux and macOS."""

    monkeypatch.setattr(
        "core.music_ai_client.mimetypes.guess_type",
        lambda _path: (platform_guess, None),
    )

    assert guess_content_type("song.FLAC") == "audio/flac"


# ----------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------
def test_job_polls_until_it_succeeds_and_returns_the_result():
    job_url = f"{API_BASE_URL}/job/job-1"
    statuses = ["QUEUED", "STARTED", "SUCCEEDED"]

    def respond(call_index: int) -> MusicAIResponse:
        status = statuses[min(call_index - 1, len(statuses) - 1)]
        return _json_response(
            {
                "id": "job-1",
                "status": status,
                "result": (
                    {"vocals": "https://cdn.music.ai/a/vocals.wav"}
                    if status == "SUCCEEDED"
                    else None
                ),
            }
        )

    transport = FakeTransport({("GET", job_url): respond})
    job = _client(transport).wait_for_job("job-1", poll_interval=0.01)

    assert job.succeeded
    assert job.result["vocals"].endswith("vocals.wav")
    assert len(transport.calls) == 3


def test_failed_job_raises_with_the_api_error_text_and_code():
    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/job/job-2"): _json_response(
                {
                    "id": "job-2",
                    "status": "FAILED",
                    "error": {
                        "code": "BAD_INPUT",
                        "title": "Invalid input",
                        "message": "File not found.",
                    },
                }
            )
        }
    )
    with pytest.raises(MusicAIJobError) as excinfo:
        _client(transport).wait_for_job("job-2", poll_interval=0.01)

    assert excinfo.value.code == "BAD_INPUT"
    assert "Invalid input" in str(excinfo.value)
    assert "File not found." in str(excinfo.value)


def test_a_job_that_never_finishes_times_out_without_claiming_failure():
    clock = {"value": 0.0}

    def advance(_seconds):
        clock["value"] += 60.0

    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/job/job-3"): _json_response(
                {"id": "job-3", "status": "STARTED"}
            )
        }
    )
    client = _client(
        transport, sleep=advance, monotonic=lambda: clock["value"]
    )

    with pytest.raises(MusicAIJobError) as excinfo:
        client.wait_for_job("job-3", poll_interval=1.0, timeout=120.0)

    assert excinfo.value.code == "TIMEOUT"
    assert "not cancelled" in str(excinfo.value)


def test_cancellation_stops_polling_before_the_first_request():
    transport = FakeTransport()
    with pytest.raises(MusicAIJobError) as excinfo:
        _client(transport).wait_for_job("job-4", should_cancel=lambda: True)

    assert excinfo.value.code == "CANCELLED"
    assert transport.calls == []


def test_create_job_sends_the_slug_and_input_url():
    transport = FakeTransport(
        {("POST", f"{API_BASE_URL}/job"): _json_response({"id": "job-5"})}
    )
    job_id = _client(transport).create_job(
        name="WebJam",
        workflow=DOCUMENTED_STEMS_WORKFLOW,
        params={"inputUrl": "https://storage.googleapis.com/download/a"},
        metadata={"session": "tuesday"},
    )

    assert job_id == "job-5"
    body = json.loads(transport.calls[0]["body"])
    assert body["workflow"] == DOCUMENTED_STEMS_WORKFLOW
    assert body["params"]["inputUrl"].startswith("https://storage.googleapis.com/")
    assert body["metadata"] == {"session": "tuesday"}


def test_create_job_requires_a_workflow_slug():
    transport = FakeTransport()
    with pytest.raises(MusicAIRequestError):
        _client(transport).create_job(name="x", workflow="  ", params={})
    assert transport.calls == []


@pytest.mark.parametrize("job_id", ["", "../secrets", "a b", "job/1", "job?x=1"])
def test_job_ids_are_validated_before_reaching_a_url(job_id):
    transport = FakeTransport()
    with pytest.raises(MusicAIRequestError):
        _client(transport).get_job(job_id)
    assert transport.calls == []


def test_run_file_workflow_uploads_then_creates_then_polls(tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"1" * 1024)
    upload_url = "https://storage.googleapis.com/upload/z"
    download_url = "https://storage.googleapis.com/download/z"
    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/upload"): _json_response(
                {"uploadUrl": upload_url, "downloadUrl": download_url}
            ),
            ("PUT", upload_url): MusicAIResponse(200, b""),
            ("POST", f"{API_BASE_URL}/job"): _json_response({"id": "job-6"}),
            ("GET", f"{API_BASE_URL}/job/job-6"): _json_response(
                {
                    "id": "job-6",
                    "status": "SUCCEEDED",
                    "result": {"vocals": "https://cdn.music.ai/z/vocals.wav"},
                }
            ),
        }
    )

    job = _client(transport).run_file_workflow(
        source, workflow=DOCUMENTED_STEMS_WORKFLOW, poll_interval=0.01
    )

    assert job.succeeded
    assert [call["method"] for call in transport.calls] == [
        "GET",
        "PUT",
        "POST",
        "GET",
    ]


def test_job_status_endpoint_returns_only_the_status():
    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/job/job-7/status"): _json_response(
                {"id": "job-7", "status": "QUEUED"}
            )
        }
    )
    assert _client(transport).job_status("job-7") == "QUEUED"


def test_delete_job_uses_the_delete_verb():
    transport = FakeTransport(
        {("DELETE", f"{API_BASE_URL}/job/job-8"): _json_response({"id": "job-8"})}
    )
    _client(transport).delete_job("job-8")
    assert transport.calls[0]["method"] == "DELETE"


def test_a_missing_workflow_reports_that_the_account_list_may_have_changed():
    transport = FakeTransport(
        {("POST", f"{API_BASE_URL}/job"): MusicAIResponse(404, b"{}")}
    )
    with pytest.raises(MusicAIRequestError) as excinfo:
        _client(transport).create_job(name="x", workflow="gone", params={})
    assert "workflow list" in str(excinfo.value)


def test_unreadable_response_body_is_reported_rather_than_guessed():
    transport = FakeTransport(
        {("GET", f"{API_BASE_URL}/application"): MusicAIResponse(200, b"<html>")}
    )
    with pytest.raises(MusicAIRequestError):
        _client(transport).application()


def test_job_failure_text_falls_back_when_the_api_sends_no_detail():
    assert "failed job" in MusicAIJob(id="x", status="FAILED").failure_text()


# ----------------------------------------------------------------------
# Real transport, without a socket
# ----------------------------------------------------------------------
def test_real_transport_validates_the_url_before_opening_anything(monkeypatch):
    transport = UrllibMusicAITransport()

    def explode(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("the transport opened a connection")

    monkeypatch.setattr(transport, "_secure_opener", explode)

    with pytest.raises(MusicAIRequestError):
        transport.request(
            "GET", "http://api.music.ai/v1/job", headers={}, timeout=1.0
        )


def test_real_transport_reports_missing_tls_trust_without_leaking_the_url(
    monkeypatch,
):
    transport = UrllibMusicAITransport()
    monkeypatch.setattr(
        "core.music_ai_client.ssl.create_default_context",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("no trust")),
    )

    with pytest.raises(MusicAITransportError) as excinfo:
        transport.request(
            "GET", f"{API_BASE_URL}/application", headers={}, timeout=1.0
        )

    assert "api.music.ai" not in str(excinfo.value)
