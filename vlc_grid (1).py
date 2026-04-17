#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║            VLC BORDERLESS VIDEO GRID MANAGER  v2.0                 ║
║        "The grid is the sigil. The video is the current."          ║
╠══════════════════════════════════════════════════════════════════════╣
║  REQUIREMENTS:                                                      ║
║    sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0     ║
║               gir1.2-adw-1 vlc                                     ║
║    pip install python-vlc                                           ║
║                                                                     ║
║  USAGE:                                                             ║
║    python vlc_grid.py                   → GUI launcher             ║
║    python vlc_grid.py --layout 3x4      → 3 rows × 4 cols          ║
║    python vlc_grid.py --load grid.json  → restore saved session    ║
║    python vlc_grid.py --fullscreen      → auto-detect display      ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import random
import argparse
import time
import hashlib
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

def _run_tk_fallback():
    import importlib.util

    _fallback_path = os.path.join(os.path.dirname(__file__), "vlc_grid.py")
    if not os.path.exists(_fallback_path):
        print("Fallback launcher missing: vlc_grid.py")
        sys.exit(1)

    _spec = importlib.util.spec_from_file_location(
        "vlc_grid_windows_fallback", _fallback_path
    )
    if _spec is None or _spec.loader is None:
        print("Unable to load fallback launcher: vlc_grid.py")
        sys.exit(1)

    _fallback = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_fallback)

    _fallback.main()


if sys.platform.startswith("win"):
    _run_tk_fallback()
    sys.exit(0)

# ── GTK / Adwaita ──────────────────────────────────────────────────────
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib, Gdk, Gio, Pango, GdkPixbuf
except (ImportError, ValueError) as e:
    print(f"GTK4/Adwaita not found: {e}")
    print("Install with:")
    print("  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1")
    _run_tk_fallback()
    sys.exit(0)

# ── VLC ────────────────────────────────────────────────────────────────
try:
    import vlc
except ImportError:
    print("python-vlc not found. Install with: pip install python-vlc")
    sys.exit(1)

# ── Tkinter for borderless VLC embedding ──────────────────────────────
import tkinter as tk

# ─────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".ts", ".mts", ".m2ts", ".ogv",
    ".3gp", ".mpg", ".mpeg", ".vob", ".rmvb", ".divx",
}

THUMB_SIZE = (96, 54)
THUMB_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vlc_idiotbox_thumbs")

FILL_MODES = ["stretch", "fit", "crop", "fill"]

APP_CSS = b"""
window { background-color: #0d0d0d; }
.sidebar { background-color: #111111; border-right: 1px solid #2a2a2a; }
.cell-card { background-color: #1a1a1a; border: 1px solid #2e2e2e;
             border-radius: 6px; padding: 8px; }
.cell-label { font-family: monospace; font-size: 11px; color: #888888; }
.cell-video-label { font-family: monospace; font-size: 10px; color: #00e87a; }
.cell-empty-label { font-family: monospace; font-size: 10px; color: #444444; }
.launch-button { background-color: #003322; color: #00e87a; font-weight: bold; }
.stop-button   { background-color: #330000; color: #ff4444; font-weight: bold; }
.section-title { font-family: monospace; font-size: 11px; font-weight: bold;
                 color: #00e87a; padding: 4px 0; }
.stat-label    { font-family: monospace; font-size: 10px; color: #666666; }
"""


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
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=8, outline="#00e87a", width=2)
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


def _thumb_pixbuf(path: str, size=THUMB_SIZE):
    thumb_path = _generate_video_thumb(path, size)
    return GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb_path, size[0], size[1], True)

# ─────────────────────────────────────────────────────────────────────
#  DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────

@dataclass
class CellConfig:
    row: int
    col: int
    playlist: list         = field(default_factory=list)
    playlist_index: int    = 0
    loop_cell: bool        = True
    loop_single: bool      = False
    shuffle: bool          = False
    muted: bool            = True
    volume: int            = 50
    bg_color: str          = "#000000"
    fill_mode: str         = "stretch"
    speed: float           = 1.0
    start_offset: float    = 0.0
    aspect_override: str   = ""
    always_on_top: bool    = False
    enabled: bool          = True

    def current_video(self) -> str:
        if self.playlist and 0 <= self.playlist_index < len(self.playlist):
            return self.playlist[self.playlist_index]
        return ""

    def to_dict(self):   return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class GridConfig:
    rows: int           = 2
    cols: int           = 2
    screen_x: int       = 0
    screen_y: int       = 0
    screen_width: int   = 1920
    screen_height: int  = 1080
    gap: int            = 0
    global_mute: bool   = True
    global_volume: int  = 50
    cells: list         = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["cells"] = [c.to_dict() for c in self.cells]
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        raw_cells = d.pop("cells", [])
        known = set(cls.__dataclass_fields__)
        obj = cls(**{k: v for k, v in d.items() if k in known})
        obj.cells = [CellConfig.from_dict(c) for c in raw_cells]
        return obj

    def get_cell(self, r, c) -> Optional[CellConfig]:
        for cell in self.cells:
            if cell.row == r and cell.col == c:
                return cell
        return None

    def set_cell(self, cell: CellConfig):
        for i, c in enumerate(self.cells):
            if c.row == cell.row and c.col == cell.col:
                self.cells[i] = cell
                return
        self.cells.append(cell)

    def ensure_cells(self):
        for r in range(self.rows):
            for c in range(self.cols):
                if not self.get_cell(r, c):
                    self.cells.append(CellConfig(row=r, col=c))

    def prune_cells(self):
        self.cells = [c for c in self.cells
                      if c.row < self.rows and c.col < self.cols]


