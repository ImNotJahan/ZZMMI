"""Small Tk front end: a grid of tiles for launching the game, opening the Mods folder, etc.

Each tile is a square image with a label underneath. A tile either does something directly
(open the Mods folder) or opens a tool window (the log). Tool windows are opened through
App.open_window, which keeps at most one window per key and raises it if it's already open.
"""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable

try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox
    from tkinter.scrolledtext import ScrolledText
except ImportError as e:  # Homebrew's Python ships Tk separately
    raise SystemExit(
        f"tkinter is not available ({e}).\n"
        f"With Homebrew Python, install it with: brew install python-tk@{sys.version_info.major}.{sys.version_info.minor}"
    ) from None

from . import updates
from .game_window import game_window_visible
from .runtime.xxmi_legacy import XxmiInPrefix
from .runtime.yaagl import YaaglError, YaaglRuntime
from .mods import toggle_all_mods, ModLibrary, Mod
from .mods import reload_mods as xxmi_reload_mods

RESOURCES = Path(__file__).resolve().parent / "resources"
ICON_PATH = RESOURCES / "icon.png"
# Tile images: square PNGs exactly TILE_PX wide. Tk can't resample smoothly: anything larger is
# shrunk with subsample(), which drops pixels and leaves jagged edges. Full-size originals live
# in art/tiles/; regenerate with `sips -Z 128 art/tiles/X.png --out zzmmi/resources/tiles/X.png`.
# A missing image falls back to a drawn placeholder.
TILE_IMAGES = RESOURCES / "tiles"
TILE_PX = 128
TILE_PAD = 12
# Shown while the game starts. Made from art/loading.webp by art/make_loading_gif.py, with a transparent
# background: white art for dark mode, black art for light mode.
LOADING_GIF_DARK = RESOURCES / "loading_dark.gif"
LOADING_GIF_LIGHT = RESOURCES / "loading_light.gif"
LOADING_FRAME_MS = 20  # the GIF's frame delay (Tk doesn't expose it)
GAME_WINDOW_POLL_SECONDS = 0.5

DEFAULT_SIZE = "720x580"

FROZEN = getattr(sys, "frozen", False)
# Where each launch's logs go. The app bundle's working directory is inside the bundle, so it
# keeps them in Application Support; from source they go to ./runs like the CLI's default.
RUNS_DIR = (Path.home() / "Library" / "Application Support" / "ZZMMI" / "runs") if FROZEN else Path.cwd() / "runs"
POLL_MS = 100
TK9 = tk.TkVersion >= 9  # Tk 9 changed how scroll events arrive (see ModlistWindow._on_wheel)


def launch_command(*extra: str) -> list[str]:
    """The `zzmmi debug-launch` command line, with `extra` arguments appended."""
    if FROZEN:
        # Inside the py2app bundle there is no usable `python -m`; the app's own executable
        # runs the CLI when given arguments (see macos/app_main.py).
        cli = [str(Path(os.environ["RESOURCEPATH"]).parent / "MacOS" / "ZZMMI")]
    else:
        cli = [sys.executable, "-m", "zzmmi"]
    return [*cli, "debug-launch", "--runs-dir", str(RUNS_DIR), *extra]


def mods_dir() -> Path:
    xxmi = XxmiInPrefix(YaaglRuntime.discover())
    if not xxmi.zzmi_dir.is_dir():
        raise YaaglError(f"ZZMI is not installed in the Yaagl prefix (missing {xxmi.zzmi_dir})")
    mods = xxmi.zzmi_dir / "Mods"
    mods.mkdir(exist_ok=True)
    return mods


def logs_dir() -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)  # nothing has been launched yet
    return RUNS_DIR


class Colors:
    """Colors that follow macOS light/dark mode under Aqua, with plain fallbacks elsewhere."""

    def __init__(self, root: tk.Misc) -> None:
        aqua = root.tk.call("tk", "windowingsystem") == "aqua"
        self.bg = "systemWindowBackgroundColor" if aqua else root.cget("bg")
        self.hover = "systemUnemphasizedSelectedContentBackgroundColor" if aqua else "#d9d9d9"
        self.text = "systemTextColor" if aqua else "black"
        self.disabled_text = "systemDisabledControlTextColor" if aqua else "gray50"
        self.accent = "systemControlAccentColor" if aqua else "#4a6fa5"


