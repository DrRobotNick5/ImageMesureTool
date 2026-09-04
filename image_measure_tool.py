#!/usr/bin/env python3
"""
Image Measure Tool
===================

A Tkinter desktop app for measuring things in a photo.

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
- Save/load your work as a .json project file next to the image, and
  export all measurements to CSV.
- Scroll to zoom in on your cursor, hold the middle mouse button to pan,
  and drag an image file onto the window to open it.

Requires: Python 3.8+, Pillow (pip install pillow). Tkinter ships with
the standard Windows/macOS Python installers; on Linux install your
distro's python3-tk package if it's missing. Drag-and-drop needs the
optional tkinterdnd2 package (pip install tkinterdnd2) -- without it,
everything else still works, you just use File > Open Image instead.

Run:
    python image_measure_tool.py
"""

import json
import math
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk
except ImportError:
    raise SystemExit(
        "Pillow is required. Install it with:\n\n    pip install pillow\n"
    )

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")

AXIS_COLORS = {
    "red": {"axis": "X", "hex": "#e53935", "select_hex": "#ff8a80"},
    "green": {"axis": "Y", "hex": "#43a047", "select_hex": "#b9f6ca"},
    "blue": {"axis": "Z", "hex": "#1e88e5", "select_hex": "#82b1ff"},
}
DEFAULT_COLOR = "red"
HANDLE_RADIUS = 5
HIT_TOLERANCE = 6  # pixels, in canvas/screen space
PROJECT_EXT = ".imt.json"


def dist(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])


def fmt_len(value, unit, decimals=3):
    if value is None:
        return "?"
    return f"{value:.{decimals}g} {unit}"


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


class ImageMeasureApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Image Measure Tool")
        self.root.geometry("1200x780")

        self.image_path = None
        self.pil_image = None            # original, full-res PIL image
        self.tk_image = None              # scaled PhotoImage currently shown
        self.scale = 1.0                  # canvas px per image px
        self.view_x = 0.0                 # image-space coords shown at canvas (0, 0)
        self.view_y = 0.0
        self.pan_start = None             # (canvas_x, canvas_y, view_x, view_y) mid-drag
        self.lines = []                   # list[Line]
        self.selected_line_ids = []       # for compare / delete / calibrate
        self.current_color = tk.StringVar(value=DEFAULT_COLOR)
        self.mode = tk.StringVar(value="draw")   # 'draw' | 'parallel' | 'select'
        self.parallel_ref_id = None        # reference line id, when in parallel mode
        self.drag_start_canvas = None
        self.drag_temp_id = None
        self.project_path = None

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

        self.root.bind("<Delete>", lambda e: self.delete_selected())
        self.root.bind("<BackSpace>", lambda e: self.delete_selected())

    # ---------------------------------------------------------- UI setup
    def _build_menu(self):
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open Image...", command=self.open_image, accelerator="Ctrl+O")
        filemenu.add_separator()
        filemenu.add_command(label="Open Project...", command=self.open_project)
        filemenu.add_command(label="Save Project", command=self.save_project, accelerator="Ctrl+S")
        filemenu.add_command(label="Save Project As...", command=self.save_project_as)
        filemenu.add_separator()
        filemenu.add_command(label="Export Measurements (CSV)...", command=self.export_csv)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=filemenu)

        editmenu = tk.Menu(menubar, tearoff=0)
        editmenu.add_command(label="Delete Selected Line(s)", command=self.delete_selected)
        editmenu.add_command(label="Edit Known Length", command=self.focus_known_length)
        editmenu.add_command(label="Compare Selected Two Lines...", command=self.compare_selected)
        editmenu.add_separator()
        editmenu.add_command(label="Rotate Image 90°", command=self.rotate_image)
        menubar.add_cascade(label="Edit", menu=editmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="How to use", command=self.show_help)
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda e: self.open_image())
        self.root.bind("<Control-s>", lambda e: self.save_project())

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=6)
        bar.pack(side="top", fill="x")

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
        ttk.Radiobutton(bar, text="Draw line", variable=self.mode, value="draw",
                         command=self._on_mode_change).pack(side="left")
        ttk.Radiobutton(bar, text="Draw parallel", variable=self.mode, value="parallel",
                         command=self._on_mode_change).pack(side="left")
        ttk.Radiobutton(bar, text="Select", variable=self.mode, value="select",
                         command=self._on_mode_change).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="Compare 2 Lines", command=self.compare_selected).pack(side="left", padx=2)
        ttk.Button(bar, text="Delete", command=self.delete_selected).pack(side="left", padx=2)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="Zoom In", command=lambda: self.zoom(1.25)).pack(side="left", padx=2)
        ttk.Button(bar, text="Zoom Out", command=lambda: self.zoom(0.8)).pack(side="left", padx=2)
        ttk.Button(bar, text="Fit", command=self.fit_to_window).pack(side="left", padx=2)
        ttk.Button(bar, text="Rotate 90°", command=self.rotate_image).pack(side="left", padx=2)

        self.parallel_hint = ttk.Label(bar, text="", foreground="#a05a00")
        self.parallel_hint.pack(side="left", padx=10)

    def _build_body(self):
        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)

        # side panel: known-length field + line list (on the LEFT)
        side = ttk.Frame(body, padding=6, width=340)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        # canvas (fills the rest of the window, to the right). No scrollbars --
        # panning is done by dragging with the middle mouse button, and only
        # the visible region is ever rendered (see _render_image), which is
        # what keeps zooming in on a big photo from lagging.
        canvas_frame = ttk.Frame(body)
        canvas_frame.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg="#2b2b2b", cursor="crosshair",
                                 highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
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

        self.known_unit_var = tk.StringVar(value="mm")
        self.known_unit_box = ttk.Combobox(
            entry_row, textvariable=self.known_unit_var, width=6, state="disabled",
            values=["mm", "cm", "m", "in", "ft", "px"])
        self.known_unit_box.pack(side="left", padx=(4, 0))
        self.known_unit_box.bind("<<ComboboxSelected>>", self.commit_known_length)
        self.known_unit_box.bind("<Return>", self.commit_known_length)

        ttk.Label(known_frame, text="Type a value and press Enter. Leave blank for "
                                     "no known length -- nothing is required.",
                  foreground="#666", wraplength=300, justify="left").pack(
            anchor="w", pady=(4, 0))

        ttk.Label(side, text="Measured lines", font=("", 10, "bold")).pack(anchor="w")
        columns = ("color", "axis", "px", "real", "calib")
        self.tree = ttk.Treeview(side, columns=columns, show="headings", height=20,
                                  selectmode="extended")
        for col, label, width in [
            ("color", "Color", 55), ("axis", "Axis", 40), ("px", "Pixels", 65),
            ("real", "Real length", 100), ("calib", "Known?", 55),
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

        help_text = (
            "How to measure:\n"
            "1. Pick a color (X/Y/Z) above.\n"
            "2. Drag on the image to draw a line -- it's\n"
            "   selected automatically and the known-length\n"
            "   field above is focused and ready to type in.\n"
            "3. Type its real-world length and press Enter\n"
            "   (or leave it blank -- nothing is required).\n"
            "4. Every other line of that color then shows\n"
            "   a computed length automatically.\n\n"
            "Parallel: pick 'Draw parallel', click near an\n"
            "existing line, then drag elsewhere to add a\n"
            "new line locked to the same direction.\n\n"
            "Compare: select exactly two lines in the list\n"
            "(Ctrl/Shift-click) and click 'Compare 2 Lines'.\n\n"
            "Rotate 90° (toolbar) rotates the photo a quarter\n"
            "turn and keeps every existing line attached to\n"
            "the same spot on the image.\n\n"
            "Drag an image file onto the canvas to open it"
            + ("." if self.dnd_active else " (run: pip install tkinterdnd2).") + "\n"
            "Scroll wheel zooms in on your cursor. Hold the\n"
            "middle mouse button and drag to pan."
        )
        ttk.Label(side, text=help_text, foreground="#555", justify="left").pack(
            anchor="w", pady=(10, 0))

    def _build_statusbar(self):
        msg = "Open an image to begin (File > Open Image"
        msg += " or drag one onto the canvas)." if self.dnd_active else ")."
        self.status = tk.StringVar(value=msg)
        bar = ttk.Label(self.root, textvariable=self.status, anchor="w",
                         relief="sunken", padding=(6, 2))
        bar.pack(side="bottom", fill="x")

    def _on_mode_change(self):
        self.parallel_ref_id = None
        if self.mode.get() == "parallel":
            self.parallel_hint.config(text="Parallel mode: click near a line to lock its direction, "
                                            "then drag to draw the new line.")
        else:
            self.parallel_hint.config(text="")

    # ------------------------------------------------------------- image
    def open_image(self):
        path = filedialog.askopenfilename(
            title="Open image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
                       ("All files", "*.*")])
        if not path:
            return
        self._load_image_path(path)

    def on_drop_file(self, event):
        # event.data may be one path, or several space-separated and
        # brace-quoted (e.g. "{C:/a b/img.jpg} {C:/other.png}") -- splitlist
        # understands that Tcl-list quoting.
        paths = self.canvas.tk.splitlist(event.data)
        for path in paths:
            if path.lower().endswith(IMAGE_EXTS):
                self._load_image_path(path)
                return
        messagebox.showinfo("Not an image", "Drop an image file (jpg/png/bmp/tif/webp).")

    def _load_image_path(self, path):
        try:
            img = Image.open(path)
            img.load()
        except Exception as exc:
            messagebox.showerror("Could not open image", str(exc))
            return
        self.image_path = path
        self.pil_image = img.convert("RGB")
        self.lines = []
        self.selected_line_ids = []
        self.project_path = None
        self.root.title(f"Image Measure Tool - {os.path.basename(path)}")
        self.fit_to_window()
        self.redraw()
        self.status.set(f"Loaded {os.path.basename(path)} "
                         f"({self.pil_image.width}x{self.pil_image.height}px)")

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

    def rotate_image(self):
        """Rotate the loaded image 90 degrees clockwise, keeping every
        existing line attached to the same spot on the picture."""
        if not self.pil_image:
            messagebox.showinfo("No image", "Open an image first.")
            return
        old_h = self.pil_image.height
        self.pil_image = self.pil_image.transpose(Image.ROTATE_270)  # 90 deg clockwise
        for ln in self.lines:
            ln.x1, ln.y1 = old_h - ln.y1, ln.x1
            ln.x2, ln.y2 = old_h - ln.y2, ln.x2
        self.fit_to_window()
        self.redraw()
        self.status.set("Rotated image 90 degrees.")

    def _render_image(self):
        """Draw only the part of the image that's actually visible, resized
        to fit the canvas. However far you're zoomed in, this never resizes
        more pixels than the canvas itself has -- that's what keeps zooming
        into a large photo fast (resizing the *whole* image at high zoom is
        what caused the lag)."""
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        iw, ih = self.pil_image.width, self.pil_image.height

        # visible region, in image-space coordinates
        vx0, vy0 = self.view_x, self.view_y
        vx1, vy1 = self.view_x + cw / self.scale, self.view_y + ch / self.scale

        # clamp to the actual image bounds
        cx0, cy0 = max(vx0, 0), max(vy0, 0)
        cx1, cy1 = min(vx1, iw), min(vy1, ih)

        canvas_img = Image.new("RGB", (cw, ch), (43, 43, 43))
        if cx1 > cx0 and cy1 > cy0:
            crop = self.pil_image.crop(
                (int(cx0), int(cy0), math.ceil(cx1), math.ceil(cy1)))
            out_w = max(1, round((cx1 - cx0) * self.scale))
            out_h = max(1, round((cy1 - cy0) * self.scale))
            resample = Image.LANCZOS if self.scale <= 1 else Image.BILINEAR
            resized = crop.resize((out_w, out_h), resample)
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

    def on_canvas_press(self, event):
        if not self.pil_image:
            return
        ix, iy = self.canvas_event_to_img(event)
        tol_img = HIT_TOLERANCE / self.scale

        if self.mode.get() == "select":
            ln = self.find_line_near(ix, iy, tol_img)
            if ln:
                self.select_line(ln.id, additive=bool(event.state & 0x0001))  # shift
            else:
                self.select_line(None)
            return

        if self.mode.get() == "parallel":
            if self.parallel_ref_id is None:
                ln = self.find_line_near(ix, iy, tol_img)
                if ln:
                    self.parallel_ref_id = ln.id
                    self.select_line(ln.id)
                    self.parallel_hint.config(
                        text=f"Reference: line #{ln.id} ({ln.color}). "
                             f"Now drag to draw the parallel line.")
                else:
                    self.status.set("Click closer to an existing line to use as the parallel reference.")
                return
            # reference already chosen: fall through to start a drag

        self.drag_start_canvas = (event.x, event.y)
        self.drag_temp_id = None

    def on_canvas_drag(self, event):
        if not self.pil_image or self.drag_start_canvas is None:
            return
        if self.mode.get() not in ("draw", "parallel"):
            return
        cx, cy = event.x, event.y
        sx, sy = self.drag_start_canvas

        if self.mode.get() == "parallel" and self.parallel_ref_id is not None:
            ref = self._line_by_id(self.parallel_ref_id)
            if ref:
                ang = ref.angle()
                dirx, diry = math.cos(ang), math.sin(ang)
                # project drag vector (in image space) onto reference direction
                ix1, iy1 = self.canvas_to_img(sx, sy)
                ix2, iy2 = self.canvas_to_img(cx, cy)
                vx, vy = ix2 - ix1, iy2 - iy1
                proj_len = vx * dirx + vy * diry
                ex, ey = ix1 + proj_len * dirx, iy1 + proj_len * diry
                cx, cy = self.img_to_canvas(ex, ey)

        color_hex = AXIS_COLORS[self.current_color.get()]["hex"]
        if self.drag_temp_id:
            self.canvas.coords(self.drag_temp_id, sx, sy, cx, cy)
        else:
            self.drag_temp_id = self.canvas.create_line(
                sx, sy, cx, cy, fill=color_hex, width=2, dash=(4, 2))

    def on_canvas_release(self, event):
        if not self.pil_image or self.drag_start_canvas is None:
            return
        if self.mode.get() not in ("draw", "parallel"):
            self.drag_start_canvas = None
            return

        cx, cy = event.x, event.y
        sx, sy = self.drag_start_canvas
        if self.drag_temp_id:
            self.canvas.delete(self.drag_temp_id)
            self.drag_temp_id = None

        ix1, iy1 = self.canvas_to_img(sx, sy)
        ix2, iy2 = self.canvas_to_img(cx, cy)
        parallel_to = None

        if self.mode.get() == "parallel" and self.parallel_ref_id is not None:
            ref = self._line_by_id(self.parallel_ref_id)
            if ref:
                ang = ref.angle()
                dirx, diry = math.cos(ang), math.sin(ang)
                vx, vy = ix2 - ix1, iy2 - iy1
                proj_len = vx * dirx + vy * diry
                ix2, iy2 = ix1 + proj_len * dirx, iy1 + proj_len * diry
                parallel_to = ref.id

        self.drag_start_canvas = None
        if dist((ix1, iy1), (ix2, iy2)) < 3 / self.scale:
            return  # treat as a click, not a line

        color = self.current_color.get() if parallel_to is None else \
            self._line_by_id(parallel_to).color
        line = Line(color, ix1, iy1, ix2, iy2, unit=self._last_unit_used(),
                    parallel_to=parallel_to)
        self.lines.append(line)
        self.select_line(line.id)   # also redraws and populates the known-length field

        # Ready for the user to immediately type the known length -- no popup,
        # nothing required. Just focus the field with the cursor in place.
        self.known_length_entry.focus_set()
        self.known_length_entry.icursor("end")

        if self.mode.get() == "parallel":
            self.parallel_ref_id = None
            self.parallel_hint.config(text="Parallel mode: click near a line to lock its direction, "
                                            "then drag to draw the new line.")

    def _last_unit_used(self):
        for ln in reversed(self.lines):
            if ln.unit:
                return ln.unit
        return "mm"

    def _line_by_id(self, line_id):
        for ln in self.lines:
            if ln.id == line_id:
                return ln
        return None

    # -------------------------------------------------------------- redraw
    def redraw(self):
        self.canvas.delete("line", "label", "handle")
        for ln in self.lines:
            self._draw_line(ln)
        self._update_tree()
        self._update_axis_labels()

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
                self.known_unit_var.set(ln.unit or self._last_unit_used())
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
            ln.known_length = None
        else:
            try:
                value = float(text)
            except ValueError:
                self.status.set("Known length must be a number (or leave it blank).")
                return
            if value <= 0:
                self.status.set("Known length must be greater than 0.")
                return
            ln.known_length = value
        ln.unit = self.known_unit_var.get().strip() or "mm"
        self.redraw()
        self.populate_known_length_field()

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
            self.redraw()

    def delete_selected(self):
        if not self.selected_line_ids:
            return
        self.lines = [ln for ln in self.lines if ln.id not in self.selected_line_ids]
        self.selected_line_ids = []
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
            defaultextension=".json", filetypes=[("Image Measure project", "*.json")])
        if not path:
            return
        self.project_path = path
        self._write_project(path)

    def _write_project(self, path):
        data = {
            "image_path": self.image_path,
            "lines": [ln.to_dict() for ln in self.lines],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.status.set(f"Saved project to {path}")

    def open_project(self):
        path = filedialog.askopenfilename(
            title="Open project", filetypes=[("Image Measure project", "*.json")])
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        img_path = data.get("image_path")
        if not img_path or not os.path.exists(img_path):
            img_path = filedialog.askopenfilename(
                title="Original image not found -- locate it",
                filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp")])
            if not img_path:
                return
        img = Image.open(img_path)
        img.load()
        self.image_path = img_path
        self.pil_image = img.convert("RGB")
        self.lines = [Line.from_dict(d) for d in data.get("lines", [])]
        self.selected_line_ids = []
        self.project_path = path
        self.root.title(f"Image Measure Tool - {os.path.basename(img_path)}")
        self.fit_to_window()
        self.redraw()
        self.status.set(f"Loaded project {path}")

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
        self.status.set(f"Exported {len(self.lines)} lines to {path}")

    def show_help(self):
        messagebox.showinfo("How to use", (
            "1. File > Open Image to load a photo.\n"
            "2. Pick Red/Green/Blue (X/Y/Z) and drag on the image to draw a line. "
            "It's selected automatically and the known-length field on the left "
            "is focused, ready for you to type into.\n"
            "3. Type the real-world length that line represents (e.g. a known "
            "object edge) and press Enter, or leave it blank -- nothing is "
            "required. You can always come back and edit it later by selecting "
            "the line again.\n"
            "4. Once one line of a color has a known length, every other line of "
            "that same color shows a computed real-world length automatically -- "
            "shown on the canvas and in the side list.\n"
            "5. 'Compare 2 Lines' lets you compare any two lines directly "
            "(they don't need to be the same color), even without calibrating "
            "a whole axis.\n"
            "6. 'Draw parallel' mode: click near an existing line to lock its "
            "direction, then drag anywhere to add a new line guaranteed parallel "
            "to it.\n"
            "7. 'Rotate 90°' rotates the photo a quarter turn clockwise and keeps "
            "every line attached to the same spot on the image.\n"
            "8. Save Project keeps the image path + all lines in a .json file "
            "you can reopen later. Export CSV for a spreadsheet of measurements."
        ))


def main():
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    app = ImageMeasureApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