# ─────────────────────────────────────────────────────────────────────
#  VLC BORDERLESS WINDOW  (Tk)
# ─────────────────────────────────────────────────────────────────────

class VLCCell:
    def __init__(self, cell: CellConfig, x, y, w, h):
        self.cell   = cell
        self._alive = True
        self._order = list(range(len(cell.playlist)))
        self._pos   = 0
        if cell.shuffle:
            random.shuffle(self._order)

        args = ["--quiet", "--no-video-title-show"]
        if cell.aspect_override:
            args += [f"--aspect-ratio={cell.aspect_override}"]
        self._vlc    = vlc.Instance(*args)
        self._player = self._vlc.media_player_new()

        em = self._player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_end)

        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.configure(bg=cell.bg_color)
        self._root.geometry(f"{w}x{h}+{x}+{y}")
        if cell.always_on_top:
            self._root.attributes("-topmost", True)

        self._canvas = tk.Canvas(self._root, width=w, height=h,
                                  bg=cell.bg_color, highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        m = tk.Menu(self._root, tearoff=0, bg="#1a1a1a", fg="#e0e0e0",
                    activebackground="#003322", activeforeground="#00e87a")
        m.add_command(label="⏮ Prev",        command=self._prev)
        m.add_command(label="⏭ Next",        command=self._next)
        m.add_separator()
        m.add_command(label="⏸ Pause/Play",  command=lambda: self._player.pause())
        m.add_command(label="🔇 Toggle Mute", command=self._toggle_mute)
        m.add_separator()
        m.add_command(label="⏹ Stop",        command=lambda: self._player.stop())
        self._canvas.bind("<Button-3>", lambda e: m.tk_popup(e.x_root, e.y_root))

        self._root.after(120, self._embed)

    def _embed(self):
        wid = self._canvas.winfo_id()
        if   sys.platform.startswith("win"):   self._player.set_hwnd(wid)
        elif sys.platform.startswith("linux"):  self._player.set_xwindow(wid)
        elif sys.platform == "darwin":          self._player.set_nsobject(wid)
        self._play_idx(0)

    def _play_idx(self, pos):
        if not self._order: return
        pos = pos % len(self._order)
        self._pos = pos
        real = self._order[pos]
        if real >= len(self.cell.playlist): return
        path = self.cell.playlist[real]
        if not os.path.exists(path): return
        media = self._vlc.media_new(path)
        if self.cell.loop_single:   media.add_option("input-repeat=65535")
        if self.cell.start_offset:  media.add_option(f"start-time={self.cell.start_offset:.1f}")
        if self.cell.speed != 1.0:  media.add_option(f"rate={self.cell.speed:.2f}")
        self._player.set_media(media)
        self._player.audio_set_mute(self.cell.muted)
        self._player.audio_set_volume(self.cell.volume)
        self._player.play()

    def _on_end(self, _):
        GLib.idle_add(self._advance)

    def _advance(self):
        if not self._alive: return
        nxt = self._pos + 1
        if nxt >= len(self._order):
            if self.cell.loop_cell:
                if self.cell.shuffle: random.shuffle(self._order)
                self._play_idx(0)
        else:
            self._play_idx(nxt)

    def _prev(self):         self._play_idx(self._pos - 1)
    def _next(self):         self._play_idx(self._pos + 1)
    def _toggle_mute(self):
        self.cell.muted = not self.cell.muted
        self._player.audio_set_mute(self.cell.muted)

    def set_volume(self, v):
        self.cell.volume = v
        self._player.audio_set_volume(v)

    def set_mute(self, m):
        self.cell.muted = m
        self._player.audio_set_mute(m)

    def update_geometry(self, x, y, w, h):
        self._root.geometry(f"{w}x{h}+{x}+{y}")
        self._canvas.configure(width=w, height=h)

    def step(self):
        try:
            self._root.update()
        except tk.TclError:
            self._alive = False

    def destroy(self):
        self._alive = False
        try:
            self._player.stop()
            self._player.release()
            self._root.destroy()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
#  CELL EDITOR DIALOG
# ─────────────────────────────────────────────────────────────────────

class CellEditorDialog(Gtk.Dialog):

    def __init__(self, parent, cell: CellConfig):
        super().__init__(title=f"Cell [{cell.row},{cell.col}]",
                         transient_for=parent, modal=True)
        self.cell = cell
        self.set_default_size(700, 560)

        box = self.get_content_area()
        box.set_margin_top(12); box.set_margin_bottom(12)
        box.set_margin_start(16); box.set_margin_end(16)
        box.set_spacing(12)

        self._thumb_cache = {}
        self._thumb_pending = set()
        self._thumb_placeholder = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            _placeholder_thumb("", THUMB_SIZE), THUMB_SIZE[0], THUMB_SIZE[1], True
        )

        nb = Gtk.Notebook()
        nb.append_page(self._tab_playlist(), Gtk.Label(label="Playlist"))
        nb.append_page(self._tab_playback(), Gtk.Label(label="Playback"))
        nb.append_page(self._tab_display(),  Gtk.Label(label="Display"))
        box.append(nb)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _: self.response(Gtk.ResponseType.CANCEL))
        ok = Gtk.Button(label="✔ Apply")
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda _: self.response(Gtk.ResponseType.OK))
        row.append(cancel); row.append(ok)
        box.append(row)

    # ── PLAYLIST ─────────────────────────────────────────────────────

    def _tab_playlist(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(8)

        self._pl_store = Gtk.ListStore(object, str)
        for p in self.cell.playlist:
            self._pl_store.append([self._playlist_thumb(p), p])

        tv = Gtk.TreeView(model=self._pl_store)
        tv.set_reorderable(True)
        tv.set_headers_visible(True)
        thumb_col = Gtk.TreeViewColumn("")
        thumb_renderer = Gtk.CellRendererPixbuf()
        thumb_col.pack_start(thumb_renderer, False)
        thumb_col.add_attribute(thumb_renderer, "pixbuf", 0)
        thumb_col.set_fixed_width(104)
        tv.append_column(thumb_col)

        col = Gtk.TreeViewColumn("Path", Gtk.CellRendererText(), text=1)
        tv.append_column(col)
        self._pl_tv = tv

        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(180)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(tv)
        vbox.append(sw)

        # Buttons
        brow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btns = [
            ("+ File(s)",          lambda _: self._pl_add_files()),
            ("+ Folder (flat)",    lambda _: self._pl_add_folder(False)),
            ("+ Folder (recurse)", lambda _: self._pl_add_folder(True)),
            ("+ Folder (shuffle)", lambda _: self._pl_add_folder(True, True)),
            ("Remove",             lambda _: self._pl_remove()),
            ("Clear",              lambda _: (self._pl_store.clear(), self._pl_stat())),
            ("Sort A–Z",           lambda _: self._pl_sort("name")),
            ("Sort Date",          lambda _: self._pl_sort("date")),
            ("Shuffle",            lambda _: self._pl_sort("random")),
        ]
        for label, fn in btns:
            b = Gtk.Button(label=label)
            b.connect("clicked", fn)
            brow.append(b)
        vbox.append(brow)

        self._pl_stat_lbl = Gtk.Label(label="")
        self._pl_stat_lbl.add_css_class("stat-label")
        self._pl_stat_lbl.set_halign(Gtk.Align.START)
        vbox.append(self._pl_stat_lbl)
        self._pl_stat()
        return vbox

    def _pl_stat(self):
        n = len(self._pl_store)
        self._pl_stat_lbl.set_text(f"{n} file{'s' if n!=1 else ''} in playlist")

    def _playlist_thumb(self, path):
        if not path:
            return self._thumb_placeholder
        cached = self._thumb_cache.get(path)
        if cached is not None:
            return cached
        if path not in self._thumb_pending:
            self._thumb_pending.add(path)
            threading.Thread(target=self._playlist_thumb_worker, args=(path,), daemon=True).start()
        return self._thumb_placeholder

    def _playlist_thumb_worker(self, path):
        thumb_path = _generate_video_thumb(path, THUMB_SIZE)

        def finish():
            self._thumb_pending.discard(path)
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    thumb_path, THUMB_SIZE[0], THUMB_SIZE[1], True
                )
            except Exception:
                pixbuf = self._thumb_placeholder
            self._thumb_cache[path] = pixbuf
            for row in self._pl_store:
                if row[1] == path:
                    row[0] = pixbuf
            return False

        GLib.idle_add(finish)

    def _pl_add_files(self):
        dlg = Gtk.FileChooserDialog(title="Add Videos",
                                     action=Gtk.FileChooserAction.OPEN,
                                     transient_for=self)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Add",    Gtk.ResponseType.ACCEPT)
        dlg.set_select_multiple(True)
        ff = Gtk.FileFilter(); ff.set_name("Video files")
        for ext in VIDEO_EXTS: ff.add_pattern(f"*{ext}")
        dlg.add_filter(ff)

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                for f in d.get_files():
                    path = f.get_path()
                    self._pl_store.append([self._playlist_thumb(path), path])
                self._pl_stat()
            d.destroy()
        dlg.connect("response", resp)
        dlg.present()

    def _pl_add_folder(self, recursive, shuffle=False):
        dlg = Gtk.FileChooserDialog(title="Select Folder",
                                     action=Gtk.FileChooserAction.SELECT_FOLDER,
                                     transient_for=self)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Add",    Gtk.ResponseType.ACCEPT)

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                folder = d.get_file().get_path()
                items = _scan_folder(folder, recursive=recursive)
                if shuffle:
                    random.shuffle(items)
                else:
                    items.sort(key=lambda p: os.path.basename(p).lower())
                for p in items:
                    self._pl_store.append([self._playlist_thumb(p), p])
                self._pl_stat()
            d.destroy()
        dlg.connect("response", resp)
        dlg.present()

    def _pl_remove(self):
        sel = self._pl_tv.get_selection()
        _, itr = sel.get_selected()
        if itr:
            self._pl_store.remove(itr)
            self._pl_stat()

    def _pl_sort(self, mode):
        items = [row[1] for row in self._pl_store]
        if   mode == "name":   items.sort(key=lambda p: os.path.basename(p).lower())
        elif mode == "date":   items.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
        elif mode == "random": random.shuffle(items)
        self._pl_store.clear()
        for p in items:
            self._pl_store.append([self._playlist_thumb(p), p])

    def get_playlist(self):
        return [row[1] for row in self._pl_store]

    # ── PLAYBACK ─────────────────────────────────────────────────────

    def _tab_playback(self):
        g = Gtk.Grid(); g.set_row_spacing(10); g.set_column_spacing(12)
        g.set_margin_top(8); g.set_margin_start(4)
        r = 0

        def row(lbl, w):
            nonlocal r
            l = Gtk.Label(label=lbl); l.set_halign(Gtk.Align.START)
            g.attach(l, 0, r, 1, 1); g.attach(w, 1, r, 1, 1); r += 1

        def sw(val):
            s = Gtk.Switch(); s.set_active(val); return s

        def scale(val, lo, hi, step, digits, w=200):
            a = Gtk.Adjustment(value=val, lower=lo, upper=hi,
                                step_increment=step, page_increment=step*10)
            s = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=a)
            s.set_digits(digits); s.set_size_request(w, -1)
            return s, a

        self._sw_loop_cell   = sw(self.cell.loop_cell);   row("Loop Playlist",    self._sw_loop_cell)
        self._sw_loop_single = sw(self.cell.loop_single); row("Loop Single File", self._sw_loop_single)
        self._sw_shuffle     = sw(self.cell.shuffle);     row("Shuffle",          self._sw_shuffle)
        self._sw_muted       = sw(self.cell.muted);       row("Muted",            self._sw_muted)

        vs, self._vol_adj   = scale(self.cell.volume,        0,    100, 1,    0); row("Volume",         vs)
        ss, self._speed_adj = scale(self.cell.speed,        0.1,   4.0, 0.05, 2); row("Speed ×",        ss)
        os_, self._off_adj  = scale(self.cell.start_offset,  0,   3600, 1,    1); row("Start Offset s", os_)
        return g

    # ── DISPLAY ──────────────────────────────────────────────────────

    def _tab_display(self):
        g = Gtk.Grid(); g.set_row_spacing(10); g.set_column_spacing(12)
        g.set_margin_top(8); g.set_margin_start(4)
        r = 0

        def row(lbl, w):
            nonlocal r
            l = Gtk.Label(label=lbl); l.set_halign(Gtk.Align.START)
            g.attach(l, 0, r, 1, 1); g.attach(w, 1, r, 1, 1); r += 1

        self._fill_combo = Gtk.DropDown.new_from_strings(FILL_MODES)
        idx = FILL_MODES.index(self.cell.fill_mode) if self.cell.fill_mode in FILL_MODES else 0
        self._fill_combo.set_selected(idx)
        row("Fill Mode", self._fill_combo)

        self._aspect_entry = Gtk.Entry()
        self._aspect_entry.set_text(self.cell.aspect_override)
        self._aspect_entry.set_placeholder_text("e.g. 16:9  (blank=auto)")
        row("Aspect Override", self._aspect_entry)

        rgba = Gdk.RGBA(); rgba.parse(self.cell.bg_color)
        self._color_btn = Gtk.ColorButton(rgba=rgba)
        row("Background Color", self._color_btn)

        self._sw_ontop   = Gtk.Switch(); self._sw_ontop.set_active(self.cell.always_on_top)
        row("Always On Top", self._sw_ontop)

        self._sw_enabled = Gtk.Switch(); self._sw_enabled.set_active(self.cell.enabled)
        row("Cell Enabled", self._sw_enabled)
        return g

    # ── APPLY ────────────────────────────────────────────────────────

    def apply_to_cell(self):
        self.cell.playlist        = self.get_playlist()
        self.cell.loop_cell       = self._sw_loop_cell.get_active()
        self.cell.loop_single     = self._sw_loop_single.get_active()
        self.cell.shuffle         = self._sw_shuffle.get_active()
        self.cell.muted           = self._sw_muted.get_active()
        self.cell.volume          = int(self._vol_adj.get_value())
        self.cell.speed           = round(self._speed_adj.get_value(), 2)
        self.cell.start_offset    = self._off_adj.get_value()
        self.cell.fill_mode       = FILL_MODES[self._fill_combo.get_selected()]
        self.cell.aspect_override = self._aspect_entry.get_text().strip()
        rgba = self._color_btn.get_rgba()
        self.cell.bg_color        = "#{:02x}{:02x}{:02x}".format(
            int(rgba.red*255), int(rgba.green*255), int(rgba.blue*255))
        self.cell.always_on_top   = self._sw_ontop.get_active()
        self.cell.enabled         = self._sw_enabled.get_active()


