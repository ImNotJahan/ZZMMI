"""Small Tk front end: launch the game with mods, open the Mods folder, show the log."""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox
    from tkinter.scrolledtext import ScrolledText
except ImportError as e:  # Homebrew's Python ships Tk separately
    raise SystemExit(
        f"tkinter is not available ({e}).\n"
        f"With Homebrew Python, install it with: brew install python-tk@{sys.version_info.major}.{sys.version_info.minor}"
    ) from None

from .runtime.xxmi_legacy import XxmiInPrefix
from .runtime.yaagl import YaaglError, YaaglRuntime

LAUNCH_COMMAND = [sys.executable, "-m", "zzmmi", "debug-launch"]
POLL_MS = 100


def mods_dir() -> Path:
    xxmi = XxmiInPrefix(YaaglRuntime.discover())
    if not xxmi.zzmi_dir.is_dir():
        raise YaaglError(f"ZZMI is not installed in the Yaagl prefix (missing {xxmi.zzmi_dir})")
    mods = xxmi.zzmi_dir / "Mods"
    mods.mkdir(exist_ok=True)
    return mods


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.closing = False

        root.title("ZZMMI")
        root.geometry("820x520")
        root.minsize(480, 300)

        bar = tk.Frame(root, padx=8, pady=8)
        bar.pack(side=tk.TOP, fill=tk.X)
        self.launch_btn = tk.Button(bar, text="Launch", width=12, command=self.launch)
        self.launch_btn.pack(side=tk.LEFT)
        tk.Button(bar, text="Open Mods Folder", command=self.open_mods).pack(side=tk.LEFT, padx=(8, 0))

        self.output = ScrolledText(root, wrap=tk.WORD, state=tk.DISABLED, font=("Menlo", 11))
        self.output.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def append(self, text: str) -> None:
        at_bottom = self.output.yview()[1] >= 0.999
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, text)
        self.output.configure(state=tk.DISABLED)
        if at_bottom:
            self.output.see(tk.END)

    def launch(self) -> None:
        if self.proc is not None:
            return
        self.launch_btn.configure(text="Running", state=tk.DISABLED)
        self.append("$ zzmmi debug-launch\n")
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        try:
            self.proc = subprocess.Popen(
                LAUNCH_COMMAND, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env, text=True, errors="replace", bufsize=1,
            )
        except OSError as e:
            self.append(f"Failed to start: {e}\n")
            self.launch_btn.configure(text="Launch", state=tk.NORMAL)
            return
        threading.Thread(target=self._read_output, args=(self.proc,), daemon=True).start()
        self.root.after(POLL_MS, self._poll)

    def _read_output(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.lines.put(line)
        proc.stdout.close()

    def _drain(self) -> None:
        chunk = []
        while True:
            try:
                chunk.append(self.lines.get_nowait())
            except queue.Empty:
                break
        if chunk:
            self.append("".join(chunk))

    def _poll(self) -> None:
        self._drain()
        if self.proc is not None and self.proc.poll() is not None:
            self.proc.wait()
            self._drain()
            self.append(f"[exited with code {self.proc.returncode}]\n\n")
            self.proc = None
            if self.closing:
                self.root.destroy()
                return
            self.launch_btn.configure(text="Launch", state=tk.NORMAL)
            return
        self.root.after(POLL_MS, self._poll)

    def open_mods(self) -> None:
        try:
            path = mods_dir()
        except (YaaglError, OSError) as e:
            messagebox.showerror("ZZMMI", f"Couldn't find the Mods folder:\n\n{e}")
            return
        subprocess.Popen(["open", str(path)])

    def on_close(self) -> None:
        if self.proc is None:
            self.root.destroy()
            return
        if self.closing:
            return
        if not messagebox.askyesno("ZZMMI", "The game is still running. Stop it and quit?"):
            return
        # SIGINT lets debug-launch kill the Wine session and restore the DLLs it swapped.
        self.closing = True
        self.append("Stopping the game...\n")
        self.proc.send_signal(signal.SIGINT)


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
