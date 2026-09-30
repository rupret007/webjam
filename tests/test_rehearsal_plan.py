"""Setlist edits retain work; timestamps require actual take evidence."""
from __future__ import annotations

import json
import math

import pytest

from core.rehearsal_plan import MAX_SONGS, RehearsalPlan, make_bookmark


def test_order_selection_and_payload_keep_each_songs_drafts():
    plan = RehearsalPlan("Thursday")
    first = plan.add_song("Bridge")
    first.update(notes="Keep the lower harmony", next_steps="Try it in D", key="D", tempo=94)
    first["bookmarks"].append(make_bookmark("Good second pass"))
    second = plan.add_song("Chorus")
    assert plan.advance(-1)
    assert plan.current is first
    assert plan.move(1)
    assert [song["id"] for song in plan.songs] == [second["id"], first["id"]]
    assert plan.current["notes"] == "Keep the lower harmony"
    result = RehearsalPlan.from_payload(json.loads(json.dumps(plan.payload())))
    assert result.payload() == plan.payload()
    exported = result.payload()
    exported["songs"][1]["notes"] = "Changed outside the plan"
    assert result.current["notes"] == "Keep the lower harmony"
    assert not result.advance(1)


@pytest.mark.parametrize("evidence", [
    {}, {"position_seconds": 20.0},
    {"timing_verified": False, "take_id": "take1", "take_path": "/takes/a.wav", "source_identity": "sha256:a", "position_seconds": 20},
    {"timing_verified": True, "take_id": "take1", "take_path": "/takes/a.wav", "position_seconds": 20},
    *[{"timing_verified": True, "take_id": "take1", "take_path": "/takes/a.wav", "source_identity": "sha256:a", "position_seconds": value}
      for value in (math.nan, math.inf, -1, True, "30")],
])
def test_unverified_or_invalid_moment_is_an_untimed_note(evidence):
    moment = make_bookmark("Keep this", **evidence)
    assert moment["note"] == "Keep this"
    assert moment["position_seconds"] is None
    assert moment["take_id"] is None
    assert moment["take_path"] is None
    assert moment["source_identity"] is None


def test_verified_take_position_survives_reload_and_plain_notes_stay_untimed():
    plan = RehearsalPlan()
    song = plan.add_song("Our tune")
    song["bookmarks"] = [make_bookmark("Start again", timing_verified=True,
        take_id="take1", take_path="/takes/a.wav", source_identity="sha256:recording", position_seconds=0)]
    restored = RehearsalPlan.from_payload(plan.payload())
    assert restored.current["bookmarks"] == song["bookmarks"]
    assert restored.current["bookmarks"][0]["position_seconds"] == 0.0


def test_template_reuse_preserves_existing_work_without_copying_old_session_evidence():
    plan = RehearsalPlan("Weekly")
    first = plan.add_song("Opening")
    first.update(key="G", tempo=120, goals="Land the ending", notes="Draft", completed=True,
                 next_steps="Ask Mo", moment_draft="unfinished thought")
    first["bookmarks"] = [make_bookmark("Keep")]
    template = plan.template_payload()
    assert template["songs"][0]["notes"] == ""
    assert not template["songs"][0]["completed"]
    assert not template["songs"][0]["bookmarks"]
    assert template["songs"][0]["moment_draft"] == ""
    assert plan.append_template(template) == 1
    assert plan.current is first
    assert first["notes"] == "Draft" and first["bookmarks"]
    added = plan.songs[1]
    assert added["id"] != first["id"]
    assert (added["title"], added["key"], added["tempo"], added["goals"]) == ("Opening", "G", 120, "Land the ending")


def test_invalid_or_oversized_template_is_atomic():
    plan = RehearsalPlan()
    plan.add_song("Keep")
    before = plan.payload()
    for payload in ({"version": 2}, {"songs": [None]}, {"songs": [{}] * MAX_SONGS}):
        with pytest.raises(ValueError):
            plan.append_template(payload)
        assert plan.payload() == before


def test_summary_distinguishes_human_progress_and_plain_notes():
    plan = RehearsalPlan("Evening")
    song = plan.add_song("Bridge")
    song.update(completed=True, goals="Clean entry", notes="Softer", next_steps="Send chart")
    song["bookmarks"].append(make_bookmark("Good change"))
    plan.add_song("Finale")
    text = plan.summary()
    assert "1 of 2 songs marked complete" in text
    assert "Bridge — complete" in text and "Finale — to revisit" in text
    assert "Next: Send chart" in text and "Note: Good change" in text
    assert "Take 0:00" not in text
