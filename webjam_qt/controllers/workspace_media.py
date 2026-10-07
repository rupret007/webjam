"""Explicit Library media actions bound to the originating editor snapshot."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from PySide6.QtWidgets import QFileDialog

from core.workspace_media_backup import relink_workspace_media, verify_workspace_media


def _binding(record, kind, key):
    if kind == "art":
        return [(r["id"], r["kind"], r["locator"]) for r in record.art.get("references", []) if r["id"] == key]
    return [(r.get("take_id"), r.get("take_path"), r.get("path"), r.get("source_identity")) for r in record.take_links]


class LibraryMediaActions:
    def __init__(self, owner):
        self.owner = owner
        self.take_checks = {}

    def _take_key(self, ref):
        record = self.owner.record
        return (record.id, ref.get("take_id"), ref.get("take_path"), ref.get("source_identity"))

    def take_status(self, ref):
        return self.take_checks.get(self._take_key(ref))

    def _current(self, snapshot, kind, key):
        owner = self.owner
        return bool(owner.record is not None and owner.record.id == snapshot.id
                    and owner.record.media_provenance == snapshot.media_provenance
                    and _binding(owner._edited_record(), kind, key) == _binding(snapshot, kind, key))

    def _failure(self, snapshot, kind, ref, error):
        if not self._current(snapshot, kind, ref.get("id") if kind == "art" else ref.get("take_id")):
            return
        missing = False
        cause = error
        while cause is not None:
            missing |= isinstance(cause, FileNotFoundError)
            cause = cause.__cause__
        label = "Missing — locate the original" if missing else "Not verified — missing, changed or unsupported"
        if kind == "art":
            self.owner.art.set_reference_evidence(ref["id"], ref["locator"], label)
        else:
            self.take_checks[self._take_key(ref)] = label
            self.owner._render_takes()

    def art_action(self, action, reference):
        owner = self.owner
        if owner.record is None or owner.media_operation_pending:
            return True
        if reference["kind"] != "file":
            return False
        if action == "relink" and not owner.record.import_provenance and not owner.record.media_provenance:
            # Ordinary locally-authored references retain their existing
            # explicit file-choice behavior; imported proofs cannot be rebased.
            return False
        snapshot = deepcopy(owner._edited_record())
        ref = deepcopy(reference)
        locator = None

        def choose():
            nonlocal locator
            locator, _ = QFileDialog.getOpenFileName(owner, "Locate the same Art file")
            return bool(locator)

        def work(progress, cancel):
            verified = verify_workspace_media(snapshot, "art", ref["id"], locator=locator,
                                              progress=progress, cancel_check=cancel)
            if action == "relink":
                relink_workspace_media(snapshot, verified)  # Validate the metadata mutation off Qt.
            return verified

        def complete(verified, token):
            selected = owner.art._selected_reference()
            if (not self._current(snapshot, "art", ref["id"])
                    or selected is None or selected["id"] != ref["id"]):
                owner.status.setText("The Art reference changed during verification. Choose the action again.")
                return
            label = ("Content matched when checked" if verified.matches_expected_content
                     else "File checked; no original content checksum")
            if action == "relink":
                verified.assert_current()
                owner.art.replace_reference_locator(ref["id"], ref["locator"], verified.locator)
                if not owner.save_current(_operation_token=token):
                    owner.art.set_reference_evidence(ref["id"], verified.locator, "Relink checked; changes remain unsaved")
                    return
            checked_locator = verified.locator if action == "relink" else ref["locator"]
            owner.art.set_reference_evidence(ref["id"], checked_locator, label)
            owner.status.setText(label + ". Original files are unchanged.")
            if action == "open":
                verified.assert_current()
                owner.art.open_verified_reference(ref["id"], ref["locator"], verified.locator)

        owner.workspace_flow.execute("Verify Art reference", work, complete,
                                     failed=(lambda error: self._failure(snapshot, "art", ref, error)) if action != "relink" else None,
                                     prepare=choose if action == "relink" else None)
        return True

    def take_action(self, action, reference, *, position=0.0):
        owner = self.owner
        if owner.record is None or owner.media_operation_pending:
            return
        ref = deepcopy(reference)
        if not all(ref.get(key) for key in ("take_id", "take_path", "source_identity")):
            owner.status.setText("This link has no complete take identity. Its original recording cannot be verified.")
            return
        request = None
        if action == "open":
            if not owner.save_current(force=True):
                return
            if owner._prepare_take_open is not None:
                request = owner._prepare_take_open(ref)
                if request is None:
                    return
        snapshot = deepcopy(owner._edited_record())
        # Saving may add/reorder links. Only the originally requested exact
        # reference is allowed to reach validation or activation.
        if not any(all(r.get(key) == ref.get(key) for key in ("take_id", "take_path", "source_identity"))
                   for r in snapshot.take_links):
            owner.status.setText("The requested take link changed. Select it again before opening.")
            return
        locator = None

        def choose():
            nonlocal locator
            locator = QFileDialog.getExistingDirectory(owner, "Locate the same take")
            return bool(locator)

        def work(progress, cancel):
            verified = verify_workspace_media(snapshot, "take", ref["take_id"], locator=locator,
                                              progress=progress, cancel_check=cancel)
            if action == "relink":
                relink_workspace_media(snapshot, verified)
            return verified

        def complete(verified, token):
            if not self._current(snapshot, "take", ref["take_id"]):
                owner.status.setText("The workspace links changed during verification. Choose the action again.")
                return
            target = dict(ref, take_path=verified.locator) if action == "relink" else ref
            if action == "relink":
                verified.assert_current()
                links = []
                for item in owner.record.take_links:
                    item = deepcopy(item)
                    if item.get("take_id") == ref["take_id"]:
                        item["take_path"] = verified.locator
                        if "path" in item:
                            item["path"] = verified.locator
                    links.append(item)
                owner.record = replace(owner.record, take_links=tuple(links))
                owner.rehearsal.relink_take(ref["take_id"], ref["source_identity"], verified.locator)
                owner._dirty = True
                if not owner.save_current(_operation_token=token):
                    return
            self.take_checks[self._take_key(target)] = "Content matched when checked"
            owner._render_takes()
            owner.status.setText("Take content matched when checked. Playback has not started.")
            if action == "open":
                if not owner.save_current(force=True, _operation_token=token):
                    return
                # Use the latest reconciled metadata, while the verified link
                # and dependencies still match the exact worker snapshot.
                if not self._current(snapshot, "take", ref["take_id"]):
                    owner.status.setText("Workspace links changed while saving. Open the selected take again.")
                    return
                owner.take_open_requested.emit(dict(ref, position_seconds=position, _verification=verified,
                    _workspace_snapshot=deepcopy(owner.record), _studio_request=request, _origin_dialog=owner))

        owner.workspace_flow.execute("Verify linked take", work, complete,
                                     failed=(lambda error: self._failure(snapshot, "take", ref, error)) if action != "relink" else None,
                                     prepare=choose if action == "relink" else None)
