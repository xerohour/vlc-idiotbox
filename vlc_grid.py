#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║          VLC BORDERLESS VIDEO GRID MANAGER                   ║
║    "Every screen a portal, every grid a sacred geometry"     ║
╚═══════════════════════════════════════════════════════════════╝

Requirements:
    pip install python-vlc tkinter (tkinter is usually built-in)
    VLC must be installed on your system: https://www.videolan.org/

Usage:
    python vlc_grid.py                    # launches GUI manager
    python vlc_grid.py --layout 2x3       # start with 2 rows x 3 cols
    python vlc_grid.py --load grid.json   # restore saved session
"""

import os
import sys
import json
import time
import argparse
import random
import hashlib
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
from dataclasses import dataclass, field, asdict
from typing import Optional
import threading

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError:
    Image = ImageDraw = ImageFont = ImageTk = None

try:
    import vlc
except ImportError:
    print("ERROR: python-vlc not found. Install with: pip install python-vlc")
    print("Also ensure VLC is installed on your system.")
    sys.exit(1)


VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".ts", ".mts", ".m2ts", ".ogv", ".mpg", ".mpeg", ".vob", ".divx",
}

THUMB_SIZE = (96, 54)
THUMB_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vlc_idiotbox_thumbs")


def _ensure_thumb_dir():
    os.makedirs(THUMB_CACHE_DIR, exist_ok=True)


def _thumb_cache_path(path: str, size=THUMB_SIZE) -> str:
    _ensure_thumb_dir()
    try:
        stat = os.stat(path)
        stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        stamp = "missing"
    digest = hashlib.sha1(
        f"{os.path.abspath(path)}|{stamp}|{size[0]}x{size[1]}".encode("utf-8")
    ).hexdigest()
    return os.path.join(THUMB_CACHE_DIR, f"{digest}.png")


def _placeholder_thumb(path: str, size=THUMB_SIZE) -> str:
    thumb_path = _thumb_cache_path(f"placeholder:{path}", size)
    if os.path.exists(thumb_path):
        return thumb_path

    img = Image.new("RGB", size, "#111111")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=8, outline="#00ff88", width=2)

    label = os.path.basename(path) or "EMPTY"
    label = label[:20] if len(label) > 20 else label
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((size[0] - tw) / 2, (size[1] - th) / 2 - 1), label, fill="#e0e0e0", font=font)
    img.save(thumb_path)
    return thumb_path


def _generate_video_thumb(path: str, size=THUMB_SIZE) -> str:
    thumb_path = _thumb_cache_path(path, size)
    if os.path.exists(thumb_path):
        return thumb_path

    if not os.path.exists(path):
        return _placeholder_thumb(path, size)

    inst = None
    player = None
    try:
        inst = vlc.Instance("--quiet", "--no-video-title-show", "--no-audio")
        player = inst.media_player_new()
        media = inst.media_new(path)
        player.set_media(media)
        player.play()
        time.sleep(0.35)
        try:
            player.set_time(1000)
        except Exception:
            pass
        time.sleep(0.4)
        player.video_take_snapshot(0, thumb_path, size[0], size[1])
        if not os.path.exists(thumb_path) or os.path.getsize(thumb_path) == 0:
            return _placeholder_thumb(path, size)
        return thumb_path
    except Exception:
        return _placeholder_thumb(path, size)
    finally:
        try:
            if player:
                player.stop()
                player.release()
        except Exception:
            pass
        try:
            if inst:
                inst.release()
        except Exception:
            pass


def _scan_folder(folder: str, recursive=True) -> list:
    results = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            for fn in sorted(files):
                if os.path.splitext(fn)[1].lower() in VIDEO_EXTS:
                    results.append(os.path.join(root, fn))
    else:
        for fn in sorted(os.listdir(folder)):
            if os.path.splitext(fn)[1].lower() in VIDEO_EXTS:
                results.append(os.path.join(folder, fn))
    return results


# ─────────────────────────────────────────────────
#  DATA STRUCTURES
# ─────────────────────────────────────────────────

@dataclass
class CellConfig:
    """Configuration for a single grid cell"""
    row: int
    col: int
    video_path: str = ""
    loop: bool = True
    muted: bool = True
    volume: int = 50  # 0-100
    bg_color: str = "#000000"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class GridConfig:
    """Full grid layout configuration"""
    rows: int = 2
    cols: int = 2
    screen_x: int = 0
    screen_y: int = 0
    screen_width: int = 1920
    screen_height: int = 1080
    gap: int = 0  # pixel gap between cells
    cells: list = field(default_factory=list)

    def to_dict(self):
        return {
            "rows": self.rows,
            "cols": self.cols,
            "screen_x": self.screen_x,
            "screen_y": self.screen_y,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "gap": self.gap,
            "cells": [c.to_dict() for c in self.cells]
        }

    @classmethod
    def from_dict(cls, d):
        cells = [CellConfig.from_dict(c) for c in d.get("cells", [])]
        return cls(
            rows=d.get("rows", 2),
            cols=d.get("cols", 2),
            screen_x=d.get("screen_x", 0),
            screen_y=d.get("screen_y", 0),
            screen_width=d.get("screen_width", 1920),
            screen_height=d.get("screen_height", 1080),
            gap=d.get("gap", 0),
            cells=cells
        )

    def get_cell(self, row, col) -> Optional[CellConfig]:
        for c in self.cells:
            if c.row == row and c.col == col:
                return c
        return None

    def set_cell(self, cell: CellConfig):
        for i, c in enumerate(self.cells):
            if c.row == cell.row and c.col == cell.col:
                self.cells[i] = cell
                return
        self.cells.append(cell)

    def ensure_cells(self):
        """Make sure all grid positions have a CellConfig"""
        for r in range(self.rows):
            for c in range(self.cols):
                if not self.get_cell(r, c):
                    self.cells.append(CellConfig(row=r, col=c))

    def remove_out_of_bounds(self):
        self.cells = [c for c in self.cells
                      if c.row < self.rows and c.col < self.cols]


# ─────────────────────────────────────────────────
#  VLC PLAYER WINDOW — one per cell
# ─────────────────────────────────────────────────

class VLCWindow:
    """A borderless, always-on-top VLC window for one grid cell"""

    def __init__(self, cell: CellConfig, x: int, y: int, w: int, h: int):
        self.cell = cell
        self.x = x
        self.y = y
        self.w = w
        self.h = h

        self._instance = vlc.Instance("--no-xlib", "--quiet")
        self._player = self._instance.media_player_new()

        # Tkinter container window (borderless)
        self._root = tk.Tk()
        self._root.overrideredirect(True)          # NO title bar / borders
        self._root.attributes("-topmost", False)   # set True for always-on-top
        self._root.configure(bg=cell.bg_color)
        self._root.geometry(f"{w}x{h}+{x}+{y}")

        # Canvas VLC renders into
        self._canvas = tk.Canvas(
            self._root, width=w, height=h,
            bg=cell.bg_color, highlightthickness=0
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)

        # Right-click context menu
        self._menu = tk.Menu(self._root, tearoff=0)
        self._menu.add_command(label="Load Video…", command=self._browse_video)
        self._menu.add_command(label="Toggle Mute", command=self._toggle_mute)
        self._menu.add_command(label="Mute", command=lambda: self._set_mute(True))
        self._menu.add_command(label="Unmute", command=lambda: self._set_mute(False))
        self._menu.add_command(label="Volume 100%", command=lambda: self._set_volume(100))
        self._menu.add_command(label="Volume 50%", command=lambda: self._set_volume(50))
        self._menu.add_command(label="Volume 25%", command=lambda: self._set_volume(25))
        self._menu.add_command(label="Toggle Loop", command=self._toggle_loop)
        self._menu.add_separator()
        self._menu.add_command(label="Stop", command=self._stop)
        self._canvas.bind("<Button-3>", self._show_menu)

        self._running = True
        self._root.after(100, self._embed_vlc)

    def _embed_vlc(self):
        """Attach VLC output to our canvas after window is realized"""
        wid = self._canvas.winfo_id()
        if sys.platform.startswith("win"):
            self._player.set_hwnd(wid)
        elif sys.platform.startswith("linux"):
            self._player.set_xwindow(wid)
        elif sys.platform == "darwin":
            self._player.set_nsobject(wid)

        if self.cell.video_path and os.path.exists(self.cell.video_path):
            self._play(self.cell.video_path)

    def _play(self, path: str):
        media = self._instance.media_new(path)
        if self.cell.loop:
            media.add_option("input-repeat=65535")
        self._player.set_media(media)
        self._player.audio_set_mute(self.cell.muted)
        self._player.audio_set_volume(self.cell.volume)
        self._player.play()

    def _browse_video(self):
        path = filedialog.askopenfilename(
            title="Select video",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v"),
                ("All files", "*.*")
            ]
        )
        if path:
            self.cell.video_path = path
            self._play(path)

    def _toggle_mute(self):
        self._set_mute(not self.cell.muted)

    def _set_mute(self, muted: bool):
        self.cell.muted = muted
        self._player.audio_set_mute(muted)

    def _set_volume(self, volume: int):
        volume = max(0, min(100, int(volume)))
        self.cell.volume = volume
        self._player.audio_set_volume(volume)

    def _toggle_loop(self):
        self.cell.loop = not self.cell.loop
        # Restart with new loop setting
        if self.cell.video_path:
            self._play(self.cell.video_path)

    def _stop(self):
        self._player.stop()

    def _show_menu(self, event):
        self._menu.tk_popup(event.x_root, event.y_root)

    def update_geometry(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self._root.geometry(f"{w}x{h}+{x}+{y}")
        self._canvas.config(width=w, height=h)

    def destroy(self):
        self._running = False
        try:
            self._player.stop()
            self._root.destroy()
        except Exception:
            pass

    def mainloop_step(self):
        """Call regularly to process tk events"""
        try:
            self._root.update()
        except tk.TclError:
            self._running = False


# ─────────────────────────────────────────────────
#  CONTROL PANEL — the cockpit
# ─────────────────────────────────────────────────

class ControlPanel:
    """
    The mission control GUI.
    Lets you add/remove rows & columns, set videos per cell,
    launch / stop the grid, save/load configs.
    """

    def __init__(self, config: GridConfig):
        self.config = config
        self.config.ensure_cells()
        self._windows: dict[tuple, VLCWindow] = {}  # (row,col) -> VLCWindow
        self._running = False

        self.root = tk.Tk()
        self.root.title("⬛ VLC GRID CONTROL ⬛")
        self.root.configure(bg="#111111")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._thumb_cache = {}
        self._thumb_pending = set()
        if Image is not None and ImageTk is not None:
            self._thumb_placeholder = ImageTk.PhotoImage(
                Image.open(_placeholder_thumb("", THUMB_SIZE))
            )
        else:
            self._thumb_placeholder = tk.PhotoImage(width=THUMB_SIZE[0], height=THUMB_SIZE[1])
            self._thumb_placeholder.put("#111111", to=(0, 0, THUMB_SIZE[0], THUMB_SIZE[1]))

        self._build_ui()
        self._refresh_cell_table()

    # ── UI BUILD ──────────────────────────────────

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#111111")
        style.configure("TLabel", background="#111111", foreground="#e0e0e0",
                        font=("Courier New", 10))
        style.configure("TButton", background="#222222", foreground="#00ff88",
                        font=("Courier New", 10, "bold"), borderwidth=1)
        style.configure("TEntry", fieldbackground="#1a1a1a", foreground="#ffffff",
                        font=("Courier New", 10))
        style.configure("TSpinbox", fieldbackground="#1a1a1a", foreground="#ffffff",
                        font=("Courier New", 10))

        # ── TOP: grid dimensions ──
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="ROWS").grid(row=0, column=0, padx=4)
        self._rows_var = tk.IntVar(value=self.config.rows)
        ttk.Spinbox(top, from_=1, to=10, textvariable=self._rows_var,
                    width=4).grid(row=0, column=1, padx=4)

        ttk.Label(top, text="COLS").grid(row=0, column=2, padx=4)
        self._cols_var = tk.IntVar(value=self.config.cols)
        ttk.Spinbox(top, from_=1, to=10, textvariable=self._cols_var,
                    width=4).grid(row=0, column=3, padx=4)

        ttk.Label(top, text="GAP px").grid(row=0, column=4, padx=4)
        self._gap_var = tk.IntVar(value=self.config.gap)
        ttk.Spinbox(top, from_=0, to=50, textvariable=self._gap_var,
                    width=4).grid(row=0, column=5, padx=4)

        ttk.Button(top, text="APPLY GRID", command=self._apply_grid).grid(
            row=0, column=6, padx=8)

        # ── SCREEN POSITION ──
        screen_frame = ttk.Frame(self.root, padding=8)
        screen_frame.pack(fill=tk.X)

        ttk.Label(screen_frame, text="Screen X").grid(row=0, column=0, padx=4)
        self._sx_var = tk.IntVar(value=self.config.screen_x)
        ttk.Entry(screen_frame, textvariable=self._sx_var, width=6).grid(row=0, column=1, padx=4)

        ttk.Label(screen_frame, text="Y").grid(row=0, column=2, padx=4)
        self._sy_var = tk.IntVar(value=self.config.screen_y)
        ttk.Entry(screen_frame, textvariable=self._sy_var, width=6).grid(row=0, column=3, padx=4)

        ttk.Label(screen_frame, text="W").grid(row=0, column=4, padx=4)
        self._sw_var = tk.IntVar(value=self.config.screen_width)
        ttk.Entry(screen_frame, textvariable=self._sw_var, width=6).grid(row=0, column=5, padx=4)

        ttk.Label(screen_frame, text="H").grid(row=0, column=6, padx=4)
        self._sh_var = tk.IntVar(value=self.config.screen_height)
        ttk.Entry(screen_frame, textvariable=self._sh_var, width=6).grid(row=0, column=7, padx=4)

        ttk.Button(screen_frame, text="DETECT SCREEN",
                   command=self._detect_screen).grid(row=0, column=8, padx=8)

        # ── CELL TABLE ──
        table_frame = tk.Frame(self.root, bg="#111111")
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        style.configure("Treeview", rowheight=58)
        cols = ("Path", "Loop", "Muted", "Vol", "BG")
        self._tree = ttk.Treeview(table_frame, columns=cols, show="tree headings", height=8)
        self._tree.heading("#0", text="Thumb")
        self._tree.column("#0", width=104, minwidth=104, stretch=False)
        for c in cols:
            self._tree.heading(c, text=c)
            self._tree.column(c, width={"Path": 280, "Loop": 50, "Muted": 55,
                                        "Vol": 40, "BG": 70}.get(c, 80))
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL,
                               command=self._tree.yview)
        self._tree.configure(yscroll=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<Double-1>", self._on_cell_dblclick)

        # ── ROW/COL CONTROLS ──
        grid_ops = ttk.Frame(self.root, padding=6)
        grid_ops.pack(fill=tk.X)

        ttk.Button(grid_ops, text="+ Row", command=self._add_row).pack(side=tk.LEFT, padx=4)
        ttk.Button(grid_ops, text="- Row", command=self._remove_row).pack(side=tk.LEFT, padx=4)
        ttk.Button(grid_ops, text="+ Col", command=self._add_col).pack(side=tk.LEFT, padx=4)
        ttk.Button(grid_ops, text="- Col", command=self._remove_col).pack(side=tk.LEFT, padx=4)

        # Separator
        tk.Label(grid_ops, text="  │  ", bg="#111111", fg="#444").pack(side=tk.LEFT)

        ttk.Button(grid_ops, text="▶ LAUNCH GRID",
                   command=self._launch_grid).pack(side=tk.LEFT, padx=8)
        ttk.Button(grid_ops, text="■ STOP ALL",
                   command=self._stop_all).pack(side=tk.LEFT, padx=4)

        # ── QUICK AUDIO ──
        audio_ops = ttk.Frame(self.root, padding=6)
        audio_ops.pack(fill=tk.X)

        ttk.Button(audio_ops, text="Mute All",
                   command=self._mute_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(audio_ops, text="Unmute All",
                   command=self._unmute_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(audio_ops, text="Vol 25%",
                   command=lambda: self._set_all_volume(25)).pack(side=tk.LEFT, padx=4)
        ttk.Button(audio_ops, text="Vol 50%",
                   command=lambda: self._set_all_volume(50)).pack(side=tk.LEFT, padx=4)
        ttk.Button(audio_ops, text="Vol 100%",
                   command=lambda: self._set_all_volume(100)).pack(side=tk.LEFT, padx=4)

        # ── SAVE / LOAD ──
        file_ops = ttk.Frame(self.root, padding=6)
        file_ops.pack(fill=tk.X)
        ttk.Button(file_ops, text="💾 Save Config",
                   command=self._save_config).pack(side=tk.LEFT, padx=4)
        ttk.Button(file_ops, text="📂 Load Config",
                   command=self._load_config).pack(side=tk.LEFT, padx=4)
        ttk.Button(file_ops, text="📁 Folder → Cells",
                   command=lambda: self._batch_load_folder(recursive=False, shuffle=False)).pack(side=tk.LEFT, padx=4)
        ttk.Button(file_ops, text="🌀 Folder → Cells",
                   command=lambda: self._batch_load_folder(recursive=True, shuffle=True)).pack(side=tk.LEFT, padx=4)
        ttk.Button(file_ops, text="🔀 Pure Random → Cells",
                   command=self._batch_random_cells).pack(side=tk.LEFT, padx=4)

        # ── STATUS ──
        self._status_var = tk.StringVar(value="Ready. Double-click a cell to configure it.")
        status_bar = tk.Label(self.root, textvariable=self._status_var,
                              bg="#0a0a0a", fg="#888888",
                              font=("Courier New", 9), anchor=tk.W, padx=6)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ── CELL TABLE REFRESH ────────────────────────

    def _refresh_cell_table(self):
        for item in self._tree.get_children():
            self._tree.delete(item)
        for r in range(self.config.rows):
            for c in range(self.config.cols):
                cell = self.config.get_cell(r, c)
                if cell is None:
                    cell = CellConfig(row=r, col=c)
                    self.config.set_cell(cell)
                thumb = self._thumbnail_for(cell.video_path)
                vname = os.path.basename(cell.video_path) if cell.video_path else "— empty —"
                self._tree.insert("", tk.END, iid=f"{r},{c}", text=vname, image=thumb, values=(
                    cell.video_path or "",
                    "✓" if cell.loop else "✗",
                    "✓" if cell.muted else "✗",
                    cell.volume,
                    cell.bg_color
                ))

    # ── GRID APPLY ────────────────────────────────

    def _apply_grid(self):
        self.config.rows = self._rows_var.get()
        self.config.cols = self._cols_var.get()
        self.config.gap = self._gap_var.get()
        self.config.ensure_cells()
        self.config.remove_out_of_bounds()
        self._refresh_cell_table()
        # If grid is live, reposition windows
        if self._running:
            self._reposition_windows()
        self._status("Grid updated: {}×{}".format(self.config.rows, self.config.cols))

    def _detect_screen(self):
        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        self._sw_var.set(w)
        self._sh_var.set(h)
        self._sx_var.set(0)
        self._sy_var.set(0)
        self._status(f"Detected screen: {w}×{h}")

    def _thumbnail_for(self, path: str):
        if not path:
            return self._thumb_placeholder
        if Image is None or ImageTk is None:
            return self._thumb_placeholder
        cached = self._thumb_cache.get(path)
        if cached is not None:
            return cached
        self._request_thumbnail(path)
        return self._thumb_placeholder

    def _request_thumbnail(self, path: str):
        if Image is None or ImageTk is None:
            return
        if not path or path in self._thumb_cache or path in self._thumb_pending:
            return
        if not os.path.exists(path):
            return
        self._thumb_pending.add(path)
        threading.Thread(target=self._thumbnail_worker, args=(path,), daemon=True).start()

    def _thumbnail_worker(self, path: str):
        thumb_path = _generate_video_thumb(path, THUMB_SIZE)

        def finish():
            self._thumb_pending.discard(path)
            try:
                photo = ImageTk.PhotoImage(Image.open(thumb_path))
                self._thumb_cache[path] = photo
            except Exception:
                self._thumb_cache[path] = self._thumb_placeholder
            self._refresh_cell_table()

        self.root.after(0, finish)

    # ── ROW / COL OPERATIONS ──────────────────────

    def _add_row(self):
        self.config.rows += 1
        self._rows_var.set(self.config.rows)
        self._apply_grid()

    def _remove_row(self):
        if self.config.rows > 1:
            self.config.rows -= 1
            self._rows_var.set(self.config.rows)
            self._apply_grid()

    def _add_col(self):
        self.config.cols += 1
        self._cols_var.set(self.config.cols)
        self._apply_grid()

    def _remove_col(self):
        if self.config.cols > 1:
            self.config.cols -= 1
            self._cols_var.set(self.config.cols)
            self._apply_grid()

    # ── CELL EDITOR (double-click) ─────────────────

    def _on_cell_dblclick(self, event):
        sel = self._tree.selection()
        if not sel:
            return
        iid = sel[0]
        r, c = map(int, iid.split(","))
        cell = self.config.get_cell(r, c)
        if cell is None:
            return
        self._open_cell_editor(cell)

    def _open_cell_editor(self, cell: CellConfig):
        win = tk.Toplevel(self.root)
        win.title(f"Cell [{cell.row},{cell.col}]")
        win.configure(bg="#111111")
        win.grab_set()

        def lbl(text, row):
            tk.Label(win, text=text, bg="#111111", fg="#aaaaaa",
                     font=("Courier New", 10)).grid(row=row, column=0,
                                                     sticky=tk.W, padx=8, pady=4)

        lbl("Video Path:", 0)
        path_var = tk.StringVar(value=cell.video_path)
        path_entry = tk.Entry(win, textvariable=path_var, width=40,
                              bg="#1a1a1a", fg="#fff", font=("Courier New", 10))
        path_entry.grid(row=0, column=1, padx=4, pady=4)

        def browse():
            p = filedialog.askopenfilename(
                filetypes=[("Video", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v"),
                           ("All", "*.*")])
            if p:
                path_var.set(p)

        tk.Button(win, text="Browse", command=browse,
                  bg="#222", fg="#00ff88").grid(row=0, column=2, padx=4)

        lbl("Loop:", 1)
        loop_var = tk.BooleanVar(value=cell.loop)
        tk.Checkbutton(win, variable=loop_var, bg="#111111",
                       selectcolor="#222222").grid(row=1, column=1, sticky=tk.W)

        lbl("Muted:", 2)
        muted_var = tk.BooleanVar(value=cell.muted)
        tk.Checkbutton(win, variable=muted_var, bg="#111111",
                       selectcolor="#222222").grid(row=2, column=1, sticky=tk.W)

        lbl("Volume (0-100):", 3)
        vol_var = tk.IntVar(value=cell.volume)
        tk.Scale(win, from_=0, to=100, orient=tk.HORIZONTAL,
                 variable=vol_var, bg="#111111", fg="#fff",
                 troughcolor="#333", highlightthickness=0,
                 length=200).grid(row=3, column=1, sticky=tk.W)

        lbl("BG Color:", 4)
        bg_var = tk.StringVar(value=cell.bg_color)
        bg_swatch = tk.Label(win, bg=cell.bg_color, width=6, relief=tk.SUNKEN)
        bg_swatch.grid(row=4, column=1, sticky=tk.W, padx=4)

        def pick_color():
            color = colorchooser.askcolor(color=bg_var.get(), title="BG Color")
            if color[1]:
                bg_var.set(color[1])
                bg_swatch.configure(bg=color[1])

        tk.Button(win, text="Pick", command=pick_color,
                  bg="#222", fg="#00ff88").grid(row=4, column=2)

        def save_cell():
            cell.video_path = path_var.get()
            cell.loop = loop_var.get()
            cell.muted = muted_var.get()
            cell.volume = vol_var.get()
            cell.bg_color = bg_var.get()
            self.config.set_cell(cell)
            self._refresh_cell_table()
            # If live, update the window
            key = (cell.row, cell.col)
            if key in self._windows:
                w = self._windows[key]
                w.cell = cell
                if cell.video_path:
                    w._play(cell.video_path)
            win.destroy()

        tk.Button(win, text="✔ Save", command=save_cell,
                  bg="#002211", fg="#00ff88",
                  font=("Courier New", 11, "bold"),
                  padx=10, pady=4).grid(row=5, column=1, pady=12)

    # ── LAUNCH / STOP ─────────────────────────────

    def _cell_pixel_rect(self, r, c):
        """Calculate pixel rect for a cell given current config"""
        total_w = self._sw_var.get()
        total_h = self._sh_var.get()
        ox = self._sx_var.get()
        oy = self._sy_var.get()
        gap = self.config.gap

        cell_w = (total_w - gap * (self.config.cols - 1)) // self.config.cols
        cell_h = (total_h - gap * (self.config.rows - 1)) // self.config.rows

        x = ox + c * (cell_w + gap)
        y = oy + r * (cell_h + gap)
        return x, y, cell_w, cell_h

    def _launch_grid(self):
        if self._running:
            self._stop_all()
        self.config.screen_x = self._sx_var.get()
        self.config.screen_y = self._sy_var.get()
        self.config.screen_width = self._sw_var.get()
        self.config.screen_height = self._sh_var.get()
        self.config.gap = self._gap_var.get()

        for r in range(self.config.rows):
            for c in range(self.config.cols):
                cell = self.config.get_cell(r, c)
                if cell is None:
                    continue
                x, y, w, h = self._cell_pixel_rect(r, c)
                win = VLCWindow(cell, x, y, w, h)
                self._windows[(r, c)] = win

        self._running = True
        self._status(f"Grid launched: {self.config.rows}×{self.config.cols} "
                     f"@ {self.config.screen_width}×{self.config.screen_height}")
        self._tick()

    def _reposition_windows(self):
        for (r, c), win in self._windows.items():
            if r < self.config.rows and c < self.config.cols:
                x, y, w, h = self._cell_pixel_rect(r, c)
                win.update_geometry(x, y, w, h)

    def _stop_all(self):
        for win in self._windows.values():
            win.destroy()
        self._windows.clear()
        self._running = False
        self._status("All windows stopped.")

    def _apply_audio_all(self, muted: Optional[bool] = None, volume: Optional[int] = None):
        for cell in self.config.cells:
            if muted is not None:
                cell.muted = muted
            if volume is not None:
                cell.volume = max(0, min(100, int(volume)))

        for win in self._windows.values():
            if muted is not None:
                win._set_mute(muted)
            if volume is not None:
                win._set_volume(volume)

        self._refresh_cell_table()

    def _mute_all(self):
        self._apply_audio_all(muted=True)
        self._status("All cells muted.")

    def _unmute_all(self):
        self._apply_audio_all(muted=False)
        self._status("All cells unmuted.")

    def _set_all_volume(self, volume: int):
        self._apply_audio_all(volume=volume)
        self._status(f"All cells volume set to {volume}%.")

    def _tick(self):
        """Drive VLC window event loops from the control panel"""
        if self._running:
            dead = []
            for key, win in self._windows.items():
                win.mainloop_step()
                if not win._running:
                    dead.append(key)
            for k in dead:
                del self._windows[k]
            self.root.after(16, self._tick)  # ~60fps tick

    # ── SAVE / LOAD ───────────────────────────────

    def _save_config(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON config", "*.json"), ("All", "*.*")],
            title="Save Grid Config"
        )
        if path:
            with open(path, "w") as f:
                json.dump(self.config.to_dict(), f, indent=2)
            self._status(f"Saved: {path}")

    def _load_config(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON config", "*.json"), ("All", "*.*")],
            title="Load Grid Config"
        )
        if path:
            with open(path) as f:
                data = json.load(f)
            self.config = GridConfig.from_dict(data)
            self._rows_var.set(self.config.rows)
            self._cols_var.set(self.config.cols)
            self._gap_var.set(self.config.gap)
            self._sx_var.set(self.config.screen_x)
            self._sy_var.set(self.config.screen_y)
            self._sw_var.set(self.config.screen_width)
            self._sh_var.set(self.config.screen_height)
            self._refresh_cell_table()
            self._status(f"Loaded: {path}")

    def _batch_load_folder(self, recursive=False, shuffle=False):
        """Auto-fill cells with video files from a folder tree."""
        folder = filedialog.askdirectory(title="Select Folder of Videos")
        if not folder:
            return
        videos = _scan_folder(folder, recursive=recursive)
        if shuffle:
            random.shuffle(videos)
        else:
            videos.sort(key=lambda p: os.path.basename(p).lower())
        if not videos:
            messagebox.showinfo("No Videos", "No video files found in that folder.")
            return

        idx = 0
        for r in range(self.config.rows):
            for c in range(self.config.cols):
                if idx >= len(videos):
                    break
                cell = self.config.get_cell(r, c)
                if cell is None:
                    cell = CellConfig(row=r, col=c)
                cell.video_path = videos[idx]
                self.config.set_cell(cell)
                idx += 1

        self._refresh_cell_table()
        mode = "recursive shuffle" if recursive and shuffle else "recursive" if recursive else "flat"
        self._status(f"Loaded {min(idx, len(videos))} videos from {mode} folder scan.")

    def _batch_random_cells(self):
        """Assign a random video to each cell, independently."""
        folder = filedialog.askdirectory(title="Select Folder of Videos")
        if not folder:
            return
        videos = _scan_folder(folder, recursive=True)
        if not videos:
            messagebox.showinfo("No Videos", "No video files found in that folder.")
            return

        for r in range(self.config.rows):
            for c in range(self.config.cols):
                cell = self.config.get_cell(r, c)
                if cell is None:
                    cell = CellConfig(row=r, col=c)
                cell.video_path = random.choice(videos)
                self.config.set_cell(cell)

        self._refresh_cell_table()
        self._status(f"Randomly assigned videos to {self.config.rows * self.config.cols} cells.")

    # ── MISC ──────────────────────────────────────

    def _status(self, msg):
        self._status_var.set(msg)

    def _on_close(self):
        self._stop_all()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ─────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────

def parse_layout(s: str):
    try:
        parts = s.lower().split("x")
        return int(parts[0]), int(parts[1])
    except Exception:
        raise argparse.ArgumentTypeError(f"Layout must be RxC, e.g. 2x3. Got: {s}")


def main():
    parser = argparse.ArgumentParser(
        description="VLC Borderless Video Grid Manager"
    )
    parser.add_argument("--layout", type=parse_layout, default=None,
                        metavar="RxC",
                        help="Initial grid size, e.g. --layout 2x3")
    parser.add_argument("--load", type=str, default=None,
                        metavar="CONFIG.json",
                        help="Load a saved grid config")
    parser.add_argument("--fullscreen", action="store_true",
                        help="Auto-set screen size to detected display")
    args = parser.parse_args()

    if args.load:
        with open(args.load) as f:
            config = GridConfig.from_dict(json.load(f))
    else:
        config = GridConfig()
        if args.layout:
            config.rows, config.cols = args.layout

    if args.fullscreen:
        # We need a temp tk root to detect screen size
        tmp = tk.Tk()
        config.screen_width = tmp.winfo_screenwidth()
        config.screen_height = tmp.winfo_screenheight()
        tmp.destroy()

    app = ControlPanel(config)
    app.run()


if __name__ == "__main__":
    main()