def load_tile_image(path: Path, size: int) -> tk.PhotoImage | None:
    try:
        image = tk.PhotoImage(file=str(path))
    except tk.TclError:
        return None
    factor = -(-max(image.width(), image.height()) // size)  # ceil division
    return image.subsample(factor) if factor > 1 else image


class LogBuffer:
    """Keeps everything written to the log so windows opened later can show the backlog."""

    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._listeners: list[Callable[[str], None]] = []

    def write(self, text: str) -> None:
        self._chunks.append(text)
        for listener in list(self._listeners):
            listener(text)

    def contents(self) -> str:
        return "".join(self._chunks)

    def subscribe(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[str], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)


class GameProcess:
    """Runs `zzmmi debug-launch` as a child process and streams its output on the Tk thread."""

    def __init__(self, root: tk.Misc, on_output: Callable[[str], None], on_exit: Callable[[int], None]) -> None:
        self.root = root
        self.on_output = on_output
        self.on_exit = on_exit
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str] = queue.Queue()

    @property
    def running(self) -> bool:
        return self.proc is not None

    def start(self, command: list[str]) -> None:
        """Run `command`. Raises OSError if the process can't be spawned."""
        if self.proc is not None:
            return
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, text=True, errors="replace", bufsize=1,
        )
        threading.Thread(target=self._read_output, args=(self.proc,), daemon=True).start()
        self.root.after(POLL_MS, self._poll)

    def interrupt(self) -> None:
        # SIGINT lets debug-launch kill the Wine session and restore the DLLs it swapped.
        if self.proc is not None:
            self.proc.send_signal(signal.SIGINT)

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
            self.on_output("".join(chunk))

    def _poll(self) -> None:
        self._drain()
        if self.proc is not None and self.proc.poll() is not None:
            self.proc.wait()
            self._drain()
            code = self.proc.returncode
            self.proc = None
            self.on_exit(code)
            return
        self.root.after(POLL_MS, self._poll)


