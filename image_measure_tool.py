#!/usr/bin/env python3
"""
Image Measure Tool
===================

A Tkinter desktop app for measuring things in a photo, organized into tabs
so you can have several photos/projects open at once.

- Draw lines in three colors that map to real-world axes:
    Red   = X
    Green = Y
    Blue  = Z
- Assign a known real-world length to any line. Every other line drawn
  in that same color automatically gets a computed real-world length,
  based on that axis's pixels-per-unit scale.
- Compare any two lines directly: pick a line with a known length and
  a second line, and the tool computes the second line's real-world
  length from the pixel-length ratio (independent of axis calibration).
- Draw a line that is forced parallel to an existing line (lock the
  direction, drag only changes position/length) -- handy for measuring
  parallel edges (e.g. opposite sides of a box) from a single photo.
- Each open photo lives in its own tab (with a close button), so you can
  work on several images/projects side by side.
- Save/load a project as a single .imt file next to the image (or anywhere
  you like) -- it carries the image path AND a copy of the image itself, so
  it still opens correctly even if the photo gets moved, renamed, or is on
  a different computer. Export all measurements to CSV separately.
  Separately from project files, the whole window -- every open tab, even
  ones you never explicitly saved -- is remembered automatically and
  restored the next time you launch the app (see SESSION_PATH below).
- Scroll to zoom in on your cursor, hold the middle mouse button to pan,
  and drag an image OR project (.imt) file onto a tab to open it.

Requires: Python 3.8+, Pillow (pip install pillow). Tkinter ships with
the standard Windows/macOS Python installers; on Linux install your
distro's python3-tk package if it's missing. Drag-and-drop needs the
optional tkinterdnd2 package (pip install tkinterdnd2) -- without it,
everything else still works, you just use File > Open Image instead.

Run:
    python image_measure_tool.py
"""

import base64
import io
import json
import math
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk, ImageOps
except ImportError:
    raise SystemExit(
        "Pillow is required. Install it with:\n\n    pip install pillow\n"
    )

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

APP_NAME = "Image Measure Tool"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")

AXIS_COLORS = {
    "red": {"axis": "X", "hex": "#e53935", "select_hex": "#ff8a80"},
    "green": {"axis": "Y", "hex": "#43a047", "select_hex": "#b9f6ca"},
    "blue": {"axis": "Z", "hex": "#1e88e5", "select_hex": "#82b1ff"},
}
DEFAULT_COLOR = "red"
UNIT_CHOICES = ["mm", "cm", "m", "in", "ft", "px"]
DEFAULT_UNIT = "mm"
HANDLE_RADIUS = 5
HIT_TOLERANCE = 6  # pixels, in canvas/screen space
CLICK_MOVE_THRESHOLD = 4  # canvas pixels of movement that turns a click into a drag
PROJECT_EXT = ".imt"
# .imt.json was this app's project extension before projects started embedding
# a copy of the image -- still openable, just no longer the default Save name.
LEGACY_PROJECT_EXT = ".imt.json"

# --------------------------------------------------------------- session ---
# Per-project files (.imt, saved with File > Save Project) are the portable,
# explicit save format -- one file per project, carrying a copy of the image
# itself (not just its path) so the project still opens correctly even if
# the photo gets moved/renamed/shared to another computer.
#
# SESSION_PATH is a *separate*, small file the app writes on its own every
# time it closes: which tabs were open, which image/lines each one had (even
# if you never hit "Save Project"), and where you were zoomed/panned to. On
# the next launch it's read back automatically to restore the window to
# exactly how you left it. It intentionally lives in a per-user app-data
# folder rather than next to the script, so it does NOT get swept up by
# OneDrive/Git and each computer keeps its own "last state" independently.


def _session_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ImageMeasureTool")


SESSION_PATH = os.path.join(_session_dir(), "session.json")
SESSION_AUTOSAVE_MS = 60_000  # also save periodically, in case of a crash


