# VLC Idiotbox

Borderless VLC grid launcher for running multiple videos in a tiled layout.

This repository contains two app variants and one small cross-platform launcher:

- [`vlc_grid.py`](./vlc_grid.py) for the Tk-based Windows-friendly control panel
- [`vlc_grid (1).py`](./vlc_grid%20(1).py) for the GTK/Adwaita control panel on Linux, with a Tk fallback
- [`vlc_grid_combined.py`](./vlc_grid_combined.py) to pick the right launcher automatically

## Features

- Borderless VLC windows arranged in a configurable grid
- Per-cell video selection
- Save and load grid configs as JSON
- Bulk fill cells from a folder
- Pure random per-cell assignment from a selected folder
- Per-cell mute, volume, and loop controls
- Thumbnail previews for video files

## Requirements

- Python 3.11+ recommended
- `python-vlc`
- VLC installed locally
- `Pillow`
- `tkinter` for the Windows/Tk UI
- On Linux GTK mode: `PyGObject`, GTK 4, Adwaita, and Cairo bindings

Example Linux packages:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 vlc
```

Install Python dependencies:

```bash
pip install python-vlc Pillow
```

## Usage

Launch the platform-aware entry point:

```bash
python vlc_grid_combined.py
```

Or run a specific UI:

```bash
python vlc_grid.py
python "vlc_grid (1).py"
```

Optional arguments supported by both apps:

- `--layout RxC` to start with a custom grid size, for example `--layout 2x3`
- `--load CONFIG.json` to restore a saved session
- `--fullscreen` to use the current display size

## Workflow

1. Set the grid size and screen bounds.
2. Fill cells individually or use one of the folder import actions.
3. Adjust per-cell audio and loop settings.
4. Launch the grid.
5. Save the layout when you want to restore it later.

## Notes

- Thumbnails are cached in the system temp directory under `vlc_idiotbox_thumbs`.
- The repo currently has separate Windows/Tk and Linux/GTK implementations because the UI stacks differ by platform.
- If GTK libraries are missing on Linux, the GTK launcher falls back to the Tk version.