class Tile(tk.Frame):
    """A clickable square image with a text label below it."""

    def __init__(
        self, master: tk.Misc, colors: Colors, label: str, image: str | None, command: Callable[[], None],
    ) -> None:
        super().__init__(
            master, bg=colors.bg, padx=TILE_PAD // 2, pady=TILE_PAD // 2, cursor="pointinghand",
            takefocus=1, highlightthickness=2, highlightbackground=colors.bg, highlightcolor=colors.accent,
        )
        self.colors = colors
        self.label = label  # the original label, for restoring after set_label()
        self.command = command
        self.enabled = True
        self.hovered = False

        self.canvas = tk.Canvas(self, width=TILE_PX, height=TILE_PX, bg=colors.bg, highlightthickness=0)
        self.canvas.pack(side=tk.TOP)
        self.photo = load_tile_image(TILE_IMAGES / image, TILE_PX) if image else None
        if self.photo is not None:
            self.canvas.create_image(TILE_PX // 2, TILE_PX // 2, image=self.photo, anchor=tk.CENTER)
        else:
            self._draw_placeholder(label)

        self.text = tk.Label(
            self, text=label, bg=colors.bg, fg=colors.text, wraplength=TILE_PX + TILE_PAD,
            justify=tk.CENTER,
        )
        self.text.pack(side=tk.TOP, pady=(6, 0))

        for widget in (self, self.canvas, self.text):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", self._on_key)
        self.bind("<space>", self._on_key)

    def _draw_placeholder(self, label: str) -> None:
        inset, r = 8, 22
        x0, y0, x1, y1 = inset, inset, TILE_PX - inset, TILE_PX - inset
        points = [
            x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
        ]
        self.canvas.create_polygon(points, smooth=True, fill=self.colors.accent, outline="")
        self.canvas.create_text(
            TILE_PX // 2, TILE_PX // 2, text=label[:1].upper(), fill="white",
            font=("Helvetica", TILE_PX // 3, "bold"),
        )

    def set_label(self, label: str | None = None) -> None:
        """Change the label; with no argument, put the original back."""
        self.text.configure(text=self.label if label is None else label)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(cursor="pointinghand" if enabled else "arrow")
        self.text.configure(fg=self.colors.text if enabled else self.colors.disabled_text)
        self._paint()

    def invoke(self) -> None:
        if self.enabled:
            self.command()

    def _paint(self) -> None:
        bg = self.colors.hover if self.hovered and self.enabled else self.colors.bg
        for widget in (self, self.canvas, self.text):
            widget.configure(bg=bg)
        self.configure(highlightbackground=bg)

    def _pointer_inside(self) -> bool:
        widget = self.winfo_containing(*self.winfo_pointerxy())
        while widget is not None:
            if widget is self:
                return True
            widget = widget.master
        return False

    def _on_enter(self, _event: tk.Event) -> None:
        if not self.hovered:
            self.hovered = True
            self._paint()

    def _on_leave(self, _event: tk.Event) -> None:
        # Moving between the tile and its children fires Leave too; only un-hover when the
        # pointer has actually left the tile.
        if self.hovered and not self._pointer_inside():
            self.hovered = False
            self._paint()

    def _on_release(self, _event: tk.Event) -> None:
        if self._pointer_inside():  # releasing outside the tile cancels the click
            self.invoke()

    def _on_key(self, _event: tk.Event) -> str:
        self.invoke()
        return "break"


class TileGrid(tk.Frame):
    """Lays tiles out in rows, re-wrapping them to fit the window width."""

    def __init__(self, master: tk.Misc, colors: Colors) -> None:
        super().__init__(master, bg=colors.bg)
        self.colors = colors
        self.tiles: list[Tile] = []
        self.columns = 0
        self.inner = tk.Frame(self, bg=colors.bg)
        self.inner.pack(side=tk.TOP, pady=TILE_PAD)
        self.bind("<Configure>", lambda _e: self._layout())

    def add(self, label: str, image: str | None, command: Callable[[], None]) -> Tile:
        tile = Tile(self.inner, self.colors, label, image, command)
        self.tiles.append(tile)
        self.columns = 0  # force a re-layout
        self._layout()
        return tile

    def _layout(self) -> None:
        if not self.tiles:
            return
        cell = self.tiles[0].winfo_reqwidth() + TILE_PAD
        columns = max(1, min(len(self.tiles), self.winfo_width() // cell))
        if columns == self.columns:
            return
        self.columns = columns
        for i, tile in enumerate(self.tiles):
            tile.grid(row=i // columns, column=i % columns, padx=TILE_PAD // 2, pady=TILE_PAD // 2, sticky=tk.N)


class LogWindow(tk.Toplevel):
    """Shows the game log, including everything logged before the window was opened."""

    def __init__(self, master: tk.Misc, log: LogBuffer) -> None:
        super().__init__(master)
        self.log = log
        self.title("ZZMMI Log")
        self.geometry("820x520")
        self.minsize(480, 300)

        self.output = ScrolledText(self, wrap=tk.WORD, state=tk.DISABLED, font=("Menlo", 11))
        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.append(log.contents())
        self.output.see(tk.END)

        log.subscribe(self.append)
        self.bind("<Destroy>", self._on_destroy)

    def append(self, text: str) -> None:
        at_bottom = self.output.yview()[1] >= 0.999
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, text)
        self.output.configure(state=tk.DISABLED)
        if at_bottom:
            self.output.see(tk.END)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self.log.unsubscribe(self.append)


class ModlistWindow(tk.Toplevel):
    """Lists the installed mods with a checkbox each for enabling/disabling them.

    Ticking a box only records the choice. "Save list" applies the choices, moving mods between
    the Mods and ModsNotUsing folders, then reloads mods in the game if it's running. The list
    re-reads the folders whenever the window gets focus, keeping any unsaved choices.
    """

    def __init__(self, master: tk.Misc, colors: Colors) -> None:
        super().__init__(master, bg=colors.bg)
        self.colors = colors
        self.title("ZZMMI Modlist")
        self.geometry("500x800")
        self.minsize(300, 200)

        # Typing in the search box filters the rows as you go; Escape clears it.
        search_bar = tk.Frame(self, bg=colors.bg)
        search_bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))
        tk.Label(search_bar, text="Search", bg=colors.bg, fg=colors.text).pack(side=tk.LEFT)
        self.query = tk.StringVar(self)
        self.query.trace_add("write", lambda *_: self._on_query())
        self.search = tk.Entry(search_bar, textvariable=self.query, highlightbackground=colors.bg)
        self.search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.search.bind("<Escape>", lambda _e: self.query.set(""))

        bar = tk.Frame(self, bg=colors.bg)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))
        self.save_button = tk.Button(bar, text="Save list", highlightbackground=colors.bg, command=self.save)
        self.save_button.pack(side=tk.LEFT)

        # A frame of rows inside a canvas, so the list scrolls.
        self.canvas = tk.Canvas(self, bg=colors.bg, highlightthickness=0)
        scrollbar = tk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        self.rows = tk.Frame(self.canvas, bg=colors.bg)
        rows_id = self.canvas.create_window(0, 0, window=self.rows, anchor=tk.NW)
        self.rows.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox(tk.ALL)))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(rows_id, width=e.width))
        # The Toplevel is in every child's bindtags, so these catch events anywhere in the window.
        self.bind("<MouseWheel>", self._on_wheel)
        if TK9:  # Tk 9 reports trackpad and Magic Mouse scrolling as its own event
            self.bind("<TouchpadScroll>", self._on_touchpad)
        # FocusIn also fires when focus moves between widgets in the window; refresh() only
        # rebuilds the rows when the folders changed, so those extra calls cost one directory read.
        self.bind("<FocusIn>", lambda _e: self.after_idle(self.refresh))

        self.library: ModLibrary | None = None
        self.error: str | None = None  # why the library couldn't be read, if it couldn't
        self.mods: list[Mod] = []  # on-disk state the rows were built from
        self.checks: dict[Path, tk.BooleanVar] = {}  # by Mod.path
        self.row_widgets: dict[Path, tk.Checkbutton] = {}  # by Mod.path
        self.no_matches: tk.Label | None = None  # shown when the search hides every row
        self.saving = False
        self.refresh(force=True)
        self.search.focus_set()

    def refresh(self, force: bool = False, keep_pending: bool = True) -> None:
        """Re-read the mod folders and rebuild the rows if anything changed.

        Unsaved choices survive the rebuild unless `keep_pending` is False.
        """
        if self.saving or not self.winfo_exists():
            return
        try:
            if self.library is None:
                self.library = ModLibrary.discover()
            mods, error = self.library.mods(), None
        except (YaaglError, OSError) as e:
            mods, error = [], f"Couldn't find the mods:\n\n{e}"
        if not force and mods == self.mods and error == self.error:
            return
        pending = self.pending() if keep_pending else {}
        self.mods, self.error = mods, error
        self.checks = {}
        self.row_widgets = {}
        self.no_matches = None
        for child in self.rows.winfo_children():
            child.destroy()
        if error is not None:
            self._message(error)
        elif not mods:
            assert self.library is not None
            self._message(f"No mods installed yet.\n\nPut them in {self.library.enabled_dir}")
        for mod in mods:
            self._add_row(mod, pending.get(mod.name, mod.enabled))
        self._filter()

    def pending(self) -> dict[str, bool]:
        """Choices that differ from what's on disk, as {mod name: enabled}."""
        return {m.name: self.checks[m.path].get() for m in self.mods
                if m.path in self.checks and self.checks[m.path].get() != m.enabled}

    def save(self) -> None:
        """Move the mods whose boxes changed, then reload mods in the running game."""
        if self.saving or self.library is None:
            return
        errors = []
        for name, enabled in self.pending().items():
            try:
                self.library.set_enabled(name, enabled)
            except (YaaglError, OSError) as e:
                errors.append(f"Couldn't {'enable' if enabled else 'disable'} {name}: {e}")
        self.refresh(force=True, keep_pending=False)  # failed moves show what's actually on disk
        if errors:
            messagebox.showerror("ZZMMI", "\n\n".join(errors), parent=self)

        # Pressing the reload key takes about half a second, so it runs on a worker thread and
        # Tk polls for the result.
        result: dict[str, str] = {}

        def reload() -> None:
            try:
                xxmi_reload_mods()
            except (YaaglError, OSError, subprocess.SubprocessError) as e:
                result["error"] = str(e)

        def poll() -> None:
            if worker.is_alive():
                self.after(POLL_MS, poll)
                return
            self.saving = False
            self.save_button.configure(state=tk.NORMAL, text="Save list")
            if "error" in result:
                messagebox.showerror("ZZMMI", f"Saved, but couldn't reload mods in the game:\n\n{result['error']}",
                                     parent=self)

        self.saving = True
        self.save_button.configure(state=tk.DISABLED, text="Saving…")
        worker = threading.Thread(target=reload, daemon=True)
        worker.start()
        self.after(POLL_MS, poll)

    def _message(self, text: str) -> tk.Label:
        label = tk.Label(
            self.rows, text=text, bg=self.colors.bg, fg=self.colors.text, wraplength=440, justify=tk.LEFT,
        )
        label.pack(side=tk.TOP, anchor=tk.W, padx=8, pady=8)
        return label

    def _add_row(self, mod: Mod, checked: bool) -> None:
        """Make a mod's row; _filter() decides whether it's shown."""
        var = tk.BooleanVar(self, value=checked)
        self.checks[mod.path] = var
        # Checkbutton draws its box to the left of its text, and clicking the name toggles it too.
        self.row_widgets[mod.path] = tk.Checkbutton(
            self.rows, text=mod.name, variable=var, anchor=tk.W, bg=self.colors.bg, fg=self.colors.text,
            highlightthickness=0,
        )

    def _filter(self) -> None:
        """Show only the rows whose mod name contains every word in the search box (ignoring case).

        Hidden rows keep their checkbox state, so unsaved choices for them are still saved.
        """
        words = self.query.get().casefold().split()
        if self.no_matches is not None:
            self.no_matches.destroy()
            self.no_matches = None
        shown = 0
        # Forgetting and re-packing each row in turn keeps the shown rows in list order.
        for mod in self.mods:
            row = self.row_widgets.get(mod.path)
            if row is None:
                continue
            row.pack_forget()
            if all(word in mod.name.casefold() for word in words):
                row.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
                shown += 1
        if self.row_widgets and not shown:
            self.no_matches = self._message(f"No mods match “{self.query.get().strip()}”.")

    def _on_query(self) -> None:
        self._filter()
        self.canvas.yview_moveto(0)  # the old scroll position means nothing for the new results

    def _scrollable(self) -> bool:
        return self.rows.winfo_height() > self.canvas.winfo_height()  # if everything fits, don't drift

    def _scroll_pixels(self, pixels: float) -> None:
        """Scroll the list down by `pixels` (up if negative)."""
        top = self.canvas.yview()[0]
        self.canvas.yview_moveto(top + pixels / self.rows.winfo_height())

    def _on_wheel(self, event: tk.Event) -> None:
        if not self._scrollable():
            return
        if TK9:
            # Delta is in 120ths of a notch and can be any size; scroll like Tk 9's text widget.
            self._scroll_pixels(-event.delta / 4)
        else:
            # Aqua Tk 8.6 reports whole lines per event, trackpad scrolling included.
            self.canvas.yview_scroll(-event.delta, "units")

    def _on_touchpad(self, event: tk.Event) -> None:
        if not self._scrollable():
            return
        # %D packs the horizontal and vertical pixel deltas into one number.
        _dx, dy = (int(v) for v in self.tk.splitlist(self.tk.call("tk::PreciseScrollDeltas", event.delta)))
        self._scroll_pixels(-dy)


