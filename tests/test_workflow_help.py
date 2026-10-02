"""Offline instructions and explicit routes never acquire operational authority."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.creative_modes import get_creator_profile_by_key
from core.workflow_help import search_topics, workflow_topics
from webjam_qt.controllers.workflow_help import WorkflowHelpCoordinator


@pytest.mark.parametrize("profile,query,key", [
    ("music", "SAVED work", "saved_work"),
    ("music", "unsaved conflict", "draft_recovery"),
    ("music", "plain timestamp", "rehearsal_moments"),
    ("music", "A/B favorite", "take_review"),
    ("podcast_voice", "blocked alignment", "blocked_export"),
    ("music", "checksum receipt", "export_receipts"),
    ("art", "moved relink", "art_relink"),
])
def test_search_finds_specific_workflow_offline(profile, query, key):
    assert key in {topic.key for topic in search_topics(workflow_topics(profile), query)}


def test_search_is_literal_and_does_not_search_private_workspaces():
    topics = workflow_topics("music")
    assert search_topics(topics, "private-unrelated-secret") == ()
    assert search_topics(topics, "[draft]") == search_topics(topics, "draft")
    assert search_topics(topics, "") == topics
    assert all("href=" not in topic.body for topic in topics)


def test_profile_and_standalone_topics_do_not_offer_incompatible_actions():
    art = workflow_topics("art")
    assert {topic.route for topic in art} == {"library", "art"}
    assert not {"take_review", "rehearsal_moments", "export_receipts"} & {t.key for t in art}
    offline = workflow_topics("music", offline_studio=True)
    assert {topic.route for topic in offline} == {"", "studio"}
    assert "project_recovery" in {topic.key for topic in offline}
    preview = workflow_topics("review_rehearsal")
    assert "export_receipts" not in {topic.key for topic in preview}
    assert "does not permit export" in next(t.body for t in preview if t.key == "blocked_export")


@pytest.fixture
def owner():
    return SimpleNamespace(
        creator_profile=get_creator_profile_by_key("music"),
        window=SimpleNamespace(workflow_help_requested=Mock(), isVisible=Mock(return_value=True),
                               flash_message=Mock()),
        session_library=SimpleNamespace(show=Mock()),
        audio=SimpleNamespace(stopping=False, cleanup_retry_required=False, start=Mock()),
        recording=SimpleNamespace(start=Mock()), _on_rail_view_changed=Mock(),
    )


@pytest.mark.parametrize("route,profile,expected", [
    ("library", "music", {}), ("rehearsal", "music", {"tab": "plan"}),
    ("art", "art", {"tab": "plan"}),
])
def test_explicit_routes_delegate_only_to_existing_library(owner, route, profile, expected):
    owner.creator_profile = get_creator_profile_by_key(profile)
    WorkflowHelpCoordinator(owner).navigate(route)
    owner.session_library.show.assert_called_once_with(**expected)
    owner._on_rail_view_changed.assert_not_called()
    owner.audio.start.assert_not_called()
    owner.recording.start.assert_not_called()


@pytest.mark.parametrize("flag", ["_shutdown", "_shutdown_in_progress", "_shutdown_cleanup_pending",
                                  "_workspace_transition_pending", "stopping", "cleanup_retry_required"])
def test_late_navigation_is_refused_during_teardown(owner, flag):
    target = owner.audio if flag in {"stopping", "cleanup_retry_required"} else owner
    setattr(target, flag, True)
    coordinator = WorkflowHelpCoordinator(owner)
    for route in ("library", "rehearsal", "art", "studio"):
        coordinator.navigate(route)
    owner.session_library.show.assert_not_called()
    owner._on_rail_view_changed.assert_not_called()


def test_navigation_rechecks_profile_and_window_instead_of_remembering_open_help(owner):
    coordinator = WorkflowHelpCoordinator(owner)
    owner.creator_profile = get_creator_profile_by_key("art")
    coordinator.navigate("rehearsal")
    coordinator.navigate("studio")
    owner.creator_profile = get_creator_profile_by_key("music")
    owner.window.isVisible.return_value = False
    coordinator.navigate("library")
    coordinator.navigate("studio")
    owner.session_library.show.assert_not_called()
    owner._on_rail_view_changed.assert_not_called()


def test_offline_context_routes_only_to_its_existing_studio(owner):
    owner._offline_reference_studio = True
    coordinator = WorkflowHelpCoordinator(owner)
    coordinator.navigate("library")
    coordinator.navigate("studio")
    owner.session_library.show.assert_not_called()
    owner._on_rail_view_changed.assert_called_once_with("takes")
    owner.window.flash_message.assert_called_once()


@pytest.mark.parametrize("route", ["play", "export", "open_reference", "https://example.com", "start_audio"])
def test_no_action_or_external_route_can_be_dispatched(owner, route):
    WorkflowHelpCoordinator(owner).navigate(route)
    owner.session_library.show.assert_not_called()
    owner._on_rail_view_changed.assert_not_called()
    owner.audio.start.assert_not_called()
    owner.recording.start.assert_not_called()
