from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Callable

from core.creative_modes import CreativeMode


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
    ):
        super().__init__(parent, bg="#2b2b2b", relief=tk.RAISED, borderwidth=1)
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
        title = tk.Label(self, text="Shared Session Canvas", bg="#2b2b2b", fg="white", font=("Arial", 13, "bold"))
        title.pack(anchor="w", padx=10, pady=(10, 6))

        self.mode_help = tk.Label(self, text="", bg="#2b2b2b", fg="#d0d0d0", justify=tk.LEFT, wraplength=390)
        self.mode_help.pack(anchor="w", padx=10)

        state_row = tk.Frame(self, bg="#2b2b2b")
        state_row.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(state_row, text="Review State:", bg="#2b2b2b", fg="white").pack(side=tk.LEFT)
        self.review_state_var = tk.StringVar(value="draft")
        state_menu = tk.OptionMenu(state_row, self.review_state_var, "draft", "review", "final", command=self._on_state_change)
        state_menu.configure(width=10)
        state_menu.pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(self, text="Pinned Artifacts & References", bg="#2b2b2b", fg="white", font=("Arial", 10, "bold")).pack(
            anchor="w", padx=10, pady=(8, 4)
        )
        self.artifacts_list = tk.Listbox(self, height=8, font=("Arial", 10))
        self.artifacts_list.pack(fill=tk.X, padx=10)

        btn_row = tk.Frame(self, bg="#2b2b2b")
        btn_row.pack(fill=tk.X, padx=10, pady=(5, 8))
        tk.Button(btn_row, text="Add", command=self._add_artifact, padx=10).pack(side=tk.LEFT)
        tk.Button(btn_row, text="Remove", command=self._remove_selected, padx=10).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(btn_row, text="Refresh", command=self.refresh, padx=10).pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(self, text="Live Notes", bg="#2b2b2b", fg="white", font=("Arial", 10, "bold")).pack(
            anchor="w", padx=10, pady=(2, 4)
        )
        self.notes = tk.Text(self, height=12, wrap=tk.WORD, font=("Arial", 10))
        self.notes.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
        self.notes.bind("<FocusOut>", self._save_notes)

        tk.Button(self, text="Save Notes", command=self._save_notes).pack(anchor="e", padx=10, pady=(0, 8))

        tk.Label(self, text="Critique Prompts", bg="#2b2b2b", fg="white", font=("Arial", 10, "bold")).pack(
            anchor="w", padx=10, pady=(0, 4)
        )
        self.prompts = tk.Label(self, text="", bg="#2b2b2b", fg="#d0d0d0", justify=tk.LEFT, wraplength=360)
        self.prompts.pack(anchor="w", padx=10, pady=(0, 10))

    def _on_state_change(self, selected: str) -> None:
        self.on_review_state_change(selected)

    def _add_artifact(self) -> None:
        title = simpledialog.askstring("Artifact Title", "Name this artifact:", parent=self)
        if not title:
            return
        artifact_type = simpledialog.askstring(
            "Artifact Type",
            "Type (image, link, note, doc, board):",
            parent=self,
            initialvalue="link",
        )
        if not artifact_type:
            artifact_type = "link"
        reference = simpledialog.askstring("Artifact Reference", "Paste URL/path/description:", parent=self)
        if not reference:
            return
        self.add_artifact_cb(title.strip(), artifact_type.strip(), reference.strip())
        self.refresh()

    def _remove_selected(self) -> None:
        selection = self.artifacts_list.curselection()
        if not selection:
            return
        idx = int(selection[0])
        artifact_id = self.artifact_index.get(idx)
        if artifact_id is None:
            return
        self.remove_artifact_cb(artifact_id)
        self.refresh()

    def _save_notes(self, _event=None) -> None:
        self.save_notes_cb(self.notes.get("1.0", tk.END).rstrip())

    def refresh(self) -> None:
        mode = self.get_mode()
        context = self.get_room_context()
        self.mode_help.configure(text=f"Mode help: {mode.quick_help}")
        self.review_state_var.set(context.get("review_state", "draft"))

        artifacts = self.list_artifacts_cb()
        self.artifact_index = {}
        self.artifacts_list.delete(0, tk.END)
        for idx, artifact in enumerate(artifacts):
            self.artifact_index[idx] = int(artifact["id"])
            line = f"[{artifact['artifact_type']}] {artifact['title']} - {artifact['reference']}"
            self.artifacts_list.insert(tk.END, line)

        self.notes.delete("1.0", tk.END)
        self.notes.insert("1.0", self.load_notes_cb())

        prompts = "\n".join(f"- {prompt}" for prompt in mode.review_prompts)
        self.prompts.configure(text=prompts)

    def confirm_reset(self) -> bool:
        return messagebox.askokcancel("Reset Canvas", "Clear notes and artifacts for this room?", parent=self)