class Popup(tk.Toplevel):
    """A small dialog: a tile image on the left, a message to its right, buttons along the bottom.

    `buttons` are (label, action) pairs in left-to-right order; the rightmost is the default
    (Return) and the leftmost is the cancel (Escape). Any button closes the popup first, then
    runs its action, if it has one.
    """

    def __init__(
        self, master: tk.Misc, colors: Colors, image: str, message: str, detail: str = "",
        buttons: tuple[tuple[str, Callable[[], None] | None], ...] = (("Okay", None),),
    ) -> None:
        super().__init__(master, bg=colors.bg, padx=20, pady=16)
        self.title("ZZMMI")
        self.resizable(False, False)
        self.transient(master)

        body = tk.Frame(self, bg=colors.bg)
        body.pack(side=tk.TOP, fill=tk.X)
        self.photo = load_tile_image(TILE_IMAGES / image, TILE_PX)
        if self.photo is not None:
            tk.Label(body, image=self.photo, bg=colors.bg).pack(side=tk.LEFT, padx=(0, 16))
        text = tk.Frame(body, bg=colors.bg)
        text.pack(side=tk.LEFT, fill=tk.X)  # vertically centered beside the image
        base = tkfont.nametofont("TkDefaultFont")
        self.heading_font = base.copy()
        self.heading_font.configure(weight="bold", size=base.cget("size") + 2)
        tk.Label(
            text, text=message, font=self.heading_font, bg=colors.bg, fg=colors.text,
            wraplength=260, justify=tk.LEFT,
        ).pack(side=tk.TOP, anchor=tk.W)
        if detail:
            tk.Label(
                text, text=detail, bg=colors.bg, fg=colors.text, wraplength=260, justify=tk.LEFT,
            ).pack(side=tk.TOP, anchor=tk.W, pady=(6, 0))

        row = tk.Frame(self, bg=colors.bg)
        row.pack(side=tk.TOP, fill=tk.X, pady=(16, 0))
        made = []
        for label, action in reversed(buttons):
            button = tk.Button(
                row, text=label, width=8, highlightbackground=colors.bg,
                command=lambda action=action: self._choose(action),
            )
            button.pack(side=tk.RIGHT, padx=(8, 0))
            made.append(button)
        default, cancel = made[0], made[-1]
        default.configure(default=tk.ACTIVE)
        self.bind("<Return>", lambda _e: default.invoke())
        self.bind("<KP_Enter>", lambda _e: default.invoke())
        self.bind("<Escape>", lambda _e: cancel.invoke())

        # Center horizontally over the main window, a little below its title bar.
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + 60
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        default.focus_set()

    def _choose(self, action: Callable[[], None] | None) -> None:
        self.destroy()
        if action is not None:
            action()


