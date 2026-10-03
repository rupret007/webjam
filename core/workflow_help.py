"""Packaged, searchable workflow instructions; never operational state."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re


@dataclass(frozen=True)
class HelpTopic:
    key: str
    title: str
    body: str
    keywords: str = ""
    route: str = ""
    action: str = ""


def workflow_topics(profile: str, *, offline_studio: bool = False) -> tuple[HelpTopic, ...]:
    """Return static instructions for a profile, without inspecting user data."""
    library_route = "" if offline_studio else "library"
    topics = [
        HelpTopic("saved_work", "Find and continue saved work",
            "<p>Open <b>Session library…</b> from the launch File menu or the room's More menu. "
            "Search by a workspace's title or notes and choose its profile. Read its Summary "
            "or choose <b>Continue</b> to restore its local context.</p>"
            "<p>Finish or leave an active room before changing workspaces. Continuing saved work "
            "does not join a room, start recording, play a take, or open an Art reference.</p>"
            "<p>Standalone Music projects use their own <b>Open Project…</b> and recent-project list. "
            "A saved session workspace and a local audio project are separate.</p>",
            "recent history restart reopen notes search session workspace", library_route,
            "Open Session library"),
        HelpTopic("workspace_backup", "Back up or import a workspace",
            "<p>In draft backup builds, open <b>Session library</b> and choose <b>Back up…</b>. "
            "Resolve unsaved changes first. <b>Metadata only</b> saves notes, plans and stored links. "
            "Choose <b>Selected media</b> to add completed takes and local Art files, then "
            "review the contents, size and blockers. <b>Change choices…</b> keeps your selections.</p>"
            "<p><b>Create backup…</b> writes a new .webjambackup package and a small .webjamreceipt. "
            "Keep them together. <b>Import backup…</b> accepts metadata JSON, a package or its receipt; "
            "the receipt checks the intended package checksum. Review the preview, then confirm "
            "<b>Import as new</b>. Your current draft and selection stay here.</p>"
            "<p>With enlarged text or a compact window, scroll the Library to reach its actions. "
            "Tab and Shift+Tab reveal focused controls; save/recovery status stays visible.</p>"
            "<p>In <b>Takes</b>, select <b>Verify take</b> to check without playback, "
            "<b>Open Studio</b> to check and open Studio, or <b>Locate take…</b> "
            "to find the same original content. In <b>Art project</b>, use <b>Verify reference</b>, "
            "<b>Open reference</b> or <b>Relink…</b>. An imported Art file with no original "
            "checksum cannot be proven to match its original. Checks describe that moment only.</p>"
            "<p>Restored Studio takes keep their declared alternate sources. <b>Listen A/B</b> "
            "checks a restored comparison before playback. Leaving Studio, editing the arrangement "
            "or changing the active workspace retires a pending comparison; choose Listen again.</p>"
            "<p>Progress and <b>Cancel</b> remain available during copying and verification. Stop and End "
            "stay reachable in the main window. Close or Return to launch waits for the result; "
            "review it, then try again. A late cancellation may still report successful publication.</p>"
            "<p>Use <b>Check import</b>, then <b>Retry import</b> or "
            "<b>Choose backup…</b> and <b>Resume import</b> as offered. "
            "Never discard an uncertain publication's receipt. Imported history does not start new "
            "recording, playback or another app. Private text stays literal.</p>",
            "portable selected media package backup restore import checksum cancel recovery verify", library_route,
            "Open Session library"),
        HelpTopic("draft_recovery", "Keep a draft when saving fails",
            "<p>Read the save message in the editor where you were working. A failed save is "
            "not a saved draft. Keep that editor open and retry <b>Save</b> when storage is available.</p>"
            "<p>In Session library, <b>Save as copy…</b> preserves a separate workspace when the original "
            "has changed elsewhere. The original can still contain newer notes, take links or recaps; "
            "review its retained draft before leaving. Do not overwrite one version just to clear a warning.</p>"
            "<p>A recovered backup is identified in the library. Review it before saving. Previous "
            "profile notes are imported without changing their original files.</p>"
            "<p>Help and Session library are modeless: return to the main window to use Stop or End "
            "while keeping an unsaved draft open.</p>",
            "unsaved conflict disk full interrupted backup recovery copy retry", library_route,
            "Open Session library"),
    ]
    if profile == "music" and not offline_studio:
        topics.append(HelpTopic("rehearsal_moments", "Rehearsal songs and moment notes",
            "<p>In <b>Rehearsal plan</b>, add songs, key, tempo and goals. Previous / Next changes "
            "the active song while keeping each song's notes. Save plan… and Add saved plan… reuse "
            "the song order and goals without importing the previous session's moments.</p>"
            "<p>Type a note and choose <b>Mark moment</b>, or press <b>⌘M on Mac / Ctrl+M elsewhere</b> "
            "while the plan has focus. Only a confirmed current recording position creates a take "
            "bookmark. Otherwise the moment stays a plain note; the song clock is not recording time.</p>"
            "<p><b>Open in take</b> checks the exact linked recording before navigating, without "
            "starting playback. A missing or changed recording needs attention. Ending a rehearsal "
            "retains its recap and next steps with the workspace.</p>",
            "setlist bookmark timing timestamp plain note song recap summary", "rehearsal",
            "Open Rehearsal plan"))
    if profile == "art":
        topics.append(HelpTopic("art_relink", "Find a moved Art reference",
            "<p>In <b>Art project</b>, keep the project brief, progress and next steps alongside "
            "file and link references. Select a missing file, choose <b>Relink…</b>, and locate "
            "that reference. Relinking keeps its saved lesson bookmarks.</p>"
            "<p><b>Open reference</b> launches the selected file or link in its usual app only when "
            "you request it. Restoring the workspace or selecting a bookmark opens nothing.</p>"
            "<p>Lesson positions are entered by you, not measured video timecode. Select a bookmark, "
            "open the reference explicitly, and return to that position in your player. "
            "Export summary includes progress and next steps without local file paths.</p>"
            "<p>Keep making in your own tools or on paper. Art workspaces do not create audio takes.</p>",
            "art moved missing file relink lesson position bookmark reference progress summary",
            "" if offline_studio else "art", "Open Art project"))
    elif offline_studio:
        topics.append(HelpTopic("project_recovery", "Recover a local project or blocked bounce",
            "<p>Use <b>Open Project…</b> or the recent-project list for the whole .webjam folder. "
            "Review any offered recovery candidate before choosing to recover or discard it.</p>"
            "<p>If saving fails, keep your edits open and retry. Finish protected recording and "
            "Save As before closing or changing the project. Use <b>Relink Missing Media…</b> "
            "to validate and collect replacement media; do not edit files inside Media by hand.</p>"
            "<p>For a blocked <b>Bounce</b>, read the current Studio message and resolve the reported "
            "recording, media or save problem. Help does not certify a completed export. "
            "A successful export still needs listening and, if required, an import check in your editor.</p>",
            "project recovery autosave interrupted save draft export bounce missing media", "studio",
            "Return to Studio"))
    else:
        topics.extend((
            HelpTopic("take_review", "Review a completed take",
                "<p>Open <b>Studio</b> and select the completed take you intend to review. "
                "<b>Review / Compare</b> keeps a favorite and private notes with that recording.</p>"
                "<p>Use <b>Set A</b> and <b>Set B</b>, then explicitly choose <b>Listen A</b> or "
                "<b>Listen B</b>. Audition uses saved arrangements and does not edit the original "
                "recordings. Help navigation does not select a different take or start playback.</p>"
                "<p>If review notes cannot save, keep the draft and retry before changing takes. "
                "Review &amp; Rehearsal Preview permits playback-only review, not editing or export.</p>",
                "favorite notes compare audition A/B completed recording", "studio", "Open Studio"),
            HelpTopic("blocked_export", "Understand a blocked take export",
                "<p>Read the current take status and export message in <b>Studio</b>. Wait for "
                "recording and finalization to finish. Missing, damaged, still-transferring or "
                "unverified source media must be resolved before a valid export.</p>"
                "<p>Selected Local Originals need verified timeline alignment. If a performance "
                "track is explicitly silent, listen and review the affected track before retrying. "
                "Do not substitute an unrelated recording or assume a backup export was made.</p>"
                "<p>Review &amp; Rehearsal Preview does not permit export. Art has no take export. "
                "An unavailable action is not unlocked by reading Help. Originals remain unchanged.</p>",
                "export blocked unavailable disabled failed alignment silent transfer validation",
                "studio", "Open Studio"),
        ))
        if profile in {"music", "podcast_voice"}:
            topics.append(HelpTopic("export_receipts", "Check a verified export receipt",
                "<p>After an explicit export completes, open <b>Review / Compare → Export receipt…</b>. "
                "The receipt identifies the source recording, settings, destination and verified package files.</p>"
                "<p>A receipt becomes available only after package checksums verify. An export that "
                "fails or is canceled has no completed receipt; an older receipt is not proof of "
                "a new export. If the button is unavailable, read the current export message.</p>"
                "<p>The receipt proves package verification, not what you heard through your hardware "
                "or whether another editor imported it correctly. Listen and check that handoff yourself.</p>",
                "checksum verification receipt destination settings provenance export success", "studio",
                "Open Studio"))
    return tuple(topics)


def search_topics(topics: tuple[HelpTopic, ...], query: str) -> tuple[HelpTopic, ...]:
    """Case-insensitive AND search over packaged copy; no regex from the user."""
    words = re.findall(r"\w+", query[:256].casefold())
    matched = tuple(topic for topic in topics if all(
        word in unescape(re.sub(r"<[^>]*>", " ",
            f"{topic.title} {topic.keywords} {topic.body}")).casefold()
        for word in words
    ))
    # A named workflow should precede an overview that merely mentions it.
    return tuple(sorted(matched, key=lambda topic: sum(
        word in f"{topic.title} {topic.keywords}".casefold() for word in words
    ), reverse=True))
