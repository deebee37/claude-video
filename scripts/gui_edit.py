#!/usr/bin/env python3
"""gui_edit.py -- Tkinter GUI for the easy video editor."""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils import probe_video

OPERATIONS = [
    "Trim (keep a section)",
    "Cut (remove a section)",
    "Resize",
    "Rotate",
    "Speed",
    "FPS (change frame rate)",
    "Normalize audio",
    "Sharpen",
    "Denoise",
    "Watermark (text)",
    "Watermark (image/logo)",
]

VIDEO_EXTENSIONS = [
    ("Video files", "*.mp4 *.mov *.mkv *.webm"),
    ("All files", "*.*"),
]


class EasyEditorGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Easy Video Editor")
        self.root.geometry("520x420")
        self.root.resizable(False, False)

        self.video_path: Path | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        tk.Label(
            self.root, text="Easy Video Editor", font=("Helvetica", 18, "bold"),
        ).pack(pady=(18, 4))

        tk.Label(
            self.root, text="Choose a video, pick an operation, then run.",
            fg="#555555",
        ).pack(pady=(0, 12))

        # --- Video selection ---
        file_frame = tk.Frame(self.root)
        file_frame.pack(fill="x", padx=24, pady=(0, 4))

        tk.Button(
            file_frame, text="Choose Video", width=14, command=self._choose_video,
        ).pack(side="left")

        self.file_label = tk.Label(
            file_frame, text="No video selected", fg="#888888", anchor="w",
        )
        self.file_label.pack(side="left", padx=(10, 0), fill="x", expand=True)

        # --- Video info ---
        self.info_label = tk.Label(
            self.root, text="", fg="#336699", anchor="w", justify="left",
        )
        self.info_label.pack(fill="x", padx=24, pady=(0, 12))

        # --- Operation dropdown ---
        op_frame = tk.Frame(self.root)
        op_frame.pack(fill="x", padx=24, pady=(0, 12))

        tk.Label(op_frame, text="Operation:").pack(side="left")

        self.op_var = tk.StringVar(value=OPERATIONS[0])
        self.op_menu = tk.OptionMenu(op_frame, self.op_var, *OPERATIONS)
        self.op_menu.config(width=28)
        self.op_menu.pack(side="left", padx=(8, 0))

        # --- Run button ---
        self.run_btn = tk.Button(
            self.root, text="Run Edit", width=20, state="disabled",
            command=self._on_run,
        )
        self.run_btn.pack(pady=(8, 4))

        self.status_label = tk.Label(
            self.root, text="", fg="#555555", wraplength=460, justify="left",
        )
        self.status_label.pack(fill="x", padx=24, pady=(4, 0))

        # --- Footer ---
        tk.Label(
            self.root,
            text="Help: see QUICKSTART.md",
            fg="#aaaaaa", font=("Helvetica", 10),
        ).pack(side="bottom", pady=(0, 10))

    def _choose_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a video file",
            filetypes=VIDEO_EXTENSIONS,
        )
        if not path:
            return

        self.video_path = Path(path)
        self.file_label.config(text=self.video_path.name, fg="#000000")

        info = probe_video(self.video_path)
        if info:
            self.info_label.config(text=info)
        else:
            self.info_label.config(text="Video selected. Info unavailable.", fg="#999999")

        self.run_btn.config(state="normal")
        self.status_label.config(text="")

    def _on_run(self) -> None:
        messagebox.showinfo(
            "Not yet available",
            "Edit execution will be added in the next PR.",
        )


def main() -> None:
    root = tk.Tk()
    EasyEditorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