def load_gif_frames(path: Path) -> list[tk.PhotoImage]:
    frames = []
    while True:
        try:
            frames.append(tk.PhotoImage(file=str(path), format=f"gif -index {len(frames)}"))
        except tk.TclError:  # past the last frame (or no such file)
            return frames


def dark_mode(widget: tk.Misc) -> bool:
    try:
        return bool(int(widget.tk.call("tk::unsupported::MacWindowStyle", "isdark", widget)))
    except tk.TclError:  # not Aqua, or Tk older than 8.6.10
        return False


class LoadingWindow(tk.Toplevel):
    """A "Now Loading" window that stays above other windows while the game starts.

    It closes itself once the game's window is on screen, which a background thread watches
    for. close() also closes it (the game exited first); so does its close button.
    """

    def __init__(self, master: tk.Misc, colors: Colors) -> None:
        super().__init__(master, bg=colors.bg)
        self.title("Now Loading")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.frames = load_gif_frames(LOADING_GIF_DARK if dark_mode(master) else LOADING_GIF_LIGHT)
        self.frame = 0
        self.label = tk.Label(
            self, bg=colors.bg, fg=colors.text, bd=0,
            **({"image": self.frames[0]} if self.frames else {"text": "Now loading…", "font": ("Helvetica", 28, "bold")}),
        )
        self.label.pack(padx=28, pady=28)

        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_reqheight()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        self.finished = threading.Event()  # set by the watcher (game window up) or by close()
        threading.Thread(target=self._watch, daemon=True).start()
        self.after(LOADING_FRAME_MS, self._tick)

    def _watch(self) -> None:
        # Runs on a worker thread; never touches Tk. The splash notices `finished` in _tick.
        while not self.finished.is_set():
            try:
                if game_window_visible():
                    self.finished.set()
                    return
            except (OSError, subprocess.SubprocessError):
                pass  # try again next time
            self.finished.wait(GAME_WINDOW_POLL_SECONDS)

    def _tick(self) -> None:
        if self.finished.is_set():
            self.close()
            return
        if self.frames:
            self.frame = (self.frame + 1) % len(self.frames)
            self.label.configure(image=self.frames[self.frame])
        self.after(LOADING_FRAME_MS, self._tick)

    def close(self) -> None:
        self.finished.set()  # also stops the watcher
        if self.winfo_exists():
            self.destroy()


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.colors = Colors(root)
        self.log = LogBuffer()
        self.game = GameProcess(root, on_output=self.log.write, on_exit=self._on_game_exit)
        self.windows: dict[str, tk.Toplevel] = {}
        self.loading: LoadingWindow | None = None
        self.closing = False

        root.title("ZZMMI")
        root.geometry(DEFAULT_SIZE)
        root.minsize(TILE_PX + 4 * TILE_PAD, TILE_PX + 6 * TILE_PAD)
        root.configure(bg=self.colors.bg)
        self._set_icon()

        grid = TileGrid(root, self.colors)
        grid.pack(fill=tk.BOTH, expand=True)
        self.launch_tiles = [
            grid.add("Launch with mods", "launch_game.png", self.launch),
            grid.add("Launch without mods", "launch_without_mods.png", self.launch_without_mods),
        ]
        grid.add("Edit modlist", "edit_modlist.png", self.edit_modlist)
        grid.add("Open mods folder", "open_mods_folder.png", self.open_mods)
        self.require_game_running = [
            grid.add("Toggle mods", "toggle_mods.png", self.toggle_mods),
            grid.add("Reload mods", "reload_mods.png", self.reload_mods)
        ]
        for tile in self.require_game_running:  # until a game is launched
            tile.set_enabled(False)
        grid.add("View current log", "full_log.png", self.show_log)
        grid.add("Open logs folder", "open_logs.png", self.show_logs)
        self.updates_tile = grid.add("Check for updates", "check_for_updates.png", self.check_for_updates)
        grid.add("Find mods", "find_mods.png", self.visit_gamebanana)
        grid.add("Buy me a coffee <3", "buy_me_a_coffee.png", self.visit_kofi)

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _set_icon(self) -> None:
        # Tk on macOS ignores .ico files for window icons; a PNG via iconphoto also sets the
        # Dock icon (and the default for every Toplevel). Keep a reference so the image
        # isn't garbage-collected.
        try:
            self.icon = tk.PhotoImage(file=str(ICON_PATH))
            self.root.iconphoto(True, self.icon)
        except tk.TclError:
            pass

    def open_window(self, key: str, factory: Callable[[], tk.Toplevel]) -> tk.Toplevel:
        """Open the tool window registered under `key`, or bring it to the front if it's open."""
        window = self.windows.get(key)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_force()
            return window
        window = factory()
        self.windows[key] = window

        def forget(event: tk.Event) -> None:
            if event.widget is window and self.windows.get(key) is window:
                del self.windows[key]

        window.bind("<Destroy>", forget, add="+")
        return window

    # Tile actions

    def launch(self) -> None:
        self._start_game(self.launch_tiles[0])

    def launch_without_mods(self) -> None:
        # --no-inject: the plain game, without 3DMigoto (so no mods and no ZZMI core either).
        self._start_game(self.launch_tiles[1], "--no-inject")

    def _start_game(self, tile: Tile, *extra: str) -> None:
        if self.game.running:
            return

        self.log.write(" ".join(["$ zzmmi debug-launch", *extra]) + "\n")

        try:
            self.game.start(launch_command(*extra))
        except OSError as e:
            self.log.write(f"Failed to start: {e}\n")
            return

        tile.set_label("Running…")

        for launch_tile in self.launch_tiles:  # one game at a time
            launch_tile.set_enabled(False)
        for game_tile in self.require_game_running:
            game_tile.set_enabled(True)

        self._close_loading()
        self.loading = LoadingWindow(self.root, self.colors)

    def _close_loading(self) -> None:
        if self.loading is not None:
            self.loading.close()
            self.loading = None

    def edit_modlist(self) -> None:
        self.open_window("modlist", lambda: ModlistWindow(self.root, self.colors))

    def toggle_mods(self) -> None:
        toggle_all_mods()

    def reload_mods(self) -> None:
        xxmi_reload_mods()

    def open_mods(self) -> None:
        try:
            path = mods_dir()
        except (YaaglError, OSError) as e:
            messagebox.showerror("ZZMMI", f"Couldn't find the Mods folder:\n\n{e}")
            return
        subprocess.Popen(["open", str(path)])

    def show_logs(self) -> None:
        try:
            path = logs_dir()
        except OSError as e:
            messagebox.showerror("ZZMMI", f"Couldn't create the logs folder:\n\n{e}")
            return
        subprocess.Popen(["open", str(path)])

    def show_log(self) -> None:
        self.open_window("log", lambda: LogWindow(self.root, self.log))

    def visit_gamebanana(self) -> None:
        webbrowser.open("https://gamebanana.com/games/19567")

    def visit_kofi(self) -> None:
        webbrowser.open("https://ko-fi.com/imnotjahan")

    def check_for_updates(self) -> None:
        # The network request runs on a worker thread; Tk is only touched from this thread,
        # which polls for the result.
        result: dict[str, object] = {}

        def check() -> None:
            try:
                result["available"] = updates.update_available()
            except updates.UpdateCheckError as e:
                result["error"] = str(e)

        def poll() -> None:
            if worker.is_alive():
                self.root.after(POLL_MS, poll)
                return
            self.updates_tile.set_label()
            self.updates_tile.set_enabled(True)
            if "error" in result:
                self.show_popup("error.png", "Couldn't check for updates", detail=str(result["error"]))
            elif result.get("available"):
                self.show_popup(
                    "question.png", "Update available, download now?",
                    buttons=(("Later", None), ("Yes", lambda: webbrowser.open(updates.RELEASES_URL))),
                )
            else:
                self.show_popup("up_to_date.png", "Everything's up to date!",
                                detail="You have the latest version of ZZMMI.")

        self.updates_tile.set_label("Checking…")
        self.updates_tile.set_enabled(False)  # until this check finishes
        worker = threading.Thread(target=check, daemon=True)
        worker.start()
        self.root.after(POLL_MS, poll)

    def show_popup(self, image: str, message: str, **kwargs) -> Popup:
        """Show a Popup, replacing any popup that's still open."""
        old = self.windows.get("popup")
        if old is not None and old.winfo_exists():
            old.destroy()
        return self.open_window("popup", lambda: Popup(self.root, self.colors, image, message, **kwargs))

    # Game lifecycle

    def _on_game_exit(self, code: int) -> None:
        self.log.write(f"[exited with code {code}]\n\n")
        self._close_loading()  # the launch failed, or the game quit before its window was noticed
        if self.closing:
            self.root.destroy()
            return
        for tile in self.launch_tiles:
            tile.set_label()
            tile.set_enabled(True)
        for tile in self.require_game_running:
            tile.set_enabled(False)

    def on_close(self) -> None:
        if not self.game.running:
            self.root.destroy()
            return
        if self.closing:
            return
        if not messagebox.askyesno("ZZMMI", "The game is still running. Stop it and quit?"):
            return
        self.closing = True
        self.show_log()
        self.log.write("Stopping the game...\n")
        self.game.interrupt()


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