def _embedded_bytes_from_project_file(path):
    """Best-effort peek into a .imt project file for just its embedded
    image copy, without going through the full ProjectTab.load_project_data
    flow (used to rescue a session-restored tab whose image has moved, when
    that tab was also ever saved as a project). Returns None on any problem
    -- this is a fallback, never something the caller should have to
    handle exceptions for."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        b64 = data.get("image_data")
        return base64.b64decode(b64) if b64 else None
    except Exception:
        return None


def dist(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])


def fmt_len(value, unit, decimals=3):
    if value is None:
        return "?"
    return f"{value:.{decimals}g} {unit}"


def _make_x_icon(color, size=9, thickness=1, pad_left=5):
    """A small transparent PhotoImage with an 'X' drawn in it, used for the
    close button on each tab. Built by hand (no external icon file) so the
    close button never depends on a bundled asset. The ttk layout engine
    doesn't reliably honor a -padx on a custom image element (nested
    -children layouts reject it), so the gap between the tab label and the
    X is baked into the image itself via pad_left instead."""
    width = size + pad_left
    img = tk.PhotoImage(width=width, height=size)
    img.blank()  # fully transparent
    last = size - 1
    for i in range(size):
        for off in range(-thickness, thickness + 1):
            j = i + off
            if 0 <= j < size:
                img.put(color, (i + pad_left, j))
                img.put(color, (i + pad_left, last - j))
    return img


class Line:
    """A single measured line, stored in IMAGE pixel coordinates."""

    _next_id = 1

    def __init__(self, color, x1, y1, x2, y2, known_length=None, unit=None,
                 parallel_to=None):
        self.id = Line._next_id
        Line._next_id += 1
        self.color = color            # 'red' | 'green' | 'blue'
        self.x1, self.y1 = x1, y1
        self.x2, self.y2 = x2, y2
        self.known_length = known_length   # real-world length, or None
        self.unit = unit
        self.parallel_to = parallel_to     # id of reference line, or None
        # canvas item ids, filled in by the view
        self.canvas_line_id = None
        self.canvas_label_id = None

    def pixel_length(self):
        return dist((self.x1, self.y1), (self.x2, self.y2))

    def angle(self):
        return math.atan2(self.y2 - self.y1, self.x2 - self.x1)

    def midpoint(self):
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def to_dict(self):
        return {
            "id": self.id, "color": self.color,
            "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2,
            "known_length": self.known_length, "unit": self.unit,
            "parallel_to": self.parallel_to,
        }

    @classmethod
    def from_dict(cls, d):
        ln = cls(d["color"], d["x1"], d["y1"], d["x2"], d["y2"],
                  d.get("known_length"), d.get("unit"), d.get("parallel_to"))
        ln.id = d["id"]
        Line._next_id = max(Line._next_id, ln.id + 1)
        return ln


# ---------------------------------------------------------------- tabs -----
class ClosableNotebook(ttk.Notebook):
    """A ttk.Notebook whose tabs each draw a small 'x' close button.

    Clicking the x fires the virtual event <<NotebookTabClosed>>; the index
    of the tab that was clicked is left on self.last_closed_index. This
    class only detects the click -- it never removes a tab itself, so the
    owner (App) can decide whether to confirm first (e.g. unsaved work).

    If anything about the underlying ttk theme keeps the custom close
    element from being created, we fall back to plain tabs and let the
    owner's other close affordances (menu item, Ctrl+W, middle-click) cover
    it -- see App._build_menu / ProjectTab bindings.
    """

    _style_name = "Closable.TNotebook"
    _style_ready = False
    _has_close_element = False

    def __init__(self, master, **kwargs):
        self._ensure_style()
        if self._has_close_element:
            kwargs["style"] = self._style_name
        super().__init__(master, **kwargs)
        self._pressed_index = None
        self.last_closed_index = None
        if self._has_close_element:
            self.bind("<ButtonPress-1>", self._on_press, add=True)
            self.bind("<ButtonRelease-1>", self._on_release, add=True)
        # Fallback / extra convenience that works regardless of theme support:
        # middle-click (or right-click) a tab to close it.
        self.bind("<ButtonPress-2>", self._on_middle_click, add=True)

    def _on_middle_click(self, event):
        try:
            index = self.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        self.last_closed_index = index
        self.event_generate("<<NotebookTabClosed>>")

    def _on_press(self, event):
        element = self.identify(event.x, event.y)
        if "close" in element:
            index = self.index(f"@{event.x},{event.y}")
            self.state(["pressed"])
            self._pressed_index = index
            return "break"

    def _on_release(self, event):
        if not self.instate(["pressed"]):
            return
        self.state(["!pressed"])
        element = self.identify(event.x, event.y)
        index = self._pressed_index
        self._pressed_index = None
        if "close" not in element or index is None:
            return
        if self.index(f"@{event.x},{event.y}") == index:
            self.last_closed_index = index
            self.event_generate("<<NotebookTabClosed>>")

    @classmethod
    def _ensure_style(cls):
        if cls._style_ready:
            return
        cls._style_ready = True
        try:
            style = ttk.Style()
            cls._close_images = (
                _make_x_icon("#8a8a8a"),   # normal
                _make_x_icon("#e53935"),   # hovered
                _make_x_icon("#8a1c1c"),   # pressed
            )
            style.element_create(
                "close", "image", cls._close_images[0],
                ("pressed", "!disabled", cls._close_images[2]),
                ("active", "!disabled", cls._close_images[1]),
                border=6, sticky="")
            style.layout(cls._style_name, [("Notebook.client", {"sticky": "nswe"})])
            style.layout(cls._style_name + ".Tab", [
                ("Notebook.tab", {"sticky": "nswe", "children": [
                    ("Notebook.padding", {"sticky": "nswe", "children": [
                        ("Notebook.focus", {"sticky": "nswe", "children": [
                            ("Notebook.label", {"side": "left", "sticky": ""}),
                            ("close", {"side": "left", "sticky": ""}),
                        ]}),
                    ]}),
                ]}),
            ])
            cls._has_close_element = True
        except tk.TclError:
            cls._has_close_element = False


class ProjectTab(ttk.Frame):
    """One open project: an image, its measured lines, and its own view
    (zoom/pan), tree list and known-length panel. A tab with no image
    loaded yet is 'blank' -- opening an image or a project file re-uses a
    blank tab instead of piling up empty ones.

    Interaction preferences that aren't project data -- the active
    draw color and draw/select mode -- are shared across tabs and live on
    the App instance instead (self.app.current_color / self.app.mode).
    """

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        self.image_path = None
        self.project_path = None
        self.pil_image = None            # original, full-res PIL image
        self.pyramid = []                 # [(factor, PIL.Image), ...], full-res first
        self.tk_image = None              # scaled PhotoImage currently shown
        self.scale = 1.0                  # canvas px per image px
        self.view_x = 0.0                 # image-space coords shown at canvas (0, 0)
        self.view_y = 0.0
        self.rotation_turns = 0           # manual 90 deg-clockwise turns applied (0-3)
        self.image_bytes_cache = None     # raw bytes of the loaded image file, for embedding
                                            # a copy of it into the next Save Project
        self.dirty = False                # unsaved changes since last Save Project / load

        self.pan_start = None             # (canvas_x, canvas_y, view_x, view_y) mid-drag
        self.lines = []                   # list[Line]
        self.selected_line_ids = []       # for compare / delete / calibrate
        self.drag_start_img = None        # (ix, iy) anchor for a hold-and-drag line
        self.drag_temp_id = None          # canvas id of the dashed preview line
        self.dragging_vertex = None        # (Line, endpoint_index) while moving a vertex
        self.press_canvas = None          # (x, y) where the current press started
        self.press_moved = False          # did the mouse move past the click threshold?
        self.click_draw_start = None      # (ix, iy) start of a click-to-click pending line
        self.known_length_edit_snapshot = None  # (length_str, unit_str) before the current
                                                  # known-length edit, for Escape to revert to
        self._suppress_next_focus_snapshot = False  # see maybe_start_length_edit /
                                                       # _capture_length_edit_snapshot

        self._build_body()

    # ---------------------------------------------------------- UI setup
    def _build_body(self):
        # side panel: known-length field + line list (on the LEFT)
        side = ttk.Frame(self, padding=6, width=300)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        # canvas (fills the rest of the tab). No scrollbars -- panning is
        # done by dragging with the middle mouse button, and only the
        # visible region is ever rendered (see _render_image), which is
        # what keeps zooming in on a big photo from lagging.
        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg="#2b2b2b", cursor="crosshair",
                                 highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<Motion>", self.on_canvas_hover)         # vertex hover cursor
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)      # Windows/macOS
        self.canvas.bind("<Button-4>", lambda e: self.zoom(1.1, e.x, e.y))  # Linux scroll up
        self.canvas.bind("<Button-5>", lambda e: self.zoom(0.9, e.x, e.y))  # Linux scroll down
        self.canvas.bind("<ButtonPress-2>", self.on_pan_start)     # middle-mouse pan
        self.canvas.bind("<B2-Motion>", self.on_pan_drag)
        self.canvas.bind("<ButtonRelease-2>", self.on_pan_end)
        self.canvas.bind("<Configure>", self.on_canvas_resize)

        self.dnd_active = False
        if DND_AVAILABLE:
            try:
                self.canvas.drop_target_register(DND_FILES)
                self.canvas.dnd_bind("<<Drop>>", self.on_drop_file)
                self.dnd_active = True
            except tk.TclError:
                pass  # root wasn't a TkinterDnD.Tk() -- drag-and-drop stays off

        # --- known-length entry, always visible, never a popup ---
        known_frame = ttk.LabelFrame(side, text="Known length of selected line", padding=6)
        known_frame.pack(fill="x", pady=(0, 8))

        self.selection_label = ttk.Label(known_frame, text="No line selected",
                                          foreground="#555")
        self.selection_label.pack(anchor="w")

        entry_row = ttk.Frame(known_frame)
        entry_row.pack(fill="x", pady=(4, 0))
        self.known_length_var = tk.StringVar(value="")
        self.known_length_entry = ttk.Entry(entry_row, textvariable=self.known_length_var,
                                             width=12, state="disabled")
        self.known_length_entry.pack(side="left")
        self.known_length_entry.bind("<Return>", self.commit_known_length)
        self.known_length_entry.bind("<FocusOut>", self.commit_known_length)
        self.known_length_entry.bind("<FocusIn>", self._capture_length_edit_snapshot)

        self.known_unit_var = tk.StringVar(value=DEFAULT_UNIT)
        self.known_unit_box = ttk.Combobox(
            entry_row, textvariable=self.known_unit_var, width=6, state="disabled",
            values=UNIT_CHOICES)
        self.known_unit_box.pack(side="left", padx=(4, 0))
        self.known_unit_box.bind("<<ComboboxSelected>>", self.commit_known_length)
        self.known_unit_box.bind("<Return>", self.commit_known_length)
        self.known_unit_box.bind("<FocusIn>", self._capture_length_edit_snapshot)

        ttk.Label(known_frame, text="Type a value and press Enter. Leave blank for "
                                     "no known length -- nothing is required.",
                  foreground="#666", wraplength=270, justify="left").pack(
            anchor="w", pady=(4, 0))

        ttk.Label(side, text="Measured lines", font=("", 10, "bold")).pack(anchor="w")
        columns = ("color", "axis", "px", "real", "calib")
        self.tree = ttk.Treeview(side, columns=columns, show="headings", height=20,
                                  selectmode="extended")
        for col, label, width in [
            ("color", "Color", 50), ("axis", "Axis", 36), ("px", "Pixels", 58),
            ("real", "Real length", 90), ("calib", "Known?", 50),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self.focus_known_length())

        axis_frame = ttk.LabelFrame(side, text="Axis calibration", padding=6)
        axis_frame.pack(fill="x", pady=8)
        self.axis_labels = {}
        for color, meta in AXIS_COLORS.items():
            lbl = ttk.Label(axis_frame, text=f"{meta['axis']} ({color}): not calibrated")
            lbl.pack(anchor="w")
            self.axis_labels[color] = lbl

        # The empty-tab hint is drawn as canvas TEXT, not a separate widget
        # placed on top of the canvas. A widget placed there (e.g. a plain
        # ttk.Label via .place()) sits right where you'd naturally drop a
        # file onto a blank tab, and isn't itself a registered drop target --
        # so the drop lands on the label and is silently swallowed instead
        # of reaching the canvas's <<Drop>> binding below.

    def is_blank(self):
        return self.pil_image is None

    def display_name(self):
        return os.path.basename(self.image_path) if self.image_path else "Untitled"

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self.app.update_tab_title(self)

    def _clear_placeholder(self):
        self.canvas.delete("placeholder")

    def _show_placeholder(self):
        self.canvas.delete("all")
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        text = "Open an image or project, or drag one onto this tab."
        self.canvas.create_text(cw / 2, ch / 2, text=text, fill="#aaaaaa",
                                 justify="center", width=max(200, cw - 40),
                                 tags=("placeholder",))

    # ------------------------------------------------------------- image
    def on_drop_file(self, event):
        # event.data may be one path, or several space-separated and
        # brace-quoted (e.g. "{C:/a b/img.jpg} {C:/other.png}") -- splitlist
        # understands that Tcl-list quoting.
        paths = self.canvas.tk.splitlist(event.data)
        for path in paths:
            lower = path.lower()
            if lower.endswith(PROJECT_EXT) or lower.endswith(LEGACY_PROJECT_EXT):
                self.app.handle_dropped_project(self, path)
                return
            if lower.endswith(IMAGE_EXTS):
                self.app.handle_dropped_file(self, path)
                return
        messagebox.showinfo("Not recognized",
                             "Drop an image file (jpg/png/bmp/tif/webp) or a project "
                             f"file ({PROJECT_EXT}).")

    def _apply_loaded_bytes(self, raw_bytes, rotation_turns=0):
        """Build self.pil_image (+ pyramid) from raw image FILE bytes --
        i.e. exactly what's on disk, not decoded pixels -- applying EXIF
        auto-rotation and any additional manual rotation_turns on top.
        Shared by loading a real file from disk and loading the copy
        embedded in a .imt project file, so both behave identically and
        both leave self.image_bytes_cache set for the next Save Project to
        re-embed (unchanged, so re-saving never re-compresses the photo)."""
        img = Image.open(io.BytesIO(raw_bytes))
        # Cameras/phones store landscape pixel data plus an EXIF
        # "Orientation" tag saying how to rotate/flip it for display.
        # Windows (and every normal photo viewer) reads that tag; PIL's
        # raw pixels don't, which is why an image can come in sideways
        # here even though Explorer shows it upright. This applies the
        # same correction Windows does, then discards the tag so it
        # isn't applied twice.
        img = ImageOps.exif_transpose(img)
        img.load()
        img = img.convert("RGB")
        for _ in range(rotation_turns % 4):
            img = img.transpose(Image.ROTATE_270)  # matches rotate_image()'s direction
        self.pil_image = img
        self.rotation_turns = rotation_turns % 4
        self.image_bytes_cache = raw_bytes
        self._build_pyramid()

    def _reset_after_image_load(self, display_path):
        """Common bookkeeping after ANY fresh image load (from a path or
        from an embedded copy) -- new image, so any previous lines/project
        association no longer apply."""
        self.image_path = display_path
        self.dragging_vertex = None
        self.drag_start_img = None
        self.drag_temp_id = None
        self.click_draw_start = None
        self.lines = []
        self.selected_line_ids = []
        self.project_path = None
        self.dirty = False
        self._clear_placeholder()
        self.app.update_tab_title(self)

    def load_image_path(self, path, rotation_turns=0, silent=False):
        """Load a fresh image straight from disk (no project data) into
        this tab. Returns True on success. silent=True skips dialogs/
        status/redraw -- used while restoring a saved session or loading a
        project, where the caller applies its own lines/view afterward."""
        try:
            with open(path, "rb") as f:
                raw = f.read()
            self._apply_loaded_bytes(raw, rotation_turns=rotation_turns)
        except Exception as exc:
            if not silent:
                messagebox.showerror("Could not open image", str(exc))
            return False
        self._reset_after_image_load(path)
        if not silent:
            self.fit_to_window()
            self.redraw()
            self.app.set_status(f"Loaded {os.path.basename(path)} "
                                 f"({self.pil_image.width}x{self.pil_image.height}px)")
        return True

    def load_embedded_image(self, raw_bytes, display_path, rotation_turns=0):
        """Load an image from bytes embedded in a .imt project file (used
        when the original file can no longer be found at its saved path).
        Always 'silent' in the load_image_path sense -- the caller
        (load_project_data) does its own fit/redraw/status afterward."""
        try:
            self._apply_loaded_bytes(raw_bytes, rotation_turns=rotation_turns)
        except Exception as exc:
            messagebox.showerror("Could not read the image embedded in this project", str(exc))
            return False
        self._reset_after_image_load(display_path)
        return True

    def fit_to_window(self):
        if not self.pil_image:
            return
        self.canvas.update_idletasks()
        cw = max(self.canvas.winfo_width(), 400)
        ch = max(self.canvas.winfo_height(), 300)
        iw, ih = self.pil_image.width, self.pil_image.height
        self.scale = min(cw / iw, ch / ih, 1.0) or 1.0
        # center the image in the canvas
        self.view_x = iw / 2 - (cw / 2) / self.scale
        self.view_y = ih / 2 - (ch / 2) / self.scale
        self._render_image()

    def zoom(self, factor, canvas_x=None, canvas_y=None):
        """Zoom in/out, keeping the image point under (canvas_x, canvas_y)
        fixed on screen -- i.e. zoom towards the cursor. Defaults to the
        canvas center when no cursor position is given (toolbar buttons)."""
        if not self.pil_image:
            return
        if canvas_x is None:
            canvas_x = self.canvas.winfo_width() / 2
        if canvas_y is None:
            canvas_y = self.canvas.winfo_height() / 2
        ix, iy = self.canvas_to_img(canvas_x, canvas_y)
        new_scale = max(0.02, min(16.0, self.scale * factor))
        if new_scale == self.scale:
            return
        self.scale = new_scale
        self.view_x = ix - canvas_x / self.scale
        self.view_y = iy - canvas_y / self.scale
        self._render_image()

    def on_mousewheel(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        self.zoom(factor, event.x, event.y)

    def on_pan_start(self, event):
        if not self.pil_image:
            return
        self.pan_start = (event.x, event.y, self.view_x, self.view_y)

    def on_pan_drag(self, event):
        if self.pan_start is None:
            return
        sx, sy, start_vx, start_vy = self.pan_start
        self.view_x = start_vx - (event.x - sx) / self.scale
        self.view_y = start_vy - (event.y - sy) / self.scale
        self._render_image()

    def on_pan_end(self, event):
        self.pan_start = None

    def on_canvas_resize(self, event):
        if self.pil_image:
            self._render_image()
        else:
            self._show_placeholder()

    def rotate_image(self):
        """Rotate the loaded image 90 degrees clockwise, keeping every
        existing line attached to the same spot on the picture."""
        if not self.pil_image:
            messagebox.showinfo("No image", "Open an image first.")
            return
        self.cancel_click_draw()
        old_h = self.pil_image.height
        self.pil_image = self.pil_image.transpose(Image.ROTATE_270)  # 90 deg clockwise
        self.rotation_turns = (self.rotation_turns + 1) % 4
        self._build_pyramid()
        for ln in self.lines:
            ln.x1, ln.y1 = old_h - ln.y1, ln.x1
            ln.x2, ln.y2 = old_h - ln.y2, ln.x2
        self.fit_to_window()
        self.redraw()
        self.mark_dirty()
        self.app.set_status("Rotated image 90 degrees.")

    def _build_pyramid(self):
        """Precompute progressively half-sized versions of the loaded image.
        Zoomed way out, cropping straight from the full-resolution original
        and downsizing it (even with a fast filter) still costs time
        proportional to the WHOLE image's pixel count, on every single
        zoom/pan step -- that's what caused the lag when zooming out on a
        large photo. Picking the smallest pyramid level that's still
        sharp enough for the current zoom keeps the per-frame cost bounded
        by the canvas size instead, at any zoom level."""
        levels = [(1.0, self.pil_image)]
        img = self.pil_image
        factor = 1.0
        while img.width > 64 and img.height > 64 and len(levels) < 14:
            img = img.reduce(2)  # fast box-filter halving
            factor /= 2.0
            levels.append((factor, img))
        self.pyramid = levels

    def _pick_pyramid_level(self, scale):
        """The lowest-resolution pyramid level that's still >= scale (i.e.
        the smallest source image that doesn't need to be upscaled)."""
        for factor, img in reversed(self.pyramid):
            if factor >= scale:
                return factor, img
        return self.pyramid[0]

    def _render_image(self):
        """Draw only the part of the image that's actually visible, resized
        to fit the canvas, using whichever pyramid level is just sharp
        enough for the current zoom. However far you're zoomed in or out,
        this never resizes more source pixels than the canvas itself has
        room for -- that's what keeps zooming smooth regardless of the
        original photo's resolution."""
        if not self.pil_image:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        iw, ih = self.pil_image.width, self.pil_image.height

        # visible region, in ORIGINAL image-space coordinates
        vx0, vy0 = self.view_x, self.view_y
        vx1, vy1 = self.view_x + cw / self.scale, self.view_y + ch / self.scale

        # clamp to the actual image bounds
        cx0, cy0 = max(vx0, 0), max(vy0, 0)
        cx1, cy1 = min(vx1, iw), min(vy1, ih)

        canvas_img = Image.new("RGB", (cw, ch), (43, 43, 43))
        if cx1 > cx0 and cy1 > cy0:
            factor, src_img = self._pick_pyramid_level(self.scale)
            # same crop rect, translated into that pyramid level's own pixels
            lx0, ly0 = max(0, cx0 * factor), max(0, cy0 * factor)
            lx1 = min(src_img.width, cx1 * factor)
            ly1 = min(src_img.height, cy1 * factor)
            crop = src_img.crop((int(lx0), int(ly0), math.ceil(lx1), math.ceil(ly1)))
            out_w = max(1, round((cx1 - cx0) * self.scale))
            out_h = max(1, round((cy1 - cy0) * self.scale))
            resized = crop.resize((out_w, out_h), Image.LANCZOS)
            paste_x = round((cx0 - self.view_x) * self.scale)
            paste_y = round((cy0 - self.view_y) * self.scale)
            canvas_img.paste(resized, (paste_x, paste_y))

        self.tk_image = ImageTk.PhotoImage(canvas_img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image, tags=("bg",))
        self.redraw()

    # ------------------------------------------------------- coord helpers
    def img_to_canvas(self, x, y):
        return (x - self.view_x) * self.scale, (y - self.view_y) * self.scale

    def canvas_to_img(self, x, y):
        return x / self.scale + self.view_x, y / self.scale + self.view_y

    def canvas_event_to_img(self, event):
        return self.canvas_to_img(event.x, event.y)

    # -------------------------------------------------------- axis scale
    def axis_scale(self, color):
        """Return (units_per_pixel, unit) for a color axis, or (None, None)."""
        for ln in reversed(self.lines):
            if ln.color == color and ln.known_length and ln.pixel_length() > 0:
                return ln.known_length / ln.pixel_length(), ln.unit
        return None, None

    def computed_length(self, line):
        """Real-world length for a line: its own known length if set,
        otherwise derived from its axis's calibration."""
        if line.known_length:
            return line.known_length, line.unit
        upp, unit = self.axis_scale(line.color)
        if upp is None:
            return None, None
        return line.pixel_length() * upp, unit

    # ------------------------------------------------------------ drawing
    def find_line_near(self, ix, iy, tolerance_img):
        best, best_d = None, tolerance_img
        for ln in self.lines:
            d = self._point_segment_distance((ix, iy), (ln.x1, ln.y1), (ln.x2, ln.y2))
            if d <= best_d:
                best, best_d = ln, d
        return best

    def find_vertex_near(self, ix, iy, tolerance_img):
        """Return (Line, endpoint_index) for the closest line endpoint
        within tolerance, endpoint_index is 1 or 2. None if nothing's close."""
        best, best_d = None, tolerance_img
        for ln in self.lines:
            for idx, (vx, vy) in ((1, (ln.x1, ln.y1)), (2, (ln.x2, ln.y2))):
                d = dist((ix, iy), (vx, vy))
                if d <= best_d:
                    best, best_d = (ln, idx), d
        return best

    @staticmethod
    def _point_segment_distance(p, a, b):
        ax, ay = a
        bx, by = b
        px, py = p
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        if length2 == 0:
            return dist(p, a)
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / length2))
        proj = (ax + t * dx, ay + t * dy)
        return dist(p, proj)

    def _parallel_reference(self):
        """The line a Shift-constrained drag should run parallel to: the
        single selected line if there is one, otherwise the last line
        drawn. None if there's nothing to reference yet."""
        if len(self.selected_line_ids) == 1:
            ln = self._line_by_id(self.selected_line_ids[0])
            if ln:
                return ln
        return self.lines[-1] if self.lines else None

    def _constrain_to_reference(self, ix1, iy1, ix2, iy2, ref):
        """Project (ix2, iy2) onto the line through (ix1, iy1) running in
        ref's direction, so the new segment is parallel to ref."""
        ang = ref.angle()
        dirx, diry = math.cos(ang), math.sin(ang)
        vx, vy = ix2 - ix1, iy2 - iy1
        proj_len = vx * dirx + vy * diry
        return ix1 + proj_len * dirx, iy1 + proj_len * diry

    def on_canvas_press(self, event):
        if not self.pil_image:
            return

        # A line started by a short click is waiting for its closing click --
        # this press is that closing click, whatever mode we're in.
        if self.click_draw_start is not None:
            ix1, iy1 = self.click_draw_start
            self.click_draw_start = None
            ix2, iy2 = self.canvas_event_to_img(event)
            self._finalize_line(ix1, iy1, ix2, iy2, bool(event.state & 0x0001))
            return

        ix, iy = self.canvas_event_to_img(event)
        tol_img = HIT_TOLERANCE / self.scale

        if self.app.mode.get() == "select":
            ln = self.find_line_near(ix, iy, tol_img)
            if ln:
                self.select_line(ln.id, additive=bool(event.state & 0x0001))  # shift
            else:
                self.select_line(None)
            return

        # draw mode: hovering an endpoint either moves it (same color as the
        # one you've got selected to draw with) or snaps a new line's start
        # to it (different color -- chaining a segment onto another axis).
        self.dragging_vertex = None
        self.press_canvas = (event.x, event.y)
        self.press_moved = False
        hit = self.find_vertex_near(ix, iy, tol_img)
        if hit:
            vline, vidx = hit
            if vline.color == self.app.current_color.get():
                self.dragging_vertex = (vline, vidx)
                self.drag_start_img = None
                self.select_line(vline.id)
                return
            vx, vy = (vline.x1, vline.y1) if vidx == 1 else (vline.x2, vline.y2)
            self.drag_start_img = (vx, vy)
            self.drag_temp_id = None
            return

        # Store the start point in IMAGE space, not canvas space -- if you
        # zoom or middle-mouse-pan while still holding the button down, the
        # canvas pixel under your original click no longer corresponds to
        # the same spot on the photo. Re-deriving the canvas position fresh
        # from the image-space anchor on every drag/release call (below)
        # keeps the line's start point pinned to the actual photo content
        # instead of stretching to wherever that canvas pixel ended up.
        self.drag_start_img = (ix, iy)
        self.drag_temp_id = None

    def on_canvas_drag(self, event):
        if not self.pil_image:
            return

        if self.dragging_vertex is not None:
            ln, idx = self.dragging_vertex
            ix, iy = self.canvas_to_img(event.x, event.y)
            if idx == 1:
                ln.x1, ln.y1 = ix, iy
            else:
                ln.x2, ln.y2 = ix, iy
            self.redraw()
            return

        if self.drag_start_img is None or self.app.mode.get() != "draw":
            return

        # Once the mouse has moved far enough, this press-and-hold counts as
        # a drag (finalized on release, below); a release before crossing
        # this threshold is a short click instead (see on_canvas_release).
        if self.press_canvas is not None and \
                dist((event.x, event.y), self.press_canvas) >= CLICK_MOVE_THRESHOLD:
            self.press_moved = True

        self._update_temp_line(*self.drag_start_img, event)

    def _update_temp_line(self, ix1, iy1, event):
        """Draw/update the dashed preview line from image point (ix1, iy1)
        to the current cursor, applying the Shift-parallel constraint if
        held. Shared by hold-and-drag drawing and click-to-click drawing."""
        sx, sy = self.img_to_canvas(ix1, iy1)
        cx, cy = event.x, event.y

        if event.state & 0x0001:  # Shift held: constrain to parallel reference
            ref = self._parallel_reference()
            if ref:
                ix2, iy2 = self.canvas_to_img(cx, cy)
                ex, ey = self._constrain_to_reference(ix1, iy1, ix2, iy2, ref)
                cx, cy = self.img_to_canvas(ex, ey)

        color_hex = AXIS_COLORS[self.app.current_color.get()]["hex"]
        if self.drag_temp_id:
            self.canvas.coords(self.drag_temp_id, sx, sy, cx, cy)
        else:
            self.drag_temp_id = self.canvas.create_line(
                sx, sy, cx, cy, fill=color_hex, width=2, dash=(4, 2))

    def on_canvas_hover(self, event):
        """No button held. While a click-started line is pending, this is
        what drives its live preview towards the cursor. Otherwise it just
        updates the cursor to hint what a click here would do (move a
        vertex vs. snap-start a new line vs. plain draw)."""
        if not self.pil_image or self.app.mode.get() != "draw":
            return

        if self.click_draw_start is not None:
            self._update_temp_line(*self.click_draw_start, event)
            return

        ix, iy = self.canvas_to_img(event.x, event.y)
        tol_img = HIT_TOLERANCE / self.scale
        hit = self.find_vertex_near(ix, iy, tol_img)
        if hit and hit[0].color == self.app.current_color.get():
            self.canvas.config(cursor="fleur")
        elif hit:
            self.canvas.config(cursor="hand2")
        else:
            self.canvas.config(cursor="crosshair")

    def on_canvas_release(self, event):
        if not self.pil_image:
            return

        if self.dragging_vertex is not None:
            ln, idx = self.dragging_vertex
            self.dragging_vertex = None
            self.redraw()
            self.mark_dirty()
            self.app.set_status(f"Moved line #{ln.id}'s endpoint.")
            return

        if self.drag_start_img is None or self.app.mode.get() != "draw":
            self.drag_start_img = None
            return

        ix1, iy1 = self.drag_start_img
        self.drag_start_img = None

        if not self.press_moved:
            # A short click, not a drag: start a click-to-click line instead
            # of finishing anything here. The preview line (if any -- it
            # may not exist yet if the mouse never moved) keeps following
            # the cursor via on_canvas_hover until the closing click, or
            # Escape cancels it.
            self.click_draw_start = (ix1, iy1)
            self.app.set_status("Line started -- click again to finish it, "
                                 "or press Esc to cancel.")
            return

        ix2, iy2 = self.canvas_to_img(event.x, event.y)
        self._finalize_line(ix1, iy1, ix2, iy2, bool(event.state & 0x0001))

    def _finalize_line(self, ix1, iy1, ix2, iy2, shift_held):
        """Create the new line from (ix1, iy1) to (ix2, iy2). Shared by the
        hold-and-drag release and the click-to-click closing click."""
        if self.drag_temp_id:
            self.canvas.delete(self.drag_temp_id)
            self.drag_temp_id = None

        parallel_to = None
        if shift_held:
            ref = self._parallel_reference()
            if ref:
                ix2, iy2 = self._constrain_to_reference(ix1, iy1, ix2, iy2, ref)
                parallel_to = ref.id

        if dist((ix1, iy1), (ix2, iy2)) < 3 / self.scale:
            self.app.set_status("Line too short -- not created.")
            return

        color = self.app.current_color.get()
        line = Line(color, ix1, iy1, ix2, iy2, unit=self.app.default_unit.get(),
                    parallel_to=parallel_to)
        self.lines.append(line)
        self.select_line(line.id)   # also redraws and populates the known-length field
        self.mark_dirty()

        # Deliberately NOT auto-focusing the known-length field here. It's
        # selected and the field shows its current value, ready to edit --
        # but focus stays wherever it was (the canvas), so Tab still cycles
        # the line color right after finishing a line instead of being eaten
        # by the field, and you're never forced to type immediately. Typing
        # a digit (see maybe_start_length_edit, bound app-wide) is itself
        # what starts editing the length.

    def _line_by_id(self, line_id):
        for ln in self.lines:
            if ln.id == line_id:
                return ln
        return None

    # ------------------------------------------------------------- mode
    def on_mode_changed(self):
        """Called for the active tab whenever the shared draw/select mode
        changes, and again right after this tab becomes active (in case the
        mode changed while it was in the background)."""
        self.dragging_vertex = None
        self.drag_start_img = None
        self.cancel_click_draw()
        self.canvas.config(cursor="crosshair" if self.app.mode.get() == "draw" else "hand2")

    def cancel_click_draw(self, event=None):
        """Escape (when not busy reverting a known-length edit -- see
        on_escape_key), or switching modes: abandon a line that was started
        with a short click and is waiting for the closing click."""
        if self.click_draw_start is None:
            return
        self.click_draw_start = None
        if self.drag_temp_id:
            self.canvas.delete(self.drag_temp_id)
            self.drag_temp_id = None
        self.app.set_status("Line cancelled.")

    def cycle_color(self, direction=1):
        """Tab (Shift+Tab to go backward) switches the active line color
        (X -> Y -> Z -> X), so you don't have to reach for the mouse
        mid-measurement. While the known-length field (or its unit box)
        has focus, Tab does NOT move focus to the next widget -- instead
        it first reverts whatever's been typed back to the value from
        before this edit (same as Escape -- see cancel_length_edit), hands
        focus back to the canvas, and *then* still cycles the color, all
        in one press."""
        focused = self.app.root.focus_get()
        if focused in (self.known_length_entry, self.known_unit_box):
            self.cancel_length_edit()
            self.canvas.focus_set()
        colors = list(AXIS_COLORS.keys())
        idx = colors.index(self.app.current_color.get())
        self.app.current_color.set(colors[(idx + direction) % len(colors)])
        return "break"

    def maybe_start_length_edit(self, event):
        """A digit or '.' typed anywhere -- not already inside the known-
        length field -- is treated as "start editing this line's known
        length", replacing whatever was there. This is the only way editing
        starts now: finishing a line no longer force-focuses the field (see
        _finalize_line), so a stray Tab press or a moment spent aiming the
        next line never gets swallowed by it. Bound app-wide (see App.__init__);
        does nothing unless exactly one line is selected."""
        focused = self.app.root.focus_get()
        if focused in (self.known_length_entry, self.known_unit_box):
            return None  # already editing normally -- let it type as usual
        if len(self.selected_line_ids) != 1:
            return None
        ch = event.char
        if not ch or not (ch.isdigit() or ch == "."):
            return None
        # Capture the snapshot ourselves, right now, BEFORE touching the var --
        # focus_set() below only *queues* a <FocusIn> event (Tk delivers it on
        # the next pass through the event loop, not synchronously), so if we
        # left this to the FocusIn binding it would fire after the var already
        # reads "2" instead of whatever the line's real value was, and typing
        # "27" would then revert to "2" instead of the pre-edit value. Suppress
        # that now-redundant (and wrong) FocusIn capture.
        self._capture_length_edit_snapshot()
        self._suppress_next_focus_snapshot = True
        self.known_length_var.set(ch)
        self.known_length_entry.focus_set()
        self.known_length_entry.icursor("end")
        return "break"

    def _capture_length_edit_snapshot(self, event=None):
        """FocusIn on the known-length field or its unit box (also called
        directly by maybe_start_length_edit, before it mutates anything):
        remember the value as of *right now*, before any of this edit's
        keystrokes land, so Escape can restore exactly this if the edit is
        abandoned."""
        if self._suppress_next_focus_snapshot:
            # maybe_start_length_edit already captured the real pre-edit
            # snapshot synchronously; this FocusIn is just the queued side
            # effect of the focus_set() it made afterward, and by the time
            # Tk delivers it the var has already been changed -- capturing
            # again here would silently replace the correct snapshot with
            # the wrong (already-edited) value.
            self._suppress_next_focus_snapshot = False
            return
        self.known_length_edit_snapshot = (self.known_length_var.get(), self.known_unit_var.get())

    def cancel_length_edit(self):
        """Throw away whatever's been typed/picked in the known-length field
        (and its unit) since editing started, restoring exactly what was
        there before -- used by Escape and by Tab/Shift-Tab (see cycle_color)."""
        if self.known_length_edit_snapshot is None:
            return False
        length_str, unit_str = self.known_length_edit_snapshot
        self.known_length_var.set(length_str)
        self.known_unit_var.set(unit_str)
        self.known_length_edit_snapshot = None
        return True

    def on_escape_key(self):
        """Escape either reverts an in-progress known-length edit back to
        its pre-edit value (if that field has focus), or -- otherwise --
        cancels a line that was started with a short click and is waiting
        for its closing click."""
        focused = self.app.root.focus_get()
        if focused in (self.known_length_entry, self.known_unit_box):
            self.cancel_length_edit()
            self.canvas.focus_set()
            return
        self.cancel_click_draw()

    # -------------------------------------------------------------- redraw
    def redraw(self):
        if not self.pil_image:
            self._show_placeholder()
            return
        self.canvas.delete("line", "label", "handle")
        for ln in self.lines:
            self._draw_line(ln)
        self._update_tree()
        self._update_axis_labels()
        self.app.refresh_toolbar_hint(self)

    def _draw_line(self, ln):
        x1, y1 = self.img_to_canvas(ln.x1, ln.y1)
        x2, y2 = self.img_to_canvas(ln.x2, ln.y2)
        meta = AXIS_COLORS[ln.color]
        selected = ln.id in self.selected_line_ids
        width = 4 if selected else 2
        outline = meta["select_hex"] if selected else meta["hex"]

        self.canvas.create_line(x1, y1, x2, y2, fill=outline, width=width, tags=("line",))
        for (x, y) in [(x1, y1), (x2, y2)]:
            self.canvas.create_oval(x - HANDLE_RADIUS, y - HANDLE_RADIUS,
                                     x + HANDLE_RADIUS, y + HANDLE_RADIUS,
                                     fill=outline, outline="white", tags=("handle",))

        real, unit = self.computed_length(ln)
        label = f"#{ln.id} {fmt_len(real, unit) if real else '? (uncalibrated)'}"
        if ln.known_length:
            label += " [known]"
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        self.canvas.create_text(mx, my - 12, text=label, fill="white",
                                 font=("", 9, "bold"), tags=("label",))

    def _update_tree(self):
        self.tree.delete(*self.tree.get_children())
        for ln in self.lines:
            real, unit = self.computed_length(ln)
            axis = AXIS_COLORS[ln.color]["axis"]
            self.tree.insert("", "end", iid=str(ln.id), values=(
                ln.color, axis, f"{ln.pixel_length():.1f}",
                fmt_len(real, unit) if real else "?",
                "yes" if ln.known_length else "no",
            ))
        # Mirrors self.selected_line_ids back onto the tree widget. This
        # fires <<TreeviewSelect>> again, which on_tree_select short-circuits
        # when nothing actually changed (see there) -- otherwise it's an
        # infinite loop that freezes the app on the very first line drawn.
        children = self.tree.get_children()
        for sel_id in self.selected_line_ids:
            if str(sel_id) in children:
                self.tree.selection_add(str(sel_id))

    def _update_axis_labels(self):
        for color, meta in AXIS_COLORS.items():
            upp, unit = self.axis_scale(color)
            if upp is None:
                self.axis_labels[color].config(
                    text=f"{meta['axis']} ({color}): not calibrated")
            else:
                self.axis_labels[color].config(
                    text=f"{meta['axis']} ({color}): {1/upp:.3f} px/{unit}  "
                         f"({upp:.5f} {unit}/px)")

    # ------------------------------------------------------------ selection
    def select_line(self, line_id, additive=False):
        if line_id is None:
            self.selected_line_ids = []
        elif additive:
            if line_id in self.selected_line_ids:
                self.selected_line_ids.remove(line_id)
            else:
                self.selected_line_ids.append(line_id)
        else:
            self.selected_line_ids = [line_id]
        self.redraw()
        self.populate_known_length_field()

    def on_tree_select(self, event=None):
        sel = [int(i) for i in self.tree.selection()]
        # _update_tree() below calls selection_add() to mirror our selection
        # back onto the tree widget, which re-fires <<TreeviewSelect>> (Tk
        # queues it, so a simple "are we already updating" flag can't catch
        # it in time). If nothing actually changed, stop here -- otherwise
        # this turns into an infinite redraw <-> selection_add loop that
        # freezes the whole app the moment a line is drawn.
        if set(sel) == set(self.selected_line_ids):
            return
        self.selected_line_ids = sel
        self.redraw()
        self.populate_known_length_field()

    # ------------------------------------------------- known-length field
    def populate_known_length_field(self):
        """Refresh the always-visible known-length field for the current
        selection. Enabled only when exactly one line is selected."""
        if len(self.selected_line_ids) == 1:
            ln = self._line_by_id(self.selected_line_ids[0])
            if ln:
                self.known_length_entry.config(state="normal")
                self.known_unit_box.config(state="readonly")
                self.known_length_var.set("" if ln.known_length is None else str(ln.known_length))
                self.known_unit_var.set(ln.unit or self.app.default_unit.get())
                self.selection_label.config(
                    text=f"Line #{ln.id} ({ln.color}, {AXIS_COLORS[ln.color]['axis']}) "
                         f"-- {ln.pixel_length():.1f} px")
                return
        self.known_length_var.set("")
        self.known_length_entry.config(state="disabled")
        self.known_unit_box.config(state="disabled")
        if len(self.selected_line_ids) == 0:
            self.selection_label.config(text="No line selected")
        else:
            self.selection_label.config(text=f"{len(self.selected_line_ids)} lines selected")

    def commit_known_length(self, event=None):
        """Apply whatever is currently typed in the known-length field to
        the single selected line. Never required -- blank just clears it."""
        if len(self.selected_line_ids) != 1:
            return
        ln = self._line_by_id(self.selected_line_ids[0])
        if not ln:
            return
        text = self.known_length_var.get().strip()
        if text == "":
            changed = ln.known_length is not None
            ln.known_length = None
        else:
            try:
                value = float(text)
            except ValueError:
                self.app.set_status("Known length must be a number (or leave it blank).")
                return
            if value <= 0:
                self.app.set_status("Known length must be greater than 0.")
                return
            changed = ln.known_length != value
            ln.known_length = value
        ln.unit = self.known_unit_var.get().strip() or self.app.default_unit.get()
        if changed:
            self.mark_dirty()
        self.redraw()
        self.populate_known_length_field()
        # This commit is the new baseline -- if editing continues (or resumes
        # later) and then gets abandoned with Escape, it should revert to
        # what was *just* committed, not to whatever was there before this
        # whole edit started.
        self.known_length_edit_snapshot = (self.known_length_var.get(), self.known_unit_var.get())

    def focus_known_length(self):
        if len(self.selected_line_ids) != 1:
            messagebox.showinfo("Select one line", "Select exactly one line first.")
            return
        self.known_length_entry.focus_set()
        self.known_length_entry.selection_range(0, "end")

    # -------------------------------------------------------------- actions
    def compare_selected(self):
        if len(self.selected_line_ids) != 2:
            messagebox.showinfo("Select two lines",
                                 "Select exactly two lines (Ctrl/Shift-click in the list) "
                                 "to compare them.")
            return
        a = self._line_by_id(self.selected_line_ids[0])
        b = self._line_by_id(self.selected_line_ids[1])
        if not a or not b:
            return

        a_real, a_unit = self.computed_length(a)
        if a_real is None:
            messagebox.showinfo(
                "Known length needed",
                f"Line #{a.id} ({a.color}) has no known length yet, and its axis "
                "isn't calibrated either.\n\nSelect just that line, type its "
                "known length into the field on the left, press Enter, then "
                "try Compare again.")
            return

        ratio = b.pixel_length() / a.pixel_length() if a.pixel_length() else 0
        b_real = a_real * ratio

        msg = (
            f"Line #{a.id} ({a.color}): {a.pixel_length():.1f} px = {fmt_len(a_real, a_unit)}\n"
            f"Line #{b.id} ({b.color}): {b.pixel_length():.1f} px\n\n"
            f"Pixel ratio (B/A): {ratio:.4f}\n"
            f"=> Line #{b.id} is approximately {fmt_len(b_real, a_unit)}"
        )
        if messagebox.askyesno("Comparison result", msg + "\n\nApply this as line "
                                f"#{b.id}'s known length?"):
            b.known_length = b_real
            b.unit = a_unit
            self.mark_dirty()
            self.redraw()

    def delete_selected(self):
        if not self.selected_line_ids:
            return
        self.lines = [ln for ln in self.lines if ln.id not in self.selected_line_ids]
        self.selected_line_ids = []
        self.mark_dirty()
        self.redraw()

    # ------------------------------------------------------------- project
    def save_project(self):
        if self.project_path:
            self._write_project(self.project_path)
        else:
            self.save_project_as()

    def save_project_as(self):
        if not self.image_path:
            messagebox.showinfo("No image", "Open an image first.")
            return
        default = os.path.splitext(self.image_path)[0] + PROJECT_EXT
        path = filedialog.asksaveasfilename(
            title="Save project", initialfile=os.path.basename(default),
            defaultextension=PROJECT_EXT,
            filetypes=[("Image Measure project", f"*{PROJECT_EXT}")])
        if not path:
            return
        self.project_path = path
        self._write_project(path)

    def _write_project(self, path):
        image_data_b64 = None
        if self.image_bytes_cache:
            image_data_b64 = base64.b64encode(self.image_bytes_cache).decode("ascii")
        data = {
            "image_path": self.image_path,
            # A copy of the original image FILE (not just its pixels -- this
            # preserves the exact bytes, so re-saving never re-compresses
            # it), so this project still opens correctly even if the photo
            # at image_path gets moved, renamed, or isn't on this computer.
            "image_data": image_data_b64,
            "rotation_turns": self.rotation_turns,
            "lines": [ln.to_dict() for ln in self.lines],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.dirty = False
        self.app.update_tab_title(self)
        size_note = ""
        if image_data_b64:
            size_kb = (len(image_data_b64) * 3 // 4) // 1024
            size_note = f" ({size_kb:,} KB -- image embedded)"
        self.app.set_status(f"Saved project to {path}{size_note}")

    def load_project_data(self, path):
        """Load a .imt project (image path + an embedded copy of the image
        + lines) into this tab. Returns True on success, False if
        cancelled/failed. Prefers the live file at the saved image_path
        when it's still there; falls back to the embedded copy (or, for an
        older project saved before image embedding existed, asks the user
        to locate the file) when it isn't."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            messagebox.showerror("Could not open project", str(exc))
            return False

        img_path = data.get("image_path")
        rotation_turns = data.get("rotation_turns", 0)
        image_b64 = data.get("image_data")
        loaded = False
        status_note = ""

        if img_path and os.path.exists(img_path):
            loaded = self.load_image_path(img_path, rotation_turns=rotation_turns, silent=True)

        if not loaded and image_b64:
            try:
                raw = base64.b64decode(image_b64)
            except Exception as exc:
                raw = None
                messagebox.showwarning("Embedded image unreadable",
                                        f"The image data embedded in this project "
                                        f"couldn't be decoded: {exc}")
            if raw:
                loaded = self.load_embedded_image(raw, display_path=img_path,
                                                   rotation_turns=rotation_turns)
                if loaded:
                    status_note = (" (original file not found at its saved location -- "
                                    "opened the copy embedded in the project instead)")

        if not loaded:
            # Last resort: an older project saved before images were embedded,
            # and the original file has also moved -- ask where it went.
            located = filedialog.askopenfilename(
                title="Original image not found -- locate it",
                filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp")])
            if not located:
                return False
            if not self.load_image_path(located, rotation_turns=rotation_turns, silent=True):
                return False

        self.lines = [Line.from_dict(d) for d in data.get("lines", [])]
        self.selected_line_ids = []
        self.project_path = path
        self.dirty = False
        self.app.update_tab_title(self)
        self.fit_to_window()
        self.redraw()
        self.app.set_status(f"Loaded project {path}{status_note}")
        return True

    def export_csv(self):
        if not self.lines:
            messagebox.showinfo("Nothing to export", "There are no lines yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Export measurements", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "color", "axis", "x1", "y1", "x2", "y2",
                        "pixel_length", "known_length", "unit",
                        "computed_length", "parallel_to"])
            for ln in self.lines:
                real, unit = self.computed_length(ln)
                w.writerow([ln.id, ln.color, AXIS_COLORS[ln.color]["axis"],
                            f"{ln.x1:.2f}", f"{ln.y1:.2f}", f"{ln.x2:.2f}", f"{ln.y2:.2f}",
                            f"{ln.pixel_length():.2f}",
                            ln.known_length if ln.known_length else "",
                            ln.unit or "", f"{real:.4f}" if real else "",
                            ln.parallel_to or ""])
        self.app.set_status(f"Exported {len(self.lines)} lines to {path}")

    # -------------------------------------------------------------- session
    def snapshot(self):
        """Everything needed to restore this tab exactly, even if it was
        never explicitly saved as a project -- used for session autosave."""
        return {
            "image_path": self.image_path,
            "project_path": self.project_path,
            "rotation_turns": self.rotation_turns,
            "scale": self.scale,
            "view_x": self.view_x,
            "view_y": self.view_y,
            "lines": [ln.to_dict() for ln in self.lines],
        }


class App:
    """Owns the window chrome shared by every tab: the menu, the toolbar
    (draw color / mode / compare / delete / zoom / rotate), the status bar,
    and the notebook of ProjectTabs. Also owns saving/restoring the session
    file so the whole window comes back the way you left it."""

    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1300x820")

        self.current_color = tk.StringVar(value=DEFAULT_COLOR)
        self.mode = tk.StringVar(value="draw")
        self.mode.trace_add("write", self._on_mode_var_change)
        # The unit new lines get by default (Settings menu). Deliberately a
        # persistent, explicitly-chosen setting rather than "whatever unit
        # the last line used" -- so one line measured in an odd unit doesn't
        # silently become the default for everything drawn after it.
        self.default_unit = tk.StringVar(value=DEFAULT_UNIT)

        self._build_menu()
        self._build_toolbar()

        self.notebook = ClosableNotebook(self.root)
        self.notebook.pack(side="top", fill="both", expand=True)
        self.notebook.bind("<<NotebookTabClosed>>", self._on_notebook_tab_closed)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_statusbar()

        self.root.bind("<Delete>", self._delete_selected_shortcut)
        self.root.bind("<BackSpace>", self._delete_selected_shortcut)
        self.root.bind("<Tab>", self._on_tab_key)
        self.root.bind("<Shift-Tab>", lambda e: self._on_tab_key(e, direction=-1))
        # Windows/some Linux send ISO_Left_Tab for Shift+Tab instead:
        self.root.bind("<ISO_Left_Tab>", lambda e: self._on_tab_key(e, direction=-1))
        self.root.bind("<KeyPress>", self._on_maybe_start_length_edit)
        self.root.bind("<Escape>", lambda e: self._dispatch("on_escape_key"))
        self.root.bind("<Control-o>", lambda e: self.open_image())
        self.root.bind("<Control-s>", lambda e: self._dispatch("save_project"))
        self.root.bind("<Control-t>", lambda e: self.new_tab())
        self.root.bind("<Control-w>", lambda e: self.close_active_tab())

        self.root.protocol("WM_DELETE_WINDOW", self.on_app_close)

        if not self._restore_session():
            self.new_tab()
        self.root.after(SESSION_AUTOSAVE_MS, self._periodic_autosave)

    # ---------------------------------------------------------- UI setup
    def _build_menu(self):
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="New Tab", command=self.new_tab, accelerator="Ctrl+T")
        filemenu.add_command(label="Open Image...", command=self.open_image, accelerator="Ctrl+O")
        filemenu.add_separator()
        filemenu.add_command(label="Open Project...", command=self.open_project)
        filemenu.add_command(label="Save Project", command=lambda: self._dispatch("save_project"),
                              accelerator="Ctrl+S")
        filemenu.add_command(label="Save Project As...",
                              command=lambda: self._dispatch("save_project_as"))
        filemenu.add_separator()
        filemenu.add_command(label="Close Tab", command=self.close_active_tab, accelerator="Ctrl+W")
        filemenu.add_separator()
        filemenu.add_command(label="Export Measurements (CSV)...",
                              command=lambda: self._dispatch("export_csv"))
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.on_app_close)
        menubar.add_cascade(label="File", menu=filemenu)

        editmenu = tk.Menu(menubar, tearoff=0)
        editmenu.add_command(label="Delete Selected Line(s)",
                              command=lambda: self._dispatch("delete_selected"))
        editmenu.add_command(label="Edit Known Length",
                              command=lambda: self._dispatch("focus_known_length"))
        editmenu.add_command(label="Compare Selected Two Lines...",
                              command=lambda: self._dispatch("compare_selected"))
        editmenu.add_separator()
        editmenu.add_command(label="Rotate Image 90°", command=lambda: self._dispatch("rotate_image"))
        menubar.add_cascade(label="Edit", menu=editmenu)

        settingsmenu = tk.Menu(menubar, tearoff=0)
        unitmenu = tk.Menu(settingsmenu, tearoff=0)
        for unit in UNIT_CHOICES:
            unitmenu.add_radiobutton(label=unit, variable=self.default_unit, value=unit)
        settingsmenu.add_cascade(label="Default Unit", menu=unitmenu)
        menubar.add_cascade(label="Settings", menu=settingsmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="How to use", command=self.show_help)
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.root.config(menu=menubar)

    def _build_toolbar(self):
        # The toolbar is a Canvas with the actual button row embedded in it,
        # rather than a plain Frame -- a plain Frame just clips whatever
        # doesn't fit a narrow window with no way to reach it (the same
        # problem the tab strip had -- see the "Tabs ▾" button above the
        # notebook). Here the fix is a horizontal scrollbar that only
        # appears once the buttons actually don't fit, plus the usual
        # scroll-wheel gestures, so every control stays reachable at any
        # window size instead of being silently cut off.
        outer = ttk.Frame(self.root)
        outer.pack(side="top", fill="x")

        canvas = tk.Canvas(outer, highlightthickness=0)
        canvas.pack(side="top", fill="x")
        self.toolbar_canvas = canvas

        hscroll = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hscroll.set)
        self.toolbar_hscroll = hscroll
        # Not packed yet -- _sync_toolbar_layout() below packs/unpacks it
        # on demand, only once the content actually overflows the window.

        bar = ttk.Frame(canvas, padding=6)
        self.toolbar_bar = bar
        bar_window = canvas.create_window((0, 0), window=bar, anchor="nw")

        def _sync_toolbar_layout(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"),
                              height=bar.winfo_reqheight())
            if bar.winfo_reqwidth() > canvas.winfo_width():
                if not hscroll.winfo_ismapped():
                    hscroll.pack(side="top", fill="x")
            elif hscroll.winfo_ismapped():
                hscroll.pack_forget()
                canvas.xview_moveto(0)

        def _sync_canvas_item_width(event):
            # Let the embedded row match the canvas's width when there's
            # room to spare, but never shrink it below what its content
            # actually needs -- that's what makes it scroll instead of
            # squashing the buttons once the window gets narrow.
            canvas.itemconfigure(bar_window, width=max(event.width, bar.winfo_reqwidth()))
            _sync_toolbar_layout()

        bar.bind("<Configure>", _sync_toolbar_layout)
        canvas.bind("<Configure>", _sync_canvas_item_width)

        def _on_toolbar_wheel(event):
            canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        canvas.bind("<MouseWheel>", _on_toolbar_wheel)        # Windows/macOS
        canvas.bind("<Shift-MouseWheel>", _on_toolbar_wheel)
        canvas.bind("<Button-4>", lambda e: canvas.xview_scroll(-1, "units"))  # Linux
        canvas.bind("<Button-5>", lambda e: canvas.xview_scroll(1, "units"))

        ttk.Label(bar, text="Line color / axis:").pack(side="left", padx=(0, 4))
        for color, meta in AXIS_COLORS.items():
            b = tk.Radiobutton(
                bar, text=f"{meta['axis']} ({color})", variable=self.current_color,
                value=color, indicatoron=False, width=10,
                fg="white", bg=meta["hex"], selectcolor=meta["hex"],
                activebackground=meta["select_hex"])
            b.pack(side="left", padx=2)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Label(bar, text="Mode:").pack(side="left", padx=(0, 4))
        ttk.Radiobutton(bar, text="Draw line", variable=self.mode, value="draw").pack(side="left")
        ttk.Radiobutton(bar, text="Select", variable=self.mode, value="select").pack(side="left")

        # Compare 2 Lines / Delete used to also have toolbar buttons here --
        # removed to declutter; both are still reachable from the Edit menu
        # ("Compare Selected Two Lines...", "Delete Selected Line(s)"), and
        # Delete/BackSpace still delete the selection directly.
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="Zoom In", command=lambda: self._dispatch("zoom", 1.25)).pack(side="left", padx=2)
        ttk.Button(bar, text="Zoom Out", command=lambda: self._dispatch("zoom", 0.8)).pack(side="left", padx=2)
        ttk.Button(bar, text="Fit", command=lambda: self._dispatch("fit_to_window")).pack(side="left", padx=2)
        ttk.Button(bar, text="Rotate 90°", command=lambda: self._dispatch("rotate_image")).pack(side="left", padx=2)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="New Tab", command=self.new_tab).pack(side="left", padx=2)
        ttk.Button(bar, text="Close Tab \u2715", command=self.close_active_tab).pack(side="left", padx=2)
        # ttk.Notebook doesn't scroll or wrap when there are more tabs than
        # fit the window -- it just clips the overflow ones with no way to
        # click them -- so this dropdown is the fallback that always reaches
        # every open tab regardless of window width or how many are open.
        # Always visible (not just when tabs overflow), right alongside the
        # other tab controls.
        self.tabs_menu_button = ttk.Button(bar, text="Tabs \u25be", width=9,
                                            command=self._show_tabs_menu)
        self.tabs_menu_button.pack(side="left", padx=2)

        self.parallel_hint = ttk.Label(bar, text="", foreground="#a05a00")
        self.parallel_hint.pack(side="left", padx=10)

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="Open an image or project to begin (File menu, "
                                               "Ctrl+O, or drag one onto a tab).")
        bar = ttk.Label(self.root, textvariable=self.status_var, anchor="w",
                         relief="sunken", padding=(6, 2))
        bar.pack(side="bottom", fill="x")

    def set_status(self, msg):
        self.status_var.set(msg)

    def show_help(self):
        messagebox.showinfo("How to use", (
            "1. Each open photo lives in its own tab -- use New Tab (Ctrl+T) or "
            "File > Open Image (Ctrl+O) to start another; the \u2715 on a tab (or "
            "middle-click, or Ctrl+W) closes it. If you have more tabs open than "
            "fit the window, the \"Tabs \u25be\" button above the tab strip lists "
            "every one of them, including any clipped off-screen.\n"
            "2. Pick Red/Green/Blue (X/Y/Z) and drag on the image to draw a line -- "
            "or press Tab (Shift+Tab to go back) to cycle the color instead of "
            "reaching for the mouse. It's selected automatically, and the "
            "known-length field on the left shows it -- but nothing is focused "
            "yet, so Tab still switches color right away if that's your next "
            "move.\n"
            "3. Start typing a number (with the line still selected) to set the "
            "real-world length that line represents (e.g. a known object edge) -- "
            "that's what focuses the field, and new lines start out in "
            "Settings > Default Unit (change it there instead of on every line). "
            "Press Enter to commit it, or leave it blank -- nothing is required. "
            "Esc reverts the field to whatever it was before you started typing, "
            "and Tab (or Shift+Tab) does the same revert and then immediately "
            "switches color, so you can always bail out of an edit and keep "
            "moving without touching the mouse -- Tab never jumps to another "
            "field while you're editing.\n"
            "4. Once one line of a color has a known length, every other line of "
            "that same color shows a computed real-world length automatically -- "
            "shown on the canvas and in the side list.\n"
            "5. 'Compare 2 Lines' lets you compare any two lines directly "
            "(they don't need to be the same color), even without calibrating "
            "a whole axis.\n"
            "6. Hold Shift while drawing a new line to lock its direction "
            "parallel to the selected line (or the last line drawn).\n"
            "7. 'Rotate 90°' rotates the photo a quarter turn clockwise and keeps "
            "every line attached to the same spot on the image.\n"
            "8. Save Project keeps one tab's image (a full copy, not just its "
            "path), rotation, and lines in a single .imt file you can reopen "
            "later -- even if the photo has since moved or is on another "
            "computer. Export CSV for a spreadsheet of measurements.\n"
            "9. The whole window -- every open tab, saved or not -- is remembered "
            "automatically and restored next time you launch the app."
        ))

    # -------------------------------------------------------------- tabs
    def active_tab(self):
        sel = self.notebook.select()
        if not sel:
            return None
        return self.notebook.nametowidget(sel)

    def new_tab(self, focus=True):
        tab = ProjectTab(self.notebook, self)
        self.notebook.add(tab, text="Untitled")
        if focus:
            self.notebook.select(tab)
        return tab

    def close_active_tab(self):
        self.request_close_tab(self.active_tab())

    def request_close_tab(self, tab):
        if tab is None:
            return
        if tab.dirty:
            name = tab.display_name()
            if not messagebox.askyesno(
                    "Close tab",
                    f'"{name}" has measurements that were never saved as a project '
                    "file.\n\nClose it anyway? (Your other open tabs, and anything "
                    "you don't close, are kept automatically and restored next time "
                    "you open the app -- but a tab you close now is gone for good "
                    "unless you've used Save Project on it.)"):
                return
        self.notebook.forget(tab)
        tab.destroy()
        if not self.notebook.tabs():
            self.new_tab()

    def _on_notebook_tab_closed(self, event=None):
        idx = self.notebook.last_closed_index
        if idx is None:
            return
        tabs = self.notebook.tabs()
        if 0 <= idx < len(tabs):
            self.request_close_tab(self.notebook.nametowidget(tabs[idx]))

    def _on_tab_changed(self, event=None):
        tab = self.active_tab()
        if tab is None:
            return
        tab.on_mode_changed()
        self.refresh_toolbar_hint(tab)
        self.update_tab_title(tab)

    def _on_mode_var_change(self, *args):
        tab = self.active_tab()
        if tab:
            tab.on_mode_changed()

    def _on_tab_key(self, event, direction=1):
        tab = self.active_tab()
        return tab.cycle_color(direction) if tab else None

    def _on_maybe_start_length_edit(self, event):
        tab = self.active_tab()
        return tab.maybe_start_length_edit(event) if tab else None

    def _delete_selected_shortcut(self, event=None):
        """Delete/BackSpace normally delete the selected line(s) -- but not
        while the known-length field (or its unit box) has focus, where
        BackSpace obviously means "erase a character I typed", not "delete
        this line"."""
        tab = self.active_tab()
        if tab is None:
            return None
        if self.root.focus_get() in (tab.known_length_entry, tab.known_unit_box):
            return None
        tab.delete_selected()
        return None

    def _dispatch(self, method_name, *args):
        tab = self.active_tab()
        if tab is None:
            return None
        return getattr(tab, method_name)(*args)

    def update_tab_title(self, tab):
        try:
            idx = self.notebook.index(tab)
        except tk.TclError:
            return
        name = tab.display_name()
        text = ("* " if tab.dirty else "") + name
        self.notebook.tab(idx, text=text)
        if tab is self.active_tab():
            self.root.title(f"{APP_NAME} - {name}" if tab.image_path else APP_NAME)

    def refresh_toolbar_hint(self, tab):
        if tab is not self.active_tab():
            return
        ref = tab._parallel_reference()
        if ref:
            self.parallel_hint.config(
                text=f"Hold Shift to draw parallel to line #{ref.id} ({ref.color})")
        else:
            self.parallel_hint.config(text="")

    def _build_tabs_menu(self):
        """Every open tab, by name -- including ones whose on-screen tab
        button is currently clipped off the notebook's tab strip (too many
        tabs to fit the window). Split out from _show_tabs_menu so it can be
        inspected directly without actually popping up a live menu."""
        menu = tk.Menu(self.root, tearoff=0)
        active = self.active_tab()
        for tab in self._all_tabs():
            name = ("* " if tab.dirty else "") + tab.display_name()
            mark = "✓ " if tab is active else "    "
            menu.add_command(label=mark + name, command=lambda t=tab: self.notebook.select(t))
        return menu

    def _show_tabs_menu(self):
        menu = self._build_tabs_menu()
        btn = self.tabs_menu_button
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # -------------------------------------------------------------- opening
    def _open_into_tab(self, loader, prefer_tab=None):
        """Reuse prefer_tab (or the active tab) if it's blank; otherwise
        open into a fresh tab. loader(tab) -> bool success. Cleans up the
        fresh tab again if loading was cancelled/failed."""
        candidate = prefer_tab or self.active_tab()
        reuse = candidate is not None and candidate.is_blank()
        target = candidate if reuse else self.new_tab(focus=False)
        ok = loader(target)
        if ok:
            self.notebook.select(target)
        elif not reuse:
            self.notebook.forget(target)
            target.destroy()
        return ok

    def open_image(self):
        path = filedialog.askopenfilename(
            title="Open image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
                       ("All files", "*.*")])
        if not path:
            return
        self._open_into_tab(lambda tab: tab.load_image_path(path))

    def open_project(self):
        path = filedialog.askopenfilename(
            title="Open project",
            filetypes=[("Image Measure project", f"*{PROJECT_EXT} *{LEGACY_PROJECT_EXT}"),
                       ("All files", "*.*")])
        if not path:
            return
        self._open_into_tab(lambda tab: tab.load_project_data(path))

    def handle_dropped_file(self, source_tab, path):
        self._open_into_tab(lambda tab: tab.load_image_path(path), prefer_tab=source_tab)

    def handle_dropped_project(self, source_tab, path):
        self._open_into_tab(lambda tab: tab.load_project_data(path), prefer_tab=source_tab)

    # -------------------------------------------------------------- session
    def _all_tabs(self):
        return [self.notebook.nametowidget(w) for w in self.notebook.tabs()]

    def save_session(self):
        """Best-effort: a problem here should never stop the app from
        closing, and never corrupt a previous good session file (written
        via a temp file + atomic replace)."""
        try:
            tabs = self._all_tabs()
            tabs_data = [t.snapshot() for t in tabs]
            active = self.notebook.index(self.notebook.select()) if tabs else 0
            data = {"version": 1, "active_index": active, "tabs": tabs_data,
                    "default_unit": self.default_unit.get()}
            os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
            tmp = SESSION_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, SESSION_PATH)
        except Exception:
            pass

    def _restore_session(self):
        if not os.path.exists(SESSION_PATH):
            return False
        try:
            with open(SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False

        if data.get("default_unit") in UNIT_CHOICES:
            self.default_unit.set(data["default_unit"])

        restored_any = False
        notes = []
        for entry in data.get("tabs", []):
            tab = ProjectTab(self.notebook, self)
            title = "Untitled"
            img_path = entry.get("image_path")
            project_path = entry.get("project_path")
            rotation_turns = entry.get("rotation_turns", 0)

            # Get the image's raw bytes from wherever they're still available:
            # the live file first, and -- if that's gone (moved/deleted) but
            # this tab was ever saved as a project -- the copy embedded in
            # that .imt file, same as opening the project directly would.
            raw = None
            recovered_via_project = False
            if img_path and os.path.exists(img_path):
                try:
                    with open(img_path, "rb") as f:
                        raw = f.read()
                except Exception:
                    raw = None
            if raw is None and project_path and os.path.exists(project_path):
                raw = _embedded_bytes_from_project_file(project_path)
                recovered_via_project = raw is not None

            ok = False
            if raw is not None:
                try:
                    tab._apply_loaded_bytes(raw, rotation_turns=rotation_turns)
                    ok = True
                except Exception:
                    ok = False

            if ok:
                tab.image_path = img_path
                tab.project_path = project_path
                tab.lines = [Line.from_dict(d) for d in entry.get("lines", [])]
                tab.selected_line_ids = []
                if entry.get("scale"):
                    tab.scale = entry["scale"]
                    tab.view_x = entry.get("view_x", tab.view_x)
                    tab.view_y = entry.get("view_y", tab.view_y)
                    tab._render_image()
                else:
                    tab.fit_to_window()
                tab.dirty = False
                tab._clear_placeholder()
                tab.redraw()
                title = tab.display_name()
                self.update_tab_title(tab)
                if recovered_via_project:
                    notes.append(f"'{title}' had moved -- recovered from its saved project file")
            elif img_path:
                notes.append(f"couldn't find '{os.path.basename(img_path)}' -- that tab is blank")

            self.notebook.add(tab, text=title)
            restored_any = True

        if restored_any:
            idx = data.get("active_index", 0)
            tabs = self.notebook.tabs()
            if 0 <= idx < len(tabs):
                self.notebook.select(tabs[idx])
            msg = f"Restored {len(tabs)} tab(s) from your last session."
            if notes:
                msg += " " + "; ".join(notes) + "."
            self.set_status(msg)
        return restored_any

    def _periodic_autosave(self):
        self.save_session()
        self.root.after(SESSION_AUTOSAVE_MS, self._periodic_autosave)

    def on_app_close(self):
        self.save_session()
        self.root.destroy()


def main():
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