# ─────────────────────────────────────────────────────────────────────
#  MAIN APPLICATION  (Adw.Application)
# ─────────────────────────────────────────────────────────────────────

class VLCGridApp(Adw.Application):

    def __init__(self, initial_config: GridConfig):
        super().__init__(application_id="com.vlcgrid.manager",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.config = initial_config
        self.config.ensure_cells()
        self._cells: dict[tuple, VLCCell] = {}
        self._alive  = False
        self._tick_id = None
        self.connect("activate", self._activate)

    # ── WINDOW ───────────────────────────────────────────────────────

    def _activate(self, _):
        self.win = Adw.ApplicationWindow(application=self)
        self.win.set_title("VLC Grid Manager")
        self.win.set_default_size(1100, 720)
        self.win.connect("close-request", self._on_close)

        css = Gtk.CssProvider()
        css.load_from_data(APP_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar = self._build_sidebar()
        sidebar.add_css_class("sidebar")
        split.append(sidebar)
        content = self._build_content()
        content.set_hexpand(True)
        split.append(content)

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_title_widget(Gtk.Label(label="⬛ VLC GRID MANAGER v2"))
        for icon, tip, fn in [("💾", "Save config", self._save_config),
                               ("📂", "Load config", self._load_config)]:
            b = Gtk.Button(label=icon); b.set_tooltip_text(tip)
            b.connect("clicked", fn); hb.pack_end(b)
        tv.add_top_bar(hb)
        tv.set_content(split)
        self.win.set_content(tv)
        self.win.present()

    # ── SIDEBAR ──────────────────────────────────────────────────────

    def _build_sidebar(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_size_request(300, -1)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(10); inner.set_margin_bottom(10)
        inner.set_margin_start(10); inner.set_margin_end(10)

        def spin(v, lo, hi):
            a = Gtk.Adjustment(value=v, lower=lo, upper=hi, step_increment=1)
            return Gtk.SpinButton(adjustment=a, numeric=True)

        # ── Grid dims ──
        inner.append(self._sec("GRID DIMENSIONS"))
        dg = Gtk.Grid(); dg.set_row_spacing(6); dg.set_column_spacing(8)
        for i, (lbl, attr, lo, hi) in enumerate([
                ("Rows", "_rows_spin", 1, 20),
                ("Cols", "_cols_spin", 1, 20),
                ("Gap px", "_gap_spin", 0, 200)]):
            dg.attach(Gtk.Label(label=lbl), 0, i, 1, 1)
            w = spin(getattr(self.config, lbl.lower().replace(" px","").replace("cols","cols").replace("rows","rows").replace("gap px","gap")), lo, hi)
            setattr(self, attr, w)
            dg.attach(w, 1, i, 1, 1)
        # fix: re-assign properly
        self._rows_spin = spin(self.config.rows, 1, 20)
        self._cols_spin = spin(self.config.cols, 1, 20)
        self._gap_spin  = spin(self.config.gap,  0, 200)
        dg2 = Gtk.Grid(); dg2.set_row_spacing(6); dg2.set_column_spacing(8)
        for i,(l,w) in enumerate([("Rows",self._rows_spin),("Cols",self._cols_spin),("Gap px",self._gap_spin)]):
            lb=Gtk.Label(label=l); lb.set_halign(Gtk.Align.START)
            dg2.attach(lb,0,i,1,1); dg2.attach(w,1,i,1,1)
        inner.append(dg2)

        apply_b = Gtk.Button(label="Apply Grid Shape")
        apply_b.connect("clicked", self._apply_grid)
        inner.append(apply_b)

        rc = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for l, fn in [("+R", self._add_row),("-R", self._rem_row),("+C", self._add_col),("-C", self._rem_col)]:
            b = Gtk.Button(label=l); b.connect("clicked", fn); b.set_hexpand(True); rc.append(b)
        inner.append(rc)

        # ── Screen pos ──
        inner.append(self._sec("SCREEN / POSITION"))
        self._sx_spin = spin(self.config.screen_x, -9999, 9999)
        self._sy_spin = spin(self.config.screen_y, -9999, 9999)
        self._sw_spin = spin(self.config.screen_width,  100, 15360)
        self._sh_spin = spin(self.config.screen_height, 100, 8640)
        sg = Gtk.Grid(); sg.set_row_spacing(6); sg.set_column_spacing(8)
        for i,(l,w) in enumerate([("X",self._sx_spin),("Y",self._sy_spin),
                                   ("W",self._sw_spin),("H",self._sh_spin)]):
            lb=Gtk.Label(label=l); lb.set_halign(Gtk.Align.START)
            sg.attach(lb,0,i,1,1); sg.attach(w,1,i,1,1)
        inner.append(sg)
        db = Gtk.Button(label="Detect Primary Screen")
        db.connect("clicked", self._detect_screen); inner.append(db)

        # ── Global audio ──
        inner.append(self._sec("GLOBAL AUDIO"))
        self._g_mute_sw = Gtk.Switch(); self._g_mute_sw.set_active(self.config.global_mute)
        self._g_vol_adj = Gtk.Adjustment(value=self.config.global_volume,
                                          lower=0, upper=100, step_increment=1)
        g_vol_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self._g_vol_adj)
        g_vol_scale.set_digits(0); g_vol_scale.set_size_request(140,-1)
        ag = Gtk.Grid(); ag.set_row_spacing(6); ag.set_column_spacing(8)
        for i,(l,w) in enumerate([("Mute All",self._g_mute_sw),("Volume",g_vol_scale)]):
            lb=Gtk.Label(label=l); lb.set_halign(Gtk.Align.START)
            ag.attach(lb,0,i,1,1); ag.attach(w,1,i,1,1)
        inner.append(ag)
        gab = Gtk.Button(label="Apply to All Cells")
        gab.connect("clicked", self._apply_global_audio); inner.append(gab)

        # ── Batch load ──
        inner.append(self._sec("BATCH LOAD"))
        for l, fn in [
            ("📁 Folder → Cells (flat)",      lambda _: self._batch_load(False)),
            ("🗂 Folder → Cells (recursive)",  lambda _: self._batch_load(True)),
            ("🎬 One Video → All Cells",       self._batch_single),
            ("🔀 Pure Random → Cells",         self._batch_random_one),
        ]:
            b = Gtk.Button(label=l); b.connect("clicked", fn); inner.append(b)

        # ── Control ──
        inner.append(self._sec("CONTROL"))
        self._launch_btn = Gtk.Button(label="▶  LAUNCH GRID")
        self._launch_btn.add_css_class("launch-button")
        self._launch_btn.connect("clicked", self._launch); inner.append(self._launch_btn)

        self._stop_btn = Gtk.Button(label="■  STOP ALL")
        self._stop_btn.add_css_class("stop-button")
        self._stop_btn.connect("clicked", self._stop); inner.append(self._stop_btn)

        repo_btn = Gtk.Button(label="↔ Reposition Live")
        repo_btn.connect("clicked", self._reposition); inner.append(repo_btn)

        scroll.set_child(inner)
        outer.append(scroll)

        self._status_lbl = Gtk.Label(label="Ready.")
        self._status_lbl.add_css_class("stat-label")
        self._status_lbl.set_halign(Gtk.Align.START)
        self._status_lbl.set_margin_start(10); self._status_lbl.set_margin_bottom(6)
        self._status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        outer.append(self._status_lbl)
        return outer

    # ── CELL GRID ─────────────────────────────────────────────────────

    def _build_content(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._grid_w = Gtk.Grid()
        self._grid_w.set_row_spacing(6); self._grid_w.set_column_spacing(6)
        self._grid_w.set_margin_top(12); self._grid_w.set_margin_start(12)
        self._grid_w.set_margin_end(12); self._grid_w.set_margin_bottom(12)
        sw.set_child(self._grid_w)
        vbox.append(sw)
        self._rebuild_grid()
        return vbox

    def _rebuild_grid(self):
        c = self._grid_w.get_first_child()
        while c:
            n = c.get_next_sibling(); self._grid_w.remove(c); c = n

        for r in range(self.config.rows):
            for c in range(self.config.cols):
                cell = self.config.get_cell(r, c)
                if not cell:
                    cell = CellConfig(row=r, col=c); self.config.set_cell(cell)
                self._grid_w.attach(self._make_card(cell), c, r, 1, 1)

    def _make_card(self, cell: CellConfig):
        frame = Gtk.Frame(); frame.add_css_class("cell-card")
        frame.set_size_request(165, 115)

        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vb.set_margin_top(6); vb.set_margin_bottom(6)
        vb.set_margin_start(8); vb.set_margin_end(8)

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        coord = Gtk.Label(label=f"[{cell.row},{cell.col}]")
        coord.add_css_class("cell-label"); coord.set_halign(Gtk.Align.START)
        coord.set_hexpand(True); hdr.append(coord)
        esw = Gtk.Switch(); esw.set_active(cell.enabled); esw.set_valign(Gtk.Align.CENTER)
        esw.connect("state-set", lambda s, st, c=cell: self._toggle_enabled(s, st, c))
        hdr.append(esw); vb.append(hdr)

        n = len(cell.playlist)
        if n == 0:
            vl = Gtk.Label(label="— empty —"); vl.add_css_class("cell-empty-label")
        else:
            name = os.path.basename(cell.current_video()) or "?"
            vl = Gtk.Label(label=f"▶ {name}"); vl.add_css_class("cell-video-label")
        vl.set_halign(Gtk.Align.START)
        vl.set_ellipsize(Pango.EllipsizeMode.END); vl.set_max_width_chars(22)
        vb.append(vl)

        icons = ("🔀" if cell.shuffle else "🔁" if cell.loop_cell else "▶") + \
                (" 🔇" if cell.muted else f" 🔊{cell.volume}")
        pl_lbl = Gtk.Label(label=f"{n} file{'s' if n!=1 else ''} {icons}")
        pl_lbl.add_css_class("stat-label"); pl_lbl.set_halign(Gtk.Align.START)
        vb.append(pl_lbl)

        audio_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        mute_label = "🔇" if cell.muted else f"🔊{cell.volume}"
        mute_btn = Gtk.Button(label=mute_label)
        mute_btn.set_tooltip_text("Toggle mute")
        mute_btn.connect("clicked", lambda _, c=cell: self._toggle_cell_mute(c))
        audio_row.append(mute_btn)

        down_btn = Gtk.Button(label="−")
        down_btn.set_tooltip_text("Lower volume")
        down_btn.connect("clicked", lambda _, c=cell: self._adjust_cell_volume(c, -10))
        audio_row.append(down_btn)

        up_btn = Gtk.Button(label="+")
        up_btn.set_tooltip_text("Raise volume")
        up_btn.connect("clicked", lambda _, c=cell: self._adjust_cell_volume(c, 10))
        audio_row.append(up_btn)

        vb.append(audio_row)

        edit = Gtk.Button(label="✏ Edit Cell"); edit.set_margin_top(4)
        edit.connect("clicked", lambda _, c=cell: self._open_editor(c))
        vb.append(edit)
        frame.set_child(vb)
        return frame

    # ── CELL EDITOR ──────────────────────────────────────────────────

    def _open_editor(self, cell):
        dlg = CellEditorDialog(self.win, cell)
        dlg.connect("response", lambda d, r, c=cell: self._editor_response(d, r, c))
        dlg.present()

    def _editor_response(self, dlg, resp, cell):
        if resp == Gtk.ResponseType.OK:
            dlg.apply_to_cell()
            self.config.set_cell(cell)
            self._rebuild_grid()
            key = (cell.row, cell.col)
            if key in self._cells and cell.enabled:
                x,y,w,h = self._rect(cell.row, cell.col)
                self._cells[key].destroy()
                self._cells[key] = VLCCell(cell, x, y, w, h)
            self._status(f"Cell [{cell.row},{cell.col}] updated.")
        dlg.destroy()

    def _toggle_enabled(self, sw, state, cell):
        cell.enabled = state
        key = (cell.row, cell.col)
        if not state and key in self._cells:
            self._cells[key].destroy(); del self._cells[key]
        elif state and self._alive and key not in self._cells:
            x,y,w,h = self._rect(cell.row, cell.col)
            self._cells[key] = VLCCell(cell, x, y, w, h)

    def _toggle_cell_mute(self, cell):
        cell.muted = not cell.muted
        key = (cell.row, cell.col)
        if key in self._cells:
            self._cells[key].set_mute(cell.muted)
        self._rebuild_grid()
        self._status(f"Cell [{cell.row},{cell.col}] muted={cell.muted}")

    def _adjust_cell_volume(self, cell, delta):
        cell.volume = max(0, min(100, cell.volume + delta))
        key = (cell.row, cell.col)
        if key in self._cells:
            self._cells[key].set_volume(cell.volume)
        self._rebuild_grid()
        self._status(f"Cell [{cell.row},{cell.col}] volume={cell.volume}%")

    # ── GEOMETRY ────────────────────────────────────────────────────

    def _rect(self, r, c):
        ox = int(self._sx_spin.get_value()); oy = int(self._sy_spin.get_value())
        tw = int(self._sw_spin.get_value()); th = int(self._sh_spin.get_value())
        gap = int(self._gap_spin.get_value())
        cw = (tw - gap*(self.config.cols-1)) // self.config.cols
        ch = (th - gap*(self.config.rows-1)) // self.config.rows
        return ox+c*(cw+gap), oy+r*(ch+gap), cw, ch

    # ── GRID CONTROL ─────────────────────────────────────────────────

    def _apply_grid(self, _=None):
        self.config.rows = int(self._rows_spin.get_value())
        self.config.cols = int(self._cols_spin.get_value())
        self.config.gap  = int(self._gap_spin.get_value())
        self.config.ensure_cells(); self.config.prune_cells()
        self._rebuild_grid()
        if self._alive: self._reposition()
        self._status(f"Grid: {self.config.rows}×{self.config.cols}")

    def _add_row(self, _=None):
        self.config.rows += 1; self._rows_spin.set_value(self.config.rows); self._apply_grid()
    def _rem_row(self, _=None):
        if self.config.rows > 1: self.config.rows -= 1; self._rows_spin.set_value(self.config.rows); self._apply_grid()
    def _add_col(self, _=None):
        self.config.cols += 1; self._cols_spin.set_value(self.config.cols); self._apply_grid()
    def _rem_col(self, _=None):
        if self.config.cols > 1: self.config.cols -= 1; self._cols_spin.set_value(self.config.cols); self._apply_grid()

    def _detect_screen(self, _=None):
        disp = Gdk.Display.get_default()
        mon = disp.get_monitors().get_item(0)
        if mon:
            g = mon.get_geometry()
            self._sw_spin.set_value(g.width); self._sh_spin.set_value(g.height)
            self._sx_spin.set_value(g.x);     self._sy_spin.set_value(g.y)
            self._status(f"Screen: {g.width}×{g.height} @ ({g.x},{g.y})")

    def _apply_global_audio(self, _=None):
        m = self._g_mute_sw.get_active(); v = int(self._g_vol_adj.get_value())
        for cell in self.config.cells: cell.muted = m; cell.volume = v
        for vc in self._cells.values(): vc.set_mute(m); vc.set_volume(v)
        self._rebuild_grid()
        self._status(f"Global: muted={m} vol={v}")

    def _launch(self, _=None):
        self._stop()
        self.config.screen_x      = int(self._sx_spin.get_value())
        self.config.screen_y      = int(self._sy_spin.get_value())
        self.config.screen_width  = int(self._sw_spin.get_value())
        self.config.screen_height = int(self._sh_spin.get_value())
        self.config.gap           = int(self._gap_spin.get_value())
        n = 0
        for r in range(self.config.rows):
            for c in range(self.config.cols):
                cell = self.config.get_cell(r, c)
                if cell and cell.enabled:
                    x,y,w,h = self._rect(r, c)
                    self._cells[(r,c)] = VLCCell(cell, x, y, w, h)
                    n += 1
        self._alive = True
        self._tick_id = GLib.timeout_add(16, self._tick)
        self._status(f"Launched: {n} cells active.")

    def _stop(self, _=None):
        if self._tick_id: GLib.source_remove(self._tick_id); self._tick_id = None
        for vc in self._cells.values(): vc.destroy()
        self._cells.clear(); self._alive = False
        self._status("Grid stopped.")

    def _reposition(self, _=None):
        self.config.gap = int(self._gap_spin.get_value())
        for (r,c), vc in self._cells.items(): vc.update_geometry(*self._rect(r,c))

    def _tick(self):
        dead = [k for k,v in self._cells.items() if not v._alive]
        for k in dead: del self._cells[k]
        for vc in self._cells.values(): vc.step()
        return True

    # ── BATCH LOAD ───────────────────────────────────────────────────

    def _batch_load(self, recursive):
        dlg = Gtk.FileChooserDialog(title="Select Root Folder",
                                     action=Gtk.FileChooserAction.SELECT_FOLDER,
                                     transient_for=self.win)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Load",   Gtk.ResponseType.ACCEPT)

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                folder = d.get_file().get_path()
                videos = sorted(_scan_folder(folder, recursive=recursive))
                idx = 0
                for row in range(self.config.rows):
                    for col in range(self.config.cols):
                        if idx >= len(videos): break
                        cell = self.config.get_cell(row, col) or CellConfig(row=row, col=col)
                        cell.playlist = [videos[idx]]; cell.playlist_index = 0
                        self.config.set_cell(cell); idx += 1
                self._rebuild_grid()
                self._status(f"Loaded {idx} videos ({'recursive' if recursive else 'flat'}).")
            d.destroy()
        dlg.connect("response", resp); dlg.present()

    def _batch_single(self, _=None):
        dlg = Gtk.FileChooserDialog(title="Select Video for All Cells",
                                     action=Gtk.FileChooserAction.OPEN,
                                     transient_for=self.win)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Load",   Gtk.ResponseType.ACCEPT)
        ff = Gtk.FileFilter(); ff.set_name("Video files")
        for ext in VIDEO_EXTS: ff.add_pattern(f"*{ext}")
        dlg.add_filter(ff)

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                path = d.get_file().get_path()
                for cell in self.config.cells:
                    cell.playlist = [path]; cell.playlist_index = 0
                self._rebuild_grid()
                self._status(f"All cells: {os.path.basename(path)}")
            d.destroy()
        dlg.connect("response", resp); dlg.present()

    def _batch_random_one(self, _=None):
        """Pick one random video per cell from a folder (recursive)."""
        dlg = Gtk.FileChooserDialog(title="Select Folder for Random Assignment",
                                     action=Gtk.FileChooserAction.SELECT_FOLDER,
                                     transient_for=self.win)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Load",   Gtk.ResponseType.ACCEPT)

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                folder = d.get_file().get_path()
                videos = _scan_folder(folder, recursive=True)
                if not videos: self._status("No videos found."); d.destroy(); return
                for cell in self.config.cells:
                    cell.playlist = [random.choice(videos)]; cell.playlist_index = 0
                self._rebuild_grid()
                self._status(f"Random video assigned to each cell from {len(videos)} found.")
            d.destroy()
        dlg.connect("response", resp); dlg.present()

    # ── SAVE / LOAD ──────────────────────────────────────────────────

    def _save_config(self, _=None):
        dlg = Gtk.FileChooserDialog(title="Save Config",
                                     action=Gtk.FileChooserAction.SAVE,
                                     transient_for=self.win)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Save",   Gtk.ResponseType.ACCEPT)
        dlg.set_current_name("grid.json")

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                path = d.get_file().get_path()
                with open(path, "w") as f: json.dump(self.config.to_dict(), f, indent=2)
                self._status(f"Saved: {path}")
            d.destroy()
        dlg.connect("response", resp); dlg.present()

    def _load_config(self, _=None):
        dlg = Gtk.FileChooserDialog(title="Load Config",
                                     action=Gtk.FileChooserAction.OPEN,
                                     transient_for=self.win)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Load",   Gtk.ResponseType.ACCEPT)
        ff = Gtk.FileFilter(); ff.set_name("JSON"); ff.add_pattern("*.json")
        dlg.add_filter(ff)

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                path = d.get_file().get_path()
                with open(path) as f: raw = json.load(f)
                self.config = GridConfig.from_dict(raw)
                self._rows_spin.set_value(self.config.rows)
                self._cols_spin.set_value(self.config.cols)
                self._gap_spin.set_value(self.config.gap)
                self._sx_spin.set_value(self.config.screen_x)
                self._sy_spin.set_value(self.config.screen_y)
                self._sw_spin.set_value(self.config.screen_width)
                self._sh_spin.set_value(self.config.screen_height)
                self._rebuild_grid()
                self._status(f"Loaded: {path}")
            d.destroy()
        dlg.connect("response", resp); dlg.present()

    # ── HELPERS ──────────────────────────────────────────────────────

    def _sec(self, txt):
        l = Gtk.Label(label=txt); l.add_css_class("section-title")
        l.set_halign(Gtk.Align.START); l.set_margin_top(6); return l

    def _status(self, msg):
        GLib.idle_add(self._status_lbl.set_text, msg)

    def _on_close(self, _):
        self._stop(); return False


# ─────────────────────────────────────────────────────────────────────
#  UTILITY
# ─────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="VLC Borderless Grid Manager v2")
    ap.add_argument("--layout", metavar="RxC",
                    help="e.g. --layout 3x4")
    ap.add_argument("--load",   metavar="CONFIG.json")
    ap.add_argument("--fullscreen", action="store_true")
    args = ap.parse_args()

    if args.load:
        with open(args.load) as f: config = GridConfig.from_dict(json.load(f))
    else:
        config = GridConfig()
        if args.layout:
            try:
                r, c = args.layout.lower().split("x")
                config.rows, config.cols = int(r), int(c)
            except Exception:
                print(f"Bad layout: {args.layout}"); sys.exit(1)

    app = VLCGridApp(config)

    if args.fullscreen:
        def after():
            disp = Gdk.Display.get_default()
            mon = disp.get_monitors().get_item(0)
            if mon:
                g = mon.get_geometry()
                config.screen_width = g.width; config.screen_height = g.height
                config.screen_x = g.x;         config.screen_y = g.y
        GLib.idle_add(after)

    app.run(sys.argv[:1])


if __name__ == "__main__":
    main()
