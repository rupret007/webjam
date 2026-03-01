from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Callable

from core.creative_modes import CreativeMode
from ui.theme import DEFAULT_THEME

ALLOWED_ARTIFACT_TYPES = {"image", "link", "note", "doc", "board"}
NOTES_SOFT_LIMIT = 10_000


class SessionCanvasPanel(tk.Frame):
    """Reusable collaborative panel for references, artifacts, and notes."""

    def __init__(
        self,
        parent: tk.Misc,
        get_mode: Callable[[], CreativeMode],
        get_room_context: Callable[[], dict[str, str]],
        on_review_state_change: Callable[[str], None],
        list_artifacts: Callable[[], list[dict[str, str]]],
        add_artifact: Callable[[str, str, str], None],
        remove_artifact: Callable[[int], None],
        load_notes: Callable[[], str],
        save_notes: Callable[[str], None],
        bg_color: str | None = None,
        fg_color: str | None = None,
        muted_fg: str | None = None,
    ):
        self._bg = bg_color or DEFAULT_THEME.bg_secondary
        self._fg = fg_color or DEFAULT_THEME.text_primary
        self._muted_fg = muted_fg or "#d0d0d0"
        super().__init__(parent, bg=self._bg, relief=tk.RAISED, borderwidth=1)
        self.get_mode = get_mode
        self.get_room_context = get_room_context
        self.on_review_state_change = on_review_state_change
        self.list_artifacts_cb = list_artifacts
        self.add_artifact_cb = add_artifact
        self.remove_artifact_cb = remove_artifact
        self.load_notes_cb = load_notes
        self.save_notes_cb = save_notes
        self.artifact_index: dict[int, int] = {}

        self._build()
        self.refresh()

    def _build(self) -> None:
        bg, fg, mfg = self._bg, self._fg, self._muted_fg
        title = tk.Label(self, text="Shared Session Canvas", bg=bg, fg=fg, font=("Arial", 13, "bold"))
        title.pack(anchor="w", padx=10, pady=(10, 6))

        self.mode_help = tk.Label(self, text="", bg=bg, fg=mfg, justify=tk.LEFT, wraplength=390)
        self.mode_help.pack(anchor="w", padx=10)

        state_row = tk.Frame(self, bg=bg)
        state_row.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(state_row, text="Review State:", bg=bg, fg=fg).pack(side=tk.LEFT)
        self.review_state_var = tk.StringVar(value="draft")
        state_menu = tk.OptionMenu(state_row, self.review_state_var, "draft", "review", "final", command=self._on_state_change)
        state_menu.configure(width=10)
        state_menu.pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(self, text="Pinned Artifacts & References", bg=bg, fg=fg, font=("Arial", 10, "bold")).pack(
            anchor="w", padx=10, pady=(8, 4)
        )
        self.artifacts_list = tk.Listbox(self, height=8, font=("Arial", 10))
        self.artifacts_list.pack(fill=tk.X, padx=10)

        btn_row = tk.Frame(self, bg=bg)
        btn_row.pack(fill=tk.X, padx=10, pady=(5, 8))
        tk.Button(btn_row, text="Add", command=self._add_artifact, padx=10).pack(side=tk.LEFT)
        tk.Button(btn_row, text="Remove", command=self._remove_selected, padx=10).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(btn_row, text="Refresh", command=self.refresh, padx=10).pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(self, text="Live Notes", bg=bg, fg=fg, font=("Arial", 10, "bold")).pack(
            anchor="w", padx=10, pady=(2, 4)
        )
        self.notes = tk.Text(self, height=12, wrap=tk.WORD, font=("Arial", 10))
        self.notes.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
        self.notes.bind("<FocusOut>", self._save_notes)

        tk.Button(self, text="Save Notes", command=self._save_notes).pack(anchor="e", padx=10, pady=(0, 8))

        tk.Label(self, text="Critique Prompts", bg=bg, fg=fg, font=("Arial", 10, "bold")).pack(
            anchor="w", padx=10, pady=(0, 4)
        )
        self.prompts = tk.Label(self, text="", bg=bg, fg=mfg, justify=tk.LEFT, wraplength=360)
        self.prompts.pack(anchor="w", padx=10, pady=(0, 10))

    def _on_state_change(self, selected: str) -> None:
        self.on_review_state_change(selected)

    def _add_artifact(self) -> None:
        title = simpledialog.askstring("Artifact Title", "Name this artifact:", parent=self)
        if not title:
            return
        artifact_type = simpledialog.askstring(
            "Artifact Type",
            f"Type ({', '.join(sorted(ALLOWED_ARTIFACT_TYPES))}):",
            parent=self,
            initialvalue="link",
        )
        if not artifact_type:
            artifact_type = "link"
        artifact_type = artifact_type.strip().lower()
        if artifact_type not in ALLOWED_ARTIFACT_TYPES:
            messagebox.showwarning(
                "Invalid Type",
                f"'{artifact_type}' is not a valid artifact type.\nAllowed: {', '.join(sorted(ALLOWED_ARTIFACT_TYPES))}",
                parent=self,
            )
            return
        reference = simpledialog.askstring("Artifact Reference", "Paste URL/path/description:", parent=self)
        if not reference:
            return
        try:
            self.add_artifact_cb(title.strip(), artifact_type, reference.strip())
        except Exception as exc:
            messagebox.showerror("Add Failed", f"Could not add artifact: {exc}", parent=self)
            return
        self.refresh()

    def _remove_selected(self) -> None:
        selection = self.artifacts_list.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Select an artifact to remove.", parent=self)
            return
        idx = int(selection[0])
        artifact_id = self.artifact_index.get(idx)
        if artifact_id is None:
            return
        try:
            self.remove_artifact_cb(artifact_id)
        except Exception as exc:
            messagebox.showerror("Remove Failed", f"Could not remove artifact: {exc}", parent=self)
            return
        self.refresh()

    def _save_notes(self, _event=None) -> None:
        content = self.notes.get("1.0", tk.END).rstrip()
        if len(content) > NOTES_SOFT_LIMIT:
            messagebox.showwarning(
                "Notes Length Warning",
                f"Notes exceed the recommended {NOTES_SOFT_LIMIT:,} character limit.\n"
                "They will still be saved, but consider trimming for performance.",
                parent=self,
            )
        try:
            self.save_notes_cb(content)
        except Exception as exc:
            messagebox.showerror("Save Failed", f"Could not save notes: {exc}", parent=self)

    def refresh(self) -> None:
        mode = self.get_mode()
        context = self.get_room_context()
        self.mode_help.configure(text=f"Mode help: {mode.quick_help}")
        self.review_state_var.set(context.get("review_state", "draft"))

        try:
            artifacts = self.list_artifacts_cb()
        except Exception:
            artifacts = []
        self.artifact_index = {}
        self.artifacts_list.delete(0, tk.END)
        for idx, artifact in enumerate(artifacts):
            self.artifact_index[idx] = int(artifact["id"])
            ref = artifact['reference']
            if len(ref) > 80:
                ref = ref[:77] + "..."
            line = f"[{artifact['artifact_type']}] {artifact['title']} - {ref}"
            self.artifacts_list.insert(tk.END, line)

        self.notes.delete("1.0", tk.END)
        try:
            self.notes.insert("1.0", self.load_notes_cb())
        except Exception:
            pass

        prompts = "\n".join(f"- {prompt}" for prompt in mode.review_prompts)
        self.prompts.configure(text=prompts)

    def confirm_reset(self) -> bool:
        return messagebox.askokcancel("Reset Canvas", "Clear notes and artifacts for this room?", parent=self)

