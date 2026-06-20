#!/usr/bin/env python3
"""gui_edit.py -- Tkinter GUI for the easy video editor."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils import (
    EDIT_SCRIPT, OUTPUT_DIR, build_output_path, choose_output_suffix,
    fmt_file_size, probe_video,
)

OPERATIONS = [
    {
        "name": "Trim (keep a section)",
        "key": "trim",
        "flag": "--trim",
        "fields": [
            {"label": "Start time:", "placeholder": "e.g. 0:30"},
            {"label": "End time:", "placeholder": "e.g. 1:45"},
        ],
    },
    {
        "name": "Cut (remove a section)",
        "key": "cut",
        "flag": "--cut",
        "fields": [
            {"label": "Start time:", "placeholder": "e.g. 0:30"},
            {"label": "End time:", "placeholder": "e.g. 1:45"},
        ],
    },
    {
        "name": "Resize",
        "key": "resize",
        "flag": "--resize",
        "fields": [
            {"label": "Size (WxH):", "placeholder": "e.g. 1280x720"},
        ],
    },
    {
        "name": "Rotate",
        "key": "rotate",
        "flag": "--rotate",
        "fields": [
            {"label": "Angle:", "type": "choice", "choices": ["90", "180", "270"]},
        ],
    },
    {
        "name": "Speed",
        "key": "speed",
        "flag": "--speed",
        "fields": [
            {"label": "Speed factor:", "placeholder": "e.g. 1.5"},
        ],
    },
    {
        "name": "FPS (change frame rate)",
        "key": "fps",
        "flag": "--fps",
        "fields": [
            {"label": "FPS:", "placeholder": "e.g. 30"},
        ],
    },
    {"name": "Normalize audio", "key": "normalize-audio", "flag": "--normalize-audio", "fields": []},
    {"name": "Sharpen", "key": "sharpen", "flag": "--sharpen", "fields": []},
    {"name": "Denoise", "key": "denoise", "flag": "--denoise", "fields": []},
    {
        "name": "Watermark (text)",
        "key": "watermark-text",
        "flag": "--watermark-text",
        "fields": [
            {"label": "Watermark text:", "placeholder": "e.g. My Video"},
        ],
    },
    {
        "name": "Watermark (image/logo)",
        "key": "watermark-image",
        "flag": "--watermark-image",
        "fields": [
            {"label": "Image path:", "type": "file"},
        ],
    },
]

WIRED_OPS = {"trim"}

OP_NAMES = [op["name"] for op in OPERATIONS]

VIDEO_EXTENSIONS = [
    ("Video files", "*.mp4 *.mov *.mkv *.webm"),
    ("All files", "*.*"),
]

IMAGE_EXTENSIONS = [
    ("Image files", "*.png *.jpg *.jpeg *.bmp *.gif"),
    ("All files", "*.*"),
]

_ERROR_HINTS = ("error", "invalid", "not supported", "only ", "could not",
                "no such", "permission", "unable", "cannot", "failed")
_STATUS_PREFIXES = ("working dir:", "input:")


def _parse_edit_error(stderr: str) -> str:
    edit_msgs: list[str] = []
    detail_lines: list[str] = []
    for ln in stderr.splitlines():
        if not ln.strip():
            continue
        if "[edit]" in ln:
            msg = ln.split("[edit]", 1)[-1].strip()
            if not msg.lower().startswith(_STATUS_PREFIXES):
                edit_msgs.append(msg)
        else:
            detail_lines.append(ln.strip())

    parts: list[str] = []
    if edit_msgs:
        parts.append(edit_msgs[-1])
    reason = ""
    for ln in detail_lines:
        if any(k in ln.lower() for k in _ERROR_HINTS):
            reason = ln
    if not reason and detail_lines:
        reason = detail_lines[-1]
    if reason and reason not in " ".join(parts):
        parts.append(reason)
    return " ".join(parts) if parts else "unknown error"


class EasyEditorGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Easy Video Editor")
        self.root.geometry("520x480")
        self.root.resizable(False, False)

        self.video_path: Path | None = None
        self.field_widgets: list[tk.Entry | tk.StringVar] = []
        self.field_frame: tk.Frame | None = None
        self._running = False
        self._proc: subprocess.Popen | None = None

        self._build_ui()
        self._update_fields()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    self._proc.kill()
        self.root.destroy()

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
        op_frame.pack(fill="x", padx=24, pady=(0, 8))

        tk.Label(op_frame, text="Operation:").pack(side="left")

        self.op_var = tk.StringVar(value=OP_NAMES[0])
        self.op_var.trace_add("write", lambda *_: self._update_fields())
        self.op_menu = tk.OptionMenu(op_frame, self.op_var, *OP_NAMES)
        self.op_menu.config(width=28)
        self.op_menu.pack(side="left", padx=(8, 0))

        # --- Dynamic fields container ---
        self.field_frame = tk.Frame(self.root)
        self.field_frame.pack(fill="x", padx=24, pady=(0, 8))

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

    def _get_current_op(self) -> dict:
        name = self.op_var.get()
        for op in OPERATIONS:
            if op["name"] == name:
                return op
        return OPERATIONS[0]

    def _update_fields(self) -> None:
        for w in self.field_frame.winfo_children():
            w.destroy()
        self.field_widgets.clear()

        op = self._get_current_op()
        fields = op["fields"]

        if not fields:
            tk.Label(
                self.field_frame, text="No extra settings needed.", fg="#888888",
            ).pack(anchor="w", pady=(4, 0))
            return

        for field in fields:
            row = tk.Frame(self.field_frame)
            row.pack(fill="x", pady=(4, 0))

            tk.Label(row, text=field["label"], width=14, anchor="w").pack(side="left")

            field_type = field.get("type", "text")

            if field_type == "choice":
                var = tk.StringVar(value=field["choices"][0])
                menu = tk.OptionMenu(row, var, *field["choices"])
                menu.config(width=12)
                menu.pack(side="left", padx=(4, 0))
                self.field_widgets.append(var)

            elif field_type == "file":
                var = tk.StringVar()
                entry = tk.Entry(row, textvariable=var, width=28)
                entry.pack(side="left", padx=(4, 0))
                tk.Button(
                    row, text="Browse",
                    command=lambda v=var: self._browse_image(v),
                ).pack(side="left", padx=(4, 0))
                self.field_widgets.append(var)

            else:
                entry = tk.Entry(row, width=20)
                placeholder = field.get("placeholder", "")
                if placeholder:
                    entry.insert(0, placeholder)
                    entry.config(fg="#aaaaaa")
                    entry.bind("<FocusIn>", lambda e, en=entry, ph=placeholder: self._clear_placeholder(en, ph))
                    entry.bind("<FocusOut>", lambda e, en=entry, ph=placeholder: self._restore_placeholder(en, ph))
                entry.pack(side="left", padx=(4, 0))
                self.field_widgets.append(entry)

    def _clear_placeholder(self, entry: tk.Entry, placeholder: str) -> None:
        if entry.get() == placeholder:
            entry.delete(0, "end")
            entry.config(fg="#000000")

    def _restore_placeholder(self, entry: tk.Entry, placeholder: str) -> None:
        if not entry.get():
            entry.insert(0, placeholder)
            entry.config(fg="#aaaaaa")

    def _browse_image(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Select an image file",
            filetypes=IMAGE_EXTENSIONS,
        )
        if path:
            var.set(path)

    def _get_field_values(self) -> list[str]:
        values = []
        op = self._get_current_op()
        for i, field in enumerate(op["fields"]):
            widget = self.field_widgets[i]
            if isinstance(widget, tk.StringVar):
                values.append(widget.get())
            elif isinstance(widget, tk.Entry):
                text = widget.get()
                placeholder = field.get("placeholder", "")
                if text == placeholder:
                    text = ""
                values.append(text)
        return values

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
            self.info_label.config(text=info, fg="#336699")
        else:
            self.info_label.config(text="Video selected. Info unavailable.", fg="#999999")

        self.run_btn.config(state="normal")
        self.status_label.config(text="")

    def _on_run(self) -> None:
        if self._running or not self.video_path:
            return

        op = self._get_current_op()

        if op["key"] not in WIRED_OPS:
            messagebox.showinfo(
                "Not yet available",
                f"{op['name']} will be added in a later PR.",
            )
            return

        values = self._get_field_values()
        for v in values:
            if not v.strip():
                messagebox.showwarning("Missing input", "Please fill in all fields.")
                return

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        suffix = choose_output_suffix(self.video_path)
        output = build_output_path(self.video_path.stem, op["key"], suffix)

        cmd = [sys.executable, str(EDIT_SCRIPT), str(self.video_path), op["flag"]]
        cmd.extend(values)
        cmd.extend(["--output", str(output)])

        output_existed = output.exists()

        self._running = True
        self.run_btn.config(state="disabled")
        self.status_label.config(text="Running edit...", fg="#555555")

        thread = threading.Thread(target=self._run_edit, args=(cmd, output, output_existed))
        thread.start()

    def _run_edit(self, cmd: list[str], output: Path, output_existed: bool) -> None:
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True,
                                          start_new_session=True)
            stdout, stderr = self._proc.communicate()
            returncode = self._proc.returncode
            self._proc = None

            if returncode == 0 and output.exists() and output.stat().st_size > 0:
                try:
                    size_text = fmt_file_size(output.stat().st_size)
                except OSError:
                    size_text = ""
                msg = f"Done! Output saved to:\n{output}"
                if size_text:
                    msg += f"\nSize: {size_text}"
                self.root.after(0, self._show_success, msg)
            else:
                error = _parse_edit_error(stderr or "")
                if returncode != 0:
                    error = error or f"exit code {returncode}"
                else:
                    error = "the edit finished but produced no output file"
                if output.exists() and not output_existed:
                    output.unlink()
                self.root.after(0, self._show_failure, error)
        except Exception as exc:
            self._proc = None
            self.root.after(0, self._show_failure, str(exc))

    def _show_success(self, msg: str) -> None:
        self._running = False
        self.run_btn.config(state="normal")
        self.status_label.config(text=msg, fg="#228B22")

    def _show_failure(self, msg: str) -> None:
        self._running = False
        self.run_btn.config(state="normal")
        self.status_label.config(text=f"Something went wrong:\n{msg}", fg="#CC0000")


def main() -> None:
    root = tk.Tk()
    EasyEditorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
