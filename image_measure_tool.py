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
  based on that axis's pixels-per-unit scale. Lengths can be typed as
  fractions/mixed numbers/simple math (1/2, 2 1/2, 2 + 1/2), and every
  line's DISPLAY (fraction vs. decimal, rounded to a configurable nearest
  fraction, and which unit) can be set independently of how its axis was
  calibrated -- see Line.display_mode/display_unit and
  ProjectTab.effective_display.
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

import ast
import base64
import copy
import io
import json
import math
import operator
import os
import re
import sys
import tkinter as tk
from fractions import Fraction
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
# Orange is a 4th line color, but NOT a 4th measurement axis -- it's never
# in AXIS_COLORS (so it never appears in the Red/Green/Blue draw-color
# picker or the Tab-cycles-color rotation) and never a target for known-
# length axis calibration on its own. It's selectable for any line via
# the known-length panel's Color row (see ProjectTab.set_line_color), and
# is what a Circle's 2 diameter lines start out as before being assigned
# to a real axis.
ORANGE_COLOR = "orange"
ORANGE_META = {"axis": None, "hex": "#fb8c00", "select_hex": "#ffcc80"}


def line_color_meta(color):
    """AXIS_COLORS[color], plus Orange (which isn't a measurement axis, so
    it's deliberately not in that dict) -- use this instead of indexing
    AXIS_COLORS directly anywhere a Line's *own* .color is being looked up,
    since an ellipse's diameter lines can be orange."""
    return AXIS_COLORS.get(color, ORANGE_META)
UNIT_CHOICES = ["mm", "cm", "m", "in", "ft", "px"]
DEFAULT_UNIT = "mm"
# Millimeters per unit, for converting a computed length into a DIFFERENT
# display unit than the one its axis was calibrated in. "px" is deliberately
# absent -- a pixel isn't a physical unit, so it can't be converted to/from
# one; a line whose display unit would require that conversion just falls
# back to showing its calibration unit instead (see ProjectTab.effective_display).
UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
FRACTION_DENOMINATOR_CHOICES = [2, 4, 8, 16, 32, 64]
DEFAULT_FRACTION_DENOMINATOR = 32
DISPLAY_MODE_CHOICES = ["decimal", "fraction"]
DEFAULT_DISPLAY_MODE = "decimal"
HANDLE_RADIUS = 5
HIT_TOLERANCE = 6  # pixels, in canvas/screen space
CLICK_MOVE_THRESHOLD = 4  # canvas pixels of movement that turns a click into a drag
PROJECT_EXT = ".imt"
MAX_UNDO_STEPS = 50
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


def convert_length(value, from_unit, to_unit):
    """Convert a length between physical units (mm/cm/m/in/ft). Returns
    None if either unit is missing or is "px" and they differ -- a pixel
    isn't a physical unit, so it can't be converted to/from one; the
    caller should fall back to the original unit in that case."""
    if value is None or not from_unit or not to_unit:
        return None
    if from_unit == to_unit:
        return value
    if from_unit not in UNIT_TO_MM or to_unit not in UNIT_TO_MM:
        return None
    return value * UNIT_TO_MM[from_unit] / UNIT_TO_MM[to_unit]


# --------------------------------------------------------- ellipse math ----
# A circle photographed at an angle projects to an ellipse. This app defines
# one from 2 "diameter" lines through a shared center -- each line's own
# vector from that center (u for one line, v for the other) is used as a
# CONJUGATE SEMI-DIAMETER PAIR: the ellipse is exactly the curve
#     P(t) = center + cos(t)*u + sin(t)*v ,  t in [0, 2*pi)
# This is well-defined for any 2 non-parallel u, v (u sits at t=0, v at
# t=pi/2, by construction) and is the same classical technique used to draw
# a circle in perspective/isometric views by hand. Two points on an ellipse
# are only USABLE this way (reproducing the intended curve) when they're a
# true conjugate pair, which is why "rotating" one diameter afterward can't
# just move it to a new spot and re-derive the shape from scratch -- see
# ellipse_canonical / ellipse_param_for_direction below, which instead work
# from the frozen canonical (semi_a, semi_b, phi) form so the curve itself
# never changes shape mid-rotate.
def ellipse_canonical(cx, cy, ux, uy, vx, vy):
    """Convert a center + 2 conjugate semi-diameter vectors (u, v) into the
    ellipse's canonical form: semi_a >= semi_b (the semi-major/minor axis
    lengths) and phi (the semi-major axis's direction, radians). Same
    curve, just re-expressed in an orthogonal basis -- needed so a later
    "rotate" can address any point on the boundary by a true geometric
    angle instead of only the 2 original conjugate directions."""
    P = ux * ux + uy * uy
    Q = vx * vx + vy * vy
    R = ux * vx + uy * vy
    if abs(P - Q) < 1e-12 and abs(R) < 1e-12:
        t0 = 0.0
    else:
        t0 = 0.5 * math.atan2(2 * R, P - Q)

    def point_at(t):
        return (ux * math.cos(t) + vx * math.sin(t), uy * math.cos(t) + vy * math.sin(t))

    p1 = point_at(t0)
    p2 = point_at(t0 + math.pi / 2)
    len1, len2 = math.hypot(*p1), math.hypot(*p2)
    if len1 >= len2:
        return len1, len2, math.atan2(p1[1], p1[0])
    return len2, len1, math.atan2(p2[1], p2[0])


def ellipse_point_at(cx, cy, semi_a, semi_b, phi, t):
    """The boundary point at canonical parameter t (radians)."""
    ca, sa = math.cos(phi), math.sin(phi)
    ct, st = math.cos(t), math.sin(t)
    return (cx + semi_a * ct * ca - semi_b * st * sa,
            cy + semi_a * ct * sa + semi_b * st * ca)


def ellipse_param_of_point(cx, cy, semi_a, semi_b, phi, px, py):
    """The canonical parameter t of a point already known to be on the
    ellipse (e.g. one of its diameter endpoints) -- the inverse of
    ellipse_point_at."""
    dx, dy = px - cx, py - cy
    ca, sa = math.cos(phi), math.sin(phi)
    rx = dx * ca + dy * sa
    ry = -dx * sa + dy * ca
    cos_t = rx / semi_a if semi_a > 1e-9 else 0.0
    sin_t = ry / semi_b if semi_b > 1e-9 else 0.0
    return math.atan2(sin_t, cos_t)


def ellipse_param_for_direction(cx, cy, semi_a, semi_b, phi, dx, dy):
    """The parameter t whose boundary point lies in the direction (dx, dy)
    as seen from the center (the antipodal solution, on the opposite side,
    is t +/- pi -- this picks the one actually pointing that way). Used
    both for a diameter following the cursor (dx,dy = cursor - center) and
    for aligning it to a reference line's direction (Make Parallel)."""
    ca, sa = math.cos(phi), math.sin(phi)
    rx = dx * ca + dy * sa
    ry = -dx * sa + dy * ca
    t = math.atan2(semi_a * ry, semi_b * rx)
    px, py = ellipse_point_at(cx, cy, semi_a, semi_b, phi, t)
    if (px - cx) * dx + (py - cy) * dy < 0:
        t += math.pi
    return t


def ellipse_nearest_point(cx, cy, semi_a, semi_b, phi, px, py, samples=72):
    """The closest point on the ellipse boundary to (px, py), for Snap
    Mode. A coarse sample around the whole boundary followed by a short
    ternary-search refinement -- simple and numerically robust (no
    divergence risk) rather than a closed-form root solve, which is more
    than accurate enough for a several-pixel hit-test tolerance."""
    best_t, best_d2 = 0.0, None
    for i in range(samples):
        t = 2 * math.pi * i / samples
        x, y = ellipse_point_at(cx, cy, semi_a, semi_b, phi, t)
        d2 = (x - px) ** 2 + (y - py) ** 2
        if best_d2 is None or d2 < best_d2:
            best_d2, best_t = d2, t
    step = 2 * math.pi / samples
    lo, hi = best_t - step, best_t + step

    def d2_at(t):
        x, y = ellipse_point_at(cx, cy, semi_a, semi_b, phi, t)
        return (x - px) ** 2 + (y - py) ** 2

    for _ in range(20):
        m1 = lo + (hi - lo) / 3
        m2 = hi - (hi - lo) / 3
        if d2_at(m1) < d2_at(m2):
            hi = m2
        else:
            lo = m1
    t = (lo + hi) / 2
    return ellipse_point_at(cx, cy, semi_a, semi_b, phi, t)


# ------------------------------------------------------- fraction input ----
# A measurement can be typed as a plain decimal ("2.5"), a simple fraction
# ("1/2"), a mixed number ("2 1/2" -- no operator needed, or "2+1/2"), or
# basic arithmetic combining any of those ("2 + 1/2 - 3/8"). Everything is
# evaluated exactly with fractions.Fraction (never float) so e.g. 1/3 stays
# exact until the very last step, instead of accumulating binary-float error.

_MIXED_NUMBER_RE = re.compile(r"(?<![\w./])(\d+)[ \t]+(\d+/\d+)(?![\d/])")

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
}


def _insert_implicit_plus(text):
    """'2 1/2' (whole number, space, fraction, no operator) is the common
    way people write a mixed number -- treat it as '2 + 1/2'. Text that
    already has an operator there ('2 + 1/2', '2 - 1/2') is left alone."""
    return _MIXED_NUMBER_RE.sub(r"\1+\2", text)


def _eval_fraction_node(node):
    if isinstance(node, ast.Expression):
        return _eval_fraction_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Fraction(str(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval_fraction_node(node.left),
                                       _eval_fraction_node(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_fraction_node(node.operand)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _eval_fraction_node(node.operand)
    raise ValueError("unsupported expression")


def parse_measurement(text):
    """Parse a measurement expression into an exact fractions.Fraction, or
    None if it's blank or not a valid one. Supports decimals, fractions,
    mixed numbers, and +, -, *, / and parentheses -- e.g. '2 + 1/2' and
    '2 1/2' both parse to Fraction(5, 2)."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    text = _insert_implicit_plus(text)
    try:
        tree = ast.parse(text, mode="eval")
        return _eval_fraction_node(tree)
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError):
        return None


def format_fraction(value, denominator):
    """Round a length to the nearest 1/denominator and format it as a
    mixed number string, whole and fraction joined with a dash so it's
    unambiguously one number (e.g. 2.53 with denominator=32 -> '2-17/32',
    not '2 17/32' which can misread as two separate numbers)."""
    if value is None:
        return None
    negative = value < 0
    value = abs(value)
    n = round(value * denominator)
    whole, rem = divmod(n, denominator)
    if rem == 0:
        s = f"{whole}"
    else:
        g = math.gcd(rem, denominator)
        rem_r, den_r = rem // g, denominator // g
        s = f"{rem_r}/{den_r}" if whole == 0 else f"{whole}-{rem_r}/{den_r}"
    return ("-" if negative else "") + s


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
                 parallel_to=None, display_mode=None, display_unit=None,
                 display_denominator=None):
        self.id = Line._next_id
        Line._next_id += 1
        self.color = color            # 'red' | 'green' | 'blue'
        self.x1, self.y1 = x1, y1
        self.x2, self.y2 = x2, y2
        self.known_length = known_length   # real-world length, or None
        self.unit = unit
        self.parallel_to = parallel_to     # id of reference line, or None
        # Per-line display overrides -- None means "use the axis default /
        # calibration unit / program-wide Fraction Denominator setting" (see
        # ProjectTab.effective_display). Independent of known_length: even a
        # computed (unknown) line can be told to show as a fraction, rounded
        # to its own nearest denominator, or converted to a different unit,
        # on its own.
        self.display_mode = display_mode    # None | 'decimal' | 'fraction'
        self.display_unit = display_unit    # None | one of UNIT_CHOICES
        self.display_denominator = display_denominator  # None | one of FRACTION_DENOMINATOR_CHOICES
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
            "display_mode": self.display_mode, "display_unit": self.display_unit,
            "display_denominator": self.display_denominator,
        }

    @classmethod
    def from_dict(cls, d):
        ln = cls(d["color"], d["x1"], d["y1"], d["x2"], d["y2"],
                  d.get("known_length"), d.get("unit"), d.get("parallel_to"),
                  d.get("display_mode"), d.get("display_unit"),
                  d.get("display_denominator"))
        ln.id = d["id"]
        Line._next_id = max(Line._next_id, ln.id + 1)
        return ln


class Ellipse:
    """A circle-in-perspective, defined by its center plus 2 conjugate
    diameters (each stored as a Line whose 2 endpoints are always kept
    exactly opposite the center -- see ProjectTab.mirror_diameter_endpoint).
    line_a_id/line_b_id point at those 2 Lines by id; the Lines themselves
    carry the color (orange until reassigned to an axis -- see
    ProjectTab.set_line_color, which works the same for any line, not
    just a circle's), their own known_length, and are what render, get
    selected, deleted, etc. -- Ellipse itself only tracks the shared
    center and its CURRENT SHAPE. There's no separate "circle known
    size" anymore: the 2 diameter lines are simply kept linked to the
    SAME known_length/unit as each other (see ProjectTab.commit_known_length),
    the same as any other measured property of the line.

    The shape (semi_a, semi_b, phi -- see ellipse_canonical) is stored here,
    not re-derived live from the 2 lines on every call. That distinction
    matters: RESHAPING (freely moving one diameter's endpoint) genuinely
    redefines the shape, so it calls recompute_shape() to re-derive and
    store a fresh one from the 2 lines' new positions. But ROTATING (see
    ProjectTab.rotating_diameter) is specifically about sliding a point
    along an ALREADY-FIXED shape without changing it -- and after an
    independent rotate, the 2 diameter lines are no longer necessarily a
    "conjugate pair" 90 degrees apart in this shape's own parameter space
    (e.g. line A rotated to align with one axis, line B left alone or
    aligned to a different axis), so re-deriving the shape live from
    (line_a, line_b) at that point would produce a DIFFERENT ellipse than
    the one actually being shown -- see the design note above
    ellipse_canonical for why only a true conjugate pair reproduces a
    given shape via the P(t) formula. Keeping the shape as its own stored
    field, updated only on reshape/creation (never on rotate or a whole-
    ellipse translate, which don't change it), is what makes "rotate one
    diameter without disturbing the other, or the circle's boundary"
    actually hold.
    """

    _next_id = 1

    def __init__(self, cx, cy, line_a_id, line_b_id,
                 semi_a=0.0, semi_b=0.0, phi=0.0):
        self.id = Ellipse._next_id
        Ellipse._next_id += 1
        self.cx, self.cy = cx, cy
        self.line_a_id = line_a_id
        self.line_b_id = line_b_id
        self.semi_a, self.semi_b, self.phi = semi_a, semi_b, phi

    def to_dict(self):
        return {
            "id": self.id, "cx": self.cx, "cy": self.cy,
            "line_a_id": self.line_a_id, "line_b_id": self.line_b_id,
            "semi_a": self.semi_a, "semi_b": self.semi_b, "phi": self.phi,
        }

    @classmethod
    def from_dict(cls, d):
        el = cls(d["cx"], d["cy"], d["line_a_id"], d["line_b_id"],
                  d.get("semi_a", 0.0), d.get("semi_b", 0.0), d.get("phi", 0.0))
        el.id = d["id"]
        Ellipse._next_id = max(Ellipse._next_id, el.id + 1)
        return el

    def canonical(self, tab=None):
        """(semi_a, semi_b, phi) -- this ellipse's current, STORED shape
        (see the class docstring for why it's stored rather than
        re-derived live). `tab` is accepted but unused, kept only so
        existing call sites (`ellipse.canonical(self)`) don't all need
        updating."""
        return self.semi_a, self.semi_b, self.phi

    def recompute_shape(self, tab):
        """Re-derive the shape from the 2 diameter lines' CURRENT
        positions and store it -- call this after a RESHAPE (a diameter
        endpoint moved freely, not via rotate), including right after the
        Circle tool creates a new one. Never call this after a rotate or a
        whole-ellipse translate; neither actually changes the shape, and
        re-deriving it from lines that are no longer a true conjugate pair
        (see the class docstring) would silently corrupt it."""
        a = tab._line_by_id(self.line_a_id)
        b = tab._line_by_id(self.line_b_id)
        ux, uy = a.x2 - self.cx, a.y2 - self.cy
        vx, vy = b.x2 - self.cx, b.y2 - self.cy
        self.semi_a, self.semi_b, self.phi = ellipse_canonical(self.cx, self.cy, ux, uy, vx, vy)


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
        self.ellipses = []                # list[Ellipse] -- circles-in-perspective
        self.selected_line_ids = []       # for compare / delete / calibrate
        self.drag_start_img = None        # (ix, iy) anchor for a hold-and-drag line
        self.drag_temp_id = None          # canvas id of the dashed preview line
        self._circle_preview_ids = []     # canvas ids of the Circle tool's dashed preview
        self.dragging_vertex = None        # (Line, endpoint_index) while moving a vertex
        self.moving_line = None           # (Line, orig_x1, orig_y1, orig_x2, orig_y2,
                                            # anchor_ix, anchor_iy) while Shift-dragging a
                                            # whole line (grabbed by its body, not an
                                            # endpoint) -- translates both endpoints by the
                                            # same delta, so orientation/length don't change
        self.press_canvas = None          # (x, y) where the current press started
        self.press_moved = False          # did the mouse move past the click threshold?
        self.click_draw_start = None      # (ix, iy) start of a click-to-click pending line
        # Vertex-move follow state -- see _constrained_vertex_point,
        # on_canvas_press's vertex_follow_active check, and
        # cancel_vertex_follow. A Shift+click on a vertex that releases
        # without dragging past the threshold detaches that endpoint and
        # has it follow the cursor (like click-to-click line drawing) until
        # the next click places it; holding Shift while it follows keeps it
        # collinear with the line's original direction, for extending or
        # shortening a line precisely.
        self.vertex_follow_active = False
        self.vertex_move_anchor = None    # (ax, ay, orig_angle) of the line's OTHER,
                                            # fixed endpoint and its pre-move direction
        self.vertex_move_orig = None      # (ox, oy) the moved endpoint had before this
                                            # gesture started, to restore on Escape
        # "Make Parallel" tool state -- see start_make_parallel/_maybe_advance_parallel_pick.
        # None while inactive; "first" while waiting to pick the reference
        # line; "second" while waiting to pick the line that gets rotated
        # to match it. parallel_first_id holds the reference line's id
        # once chosen, only meaningful while parallel_picking == "second".
        self.parallel_picking = None
        self.parallel_first_id = None
        # Circle draw tool -- see on_canvas_press's circle-mode handling.
        # None while inactive; while active, a list of the
        # image-space points clicked so far this circle (0, 1, or 2 of them --
        # a 3rd click finishes it): [] just after entering Circle mode/undoing
        # a click, [(cx,cy)] after the center click, [(cx,cy),(ax,ay)] after
        # the first circumference point.
        self.circle_points = None
        # Rotate a diameter line within its ellipse's already-fixed boundary
        # (Ctrl+Shift+drag on one of its endpoints) -- see
        # ellipse_param_for_direction. (ellipse, line, endpoint_index,
        # semi_a, semi_b, phi) frozen at the start of the gesture, so the
        # shape itself never changes mid-rotate, only where along it the
        # point sits.
        self.rotating_diameter = None
        # Translate a whole ellipse (Shift-drag on its curve, or on the BODY
        # -- not an endpoint -- of one of its diameter lines): (ellipse,
        # orig_cx, orig_cy, orig_a_coords, orig_b_coords, anchor_ix, anchor_iy).
        # orig_a_coords/orig_b_coords are each (x1,y1,x2,y2) as of gesture
        # start; every point (center + both lines' 4 endpoints) shifts by
        # the same delta, so the ellipse's shape/orientation never changes.
        self.moving_ellipse = None
        # Undo/redo -- see _push_undo/_restore_snapshot/undo/redo. Each
        # entry is a full snapshot of everything an action here can
        # change: the line list, rotation, and (for rotate specifically)
        # the image/pyramid. Pushed right before a mutation is applied, so
        # undo restores exactly the state as of just before that action.
        self.undo_stack = []
        self.redo_stack = []
        self.known_length_edit_snapshot = None  # (length_str, unit_str) before the current
                                                  # known-length edit, for Escape to revert to
        self._suppress_next_focus_snapshot = False  # see maybe_start_length_edit /
                                                       # _capture_length_edit_snapshot
        # NOT used to resolve display mode any more (see effective_display)
        # -- kept only so old session/project files that still have this
        # key load without error. It used to auto-lock an axis to
        # decimal/fraction based on how the last-typed known length looked
        # (a '/' vs. a '.'), but that silently fought with Settings >
        # Default Display Mode -- flipping the global setting had no
        # visible effect the moment ANY line on an axis had ever been
        # typed with a decimal point, which is most of them. Display mode
        # is now resolved purely as: this line's own Display-dropdown
        # override, else the live, global Default Display Mode setting --
        # no hidden per-axis state to get stuck.
        self.axis_display_mode = {color: None for color in AXIS_COLORS}

        self._build_body()

    # ---------------------------------------------------------- UI setup
    def _build_body(self):
        # The side panel (options + line list, on the LEFT) and the image
        # canvas sit in a horizontal PanedWindow so the divider between
        # them can be dragged to resize the side panel -- plain side-by-
        # side .pack() calls (the old layout) can't do that; a sash can.
        # canvas_frame gets stretch="always" so widening/narrowing the
        # whole window grows/shrinks the CANVAS side, leaving whatever
        # width you dragged the side panel to alone.
        paned = tk.PanedWindow(self, orient="horizontal", sashwidth=6,
                                sashrelief="raised", bg="#c0c0c0",
                                bd=0, opaqueresize=True)
        paned.pack(fill="both", expand=True)

        # The side panel's actual content (known-length field, Circle
        # panel, line list, axis calibration) is built inside `side`, same
        # as before -- but `side` now lives inside a Canvas+Scrollbar so
        # that if the window is too short for everything to fit, a
        # vertical scrollbar appears instead of clipping/squashing the
        # bottom of the panel. See _on_side_frame_configure /
        # _on_side_canvas_configure below for how the scrollregion and
        # inner-frame width are kept in sync.
        side_outer = ttk.Frame(paned)
        paned.add(side_outer, width=300, minsize=180)

        # A plain tk.Canvas defaults to a white background, which stands
        # out against the rest of the (themed, tan/gray) UI -- match
        # whatever the ttk theme actually uses for an ordinary frame so
        # the wrapper is invisible when nothing needs to scroll.
        themed_bg = ttk.Style().lookup("TFrame", "background") or side_outer.cget("background")
        side_canvas = tk.Canvas(side_outer, highlightthickness=0, bg=themed_bg)
        side_scrollbar = ttk.Scrollbar(side_outer, orient="vertical",
                                        command=side_canvas.yview)
        side_canvas.configure(yscrollcommand=side_scrollbar.set)
        # The scrollbar itself is only packed once content actually
        # overflows the visible height (see _update_side_scrollbar) --
        # it starts unpacked so a tall-enough window never shows one.
        side_canvas.pack(side="left", fill="both", expand=True)

        side = ttk.Frame(side_canvas, padding=6)
        side_window = side_canvas.create_window((0, 0), window=side, anchor="nw")

        self._side_scrollbar_visible = False

        def _update_side_scrollbar():
            bbox = side_canvas.bbox("all")
            content_h = bbox[3] - bbox[1] if bbox else 0
            needed = content_h > side_canvas.winfo_height()
            if needed and not self._side_scrollbar_visible:
                side_scrollbar.pack(side="right", fill="y")
                self._side_scrollbar_visible = True
            elif not needed and self._side_scrollbar_visible:
                side_scrollbar.pack_forget()
                self._side_scrollbar_visible = False

        def _on_side_frame_configure(event):
            side_canvas.configure(scrollregion=side_canvas.bbox("all"))
            _update_side_scrollbar()
        side.bind("<Configure>", _on_side_frame_configure)

        def _on_side_canvas_configure(event):
            # Keep the inner frame exactly as wide as the canvas viewport
            # so its widgets (comboboxes, buttons, ...) fill it properly
            # instead of staying whatever width they'd shrink to on their
            # own -- only the height is ever meant to scroll.
            side_canvas.itemconfig(side_window, width=event.width)
            _update_side_scrollbar()
        side_canvas.bind("<Configure>", _on_side_canvas_configure)

        def _on_side_mousewheel(event):
            if self._side_scrollbar_visible:
                side_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        # Only scrolls with the wheel while the cursor is actually over the
        # side panel (bind_all while entered, unbind_all on leave) so
        # scrolling over the canvas/image is never hijacked by this; the
        # handler itself is a no-op whenever there's nothing to scroll.
        side_canvas.bind("<Enter>", lambda e: side_canvas.bind_all("<MouseWheel>", _on_side_mousewheel))
        side_canvas.bind("<Leave>", lambda e: side_canvas.unbind_all("<MouseWheel>"))

        # canvas (fills the rest of the tab). No scrollbars of its own --
        # panning is done by dragging with the middle mouse button, and
        # only the visible region is ever rendered (see _render_image),
        # which is what keeps zooming in on a big photo from lagging.
        canvas_frame = ttk.Frame(paned)
        paned.add(canvas_frame, minsize=300, stretch="always")

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

        # Color/axis for the selected line -- ANY line can be reassigned
        # here, not just a circle's diameter lines (that used to be a
        # separate, circle-only "Circle panel"; recoloring is now just a
        # normal property of any line, same as its known length). Orange
        # means "not on a measurement axis yet" (see ORANGE_COLOR) --
        # meaningful for a circle's diameter lines, which start out
        # orange until assigned, but selectable for any line. See
        # commit_line_color / set_line_color.
        color_row = ttk.Frame(known_frame)
        color_row.pack(fill="x", pady=(6, 0))
        ttk.Label(color_row, text="Color:").pack(side="left")
        self.line_color_var = tk.StringVar(value=ORANGE_COLOR)
        self.line_color_buttons = {}
        for color in (ORANGE_COLOR,) + tuple(AXIS_COLORS.keys()):
            meta = line_color_meta(color)
            # Just "none" / the axis letter -- the button's own background
            # color already shows which color this is, so repeating the
            # color name in the label too would just make these buttons
            # wider than they need to be.
            label = "none" if color == ORANGE_COLOR else meta["axis"]
            btn = tk.Radiobutton(
                color_row, text=label, variable=self.line_color_var, value=color,
                indicatoron=False, fg="white", bg=meta["hex"], selectcolor=meta["hex"],
                activebackground=meta["select_hex"], state="disabled",
                command=self.commit_line_color)
            btn.pack(side="left", padx=2)
            self.line_color_buttons[color] = btn

        # Display override for the selected line -- independent of the known-
        # length field above: it controls how THIS line's length (known or
        # computed) is shown, not what its calibration is. "(auto)" means
        # "use the axis's default" (decimal, unless a fraction was typed
        # into some line's known length on this axis -- see
        # commit_known_length) for mode, "the axis's calibration unit" for
        # unit, and "the program-wide Settings > Fraction Denominator" for
        # denom -- so most lines never need to touch these at all.
        display_row = ttk.Frame(known_frame)
        display_row.pack(fill="x", pady=(6, 0))
        ttk.Label(display_row, text="Display:").pack(side="left")
        self.display_mode_var = tk.StringVar(value="(auto)")
        self.display_mode_box = ttk.Combobox(
            display_row, textvariable=self.display_mode_var, width=8, state="disabled",
            values=["(auto)", "decimal", "fraction"])
        self.display_mode_box.pack(side="left", padx=(4, 0))
        self.display_mode_box.bind("<<ComboboxSelected>>", self.commit_display_settings)

        self.display_unit_var = tk.StringVar(value="(auto)")
        self.display_unit_box = ttk.Combobox(
            display_row, textvariable=self.display_unit_var, width=6, state="disabled",
            values=["(auto)"] + UNIT_CHOICES)
        self.display_unit_box.pack(side="left", padx=(4, 0))
        self.display_unit_box.bind("<<ComboboxSelected>>", self.commit_display_settings)

        # Only meaningful in fraction mode -- kept out of the layout (not
        # just disabled) whenever the selected line is effectively showing
        # decimal, so it doesn't sit there uselessly. See
        # _update_denom_row_visibility.
        self.denom_row = ttk.Frame(known_frame)
        self.denom_row.pack(fill="x", pady=(4, 0))
        ttk.Label(self.denom_row, text="Fraction denom:").pack(side="left")
        self.display_denominator_var = tk.StringVar(value="(auto)")
        self.display_denominator_box = ttk.Combobox(
            self.denom_row, textvariable=self.display_denominator_var, width=8, state="disabled",
            values=["(auto)"] + [f"1/{d}" for d in FRACTION_DENOMINATOR_CHOICES])
        self.display_denominator_box.pack(side="left", padx=(4, 0))
        self.display_denominator_box.bind("<<ComboboxSelected>>", self.commit_display_settings)
        self._denom_row_visible = True

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
        self.moving_line = None
        self.vertex_follow_active = False
        self.vertex_move_anchor = None
        self.vertex_move_orig = None
        self.drag_start_img = None
        self.drag_temp_id = None
        self.click_draw_start = None
        self.circle_points = None
        self.rotating_diameter = None
        self.moving_ellipse = None
        self.lines = []
        self.ellipses = []
        self.selected_line_ids = []
        self.project_path = None
        self.dirty = False
        self.undo_stack = []
        self.redo_stack = []
        self.cancel_make_parallel()
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
        self._push_undo()
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
        self._restore_temp_line()

    def _restore_temp_line(self):
        """canvas.delete('all') above just wiped the dashed in-progress
        line preview along with everything else -- redraw() only
        recreates already-committed lines, so without this a preview that
        was showing before a zoom/pan/resize would vanish and, worse,
        STAY hidden: self.drag_temp_id still points at a canvas item that
        no longer exists, so the next _update_temp_line() call would just
        call .coords() on that dead id (a silent no-op) instead of
        creating a fresh one. Reset it here so the next update recreates
        it -- and if a line is actually mid-draw right now, redraw the
        preview immediately using the current pointer position rather
        than waiting for the next mouse-move event, since e.g. a
        hold-and-drag held steady while zooming with the scroll wheel
        doesn't itself generate one."""
        self.drag_temp_id = None
        anchor = self.drag_start_img or self.click_draw_start
        if anchor is None or self.app.mode.get() != "draw":
            return
        px = self.canvas.winfo_pointerx() - self.canvas.winfo_rootx()
        py = self.canvas.winfo_pointery() - self.canvas.winfo_rooty()
        self._update_temp_line(*anchor, px, py, False)

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
        otherwise derived from its axis's calibration. Always in whichever
        unit that calibration used -- see effective_display for a line's
        own display unit/mode (which may differ). Orange (an ellipse
        diameter not yet assigned to a real axis) never borrows calibration
        from another orange line -- orange isn't a measurement axis, it's
        just "no axis yet", so two unrelated circles' unassigned diameters
        must never cross-calibrate each other just for sharing that color."""
        if line.known_length:
            return line.known_length, line.unit
        if line.color == ORANGE_COLOR:
            return None, None
        upp, unit = self.axis_scale(line.color)
        if upp is None:
            return None, None
        return line.pixel_length() * upp, unit

    def effective_display(self, line):
        """The (value, unit, mode, denominator) a line should actually be
        SHOWN with -- computed_length()'s value/unit, converted to this
        line's own display_unit override if it has one (falling back to
        the calibration unit if that conversion isn't possible, e.g. px),
        its display_mode override if it has one (else the program-wide
        Settings > Default Display Mode, followed live -- there's no
        per-axis state in between, so this always reflects the current
        setting the instant it's changed, for any line without its own
        Display-dropdown override), and its own display_denominator
        override if it has one (else the program-wide Settings > Fraction
        Denominator)."""
        real, calib_unit = self.computed_length(line)
        if real is None:
            return None, None, None, None
        unit = line.display_unit or calib_unit
        value = convert_length(real, calib_unit, unit)
        if value is None:
            unit = calib_unit
            value = real
        mode = line.display_mode or self.app.default_display_mode.get()
        denom = line.display_denominator or self.app.fraction_denominator.get()
        return value, unit, mode, denom

    def format_display(self, value, unit, mode, denom=None):
        if value is None:
            return "?"
        if mode == "fraction":
            denom = denom or self.app.fraction_denominator.get()
            return f"{format_fraction(value, denom)} {unit}"
        return fmt_len(value, unit)

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
    def _nearest_point_on_segment(p, a, b):
        ax, ay = a
        bx, by = b
        px, py = p
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        if length2 == 0:
            return a
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / length2))
        return (ax + t * dx, ay + t * dy)

    @classmethod
    def _point_segment_distance(cls, p, a, b):
        return dist(p, cls._nearest_point_on_segment(p, a, b))

    def _apply_snap(self, ix, iy, exclude_ids=()):
        """When Snap Mode is on, pull the image-space point (ix, iy) onto
        the nearest OTHER line's vertex within the usual hit-test
        tolerance, else the nearest point along that line's body, else
        leave it untouched. A vertex match wins over a mid-line match even
        if the mid-line point happens to be marginally closer, since
        landing exactly on an existing corner is normally what you want.
        exclude_ids skips lines that shouldn't act as snap targets for this
        particular point -- e.g. the line whose own vertex is being
        dragged, so it doesn't snap onto its own other endpoint or its own
        body. Used for both drawing new lines and Shift-dragging an
        existing vertex; not applied to a Shift-dragged whole-line move
        (translation, not point-placement)."""
        if not self.app.snap_mode.get():
            return ix, iy
        tol_img = HIT_TOLERANCE / self.scale
        best_pt, best_d = None, tol_img
        for ln in self.lines:
            if ln.id in exclude_ids:
                continue
            for vx, vy in ((ln.x1, ln.y1), (ln.x2, ln.y2)):
                d = dist((ix, iy), (vx, vy))
                if d <= best_d:
                    best_pt, best_d = (vx, vy), d
        # A circle's CENTER is as meaningful a point as any line's vertex
        # (e.g. snapping a new line's end to the exact center of a circle),
        # so it's checked in this same top-priority tier.
        for el in self.ellipses:
            d = dist((ix, iy), (el.cx, el.cy))
            if d <= best_d:
                best_pt, best_d = (el.cx, el.cy), d
        if best_pt is not None:
            return best_pt
        for ln in self.lines:
            if ln.id in exclude_ids:
                continue
            proj = self._nearest_point_on_segment((ix, iy), (ln.x1, ln.y1), (ln.x2, ln.y2))
            d = dist((ix, iy), proj)
            if d <= best_d:
                best_pt, best_d = proj, d
        # A circle's EDGE (its curve, not its 2 straight diameter lines,
        # which the loop above already covers) is the same tier as
        # snapping onto a line's body -- neither is as strong a match as an
        # exact vertex/center, but either is still worth pulling onto.
        for el in self.ellipses:
            semi_a, semi_b, phi = el.canonical(self)
            proj = ellipse_nearest_point(el.cx, el.cy, semi_a, semi_b, phi, ix, iy)
            d = dist((ix, iy), proj)
            if d <= best_d:
                best_pt, best_d = proj, d
        return best_pt if best_pt is not None else (ix, iy)

    def _parallel_reference(self):
        """The line a Shift-constrained drag should run parallel to: the
        single selected line if there is one, otherwise the last line
        drawn. None if there's nothing to reference yet."""
        if len(self.selected_line_ids) == 1:
            ln = self._line_by_id(self.selected_line_ids[0])
            if ln:
                return ln
        return self.lines[-1] if self.lines else None

    @staticmethod
    def _project_onto_direction(ax, ay, px, py, angle):
        """Project (px, py) onto the line through (ax, ay) running in the
        given direction (radians)."""
        dirx, diry = math.cos(angle), math.sin(angle)
        vx, vy = px - ax, py - ay
        proj_len = vx * dirx + vy * diry
        return ax + proj_len * dirx, ay + proj_len * diry

    def _constrain_to_reference(self, ix1, iy1, ix2, iy2, ref):
        """Project (ix2, iy2) onto the line through (ix1, iy1) running in
        ref's direction, so the new segment is parallel to ref."""
        return self._project_onto_direction(ix1, iy1, ix2, iy2, ref.angle())

    def _constrained_vertex_point(self, ln, anchor, orig_angle, ix, iy, shift_held):
        """Where a vertex being moved (drawing aside -- this is for an
        EXISTING line's endpoint via Shift+grab) should land right now.
        Shift held: project onto the ray through `anchor` -- the line's
        OTHER, fixed endpoint -- running in `orig_angle`, the line's own
        direction as of the moment this move started (captured once, not
        re-read live, since the line's current angle changes as the point
        moves). That's what lets you extend or shorten a line while
        keeping it perfectly collinear with itself, the vertex-move
        equivalent of Shift constraining a new line parallel to a
        reference. Shift not held: the point moves freely. Either way,
        Snap Mode (if on) is applied last, excluding this line itself so
        it never snaps onto its own other endpoint or its own body."""
        if shift_held:
            ax, ay = anchor
            ix, iy = self._project_onto_direction(ax, ay, ix, iy, orig_angle)
        return self._apply_snap(ix, iy, exclude_ids=(ln.id,))

    # --------------------------------------------------- make-parallel tool
    def start_make_parallel(self):
        """Toolbar button / 'P': turn an existing (already-drawn) second
        line parallel to a first one, picked by clicking either on the
        canvas or in the side list. If exactly one line is already
        selected when this starts, it's taken as the reference line
        immediately and picking begins on the second one; otherwise the
        first click picks the reference. Esc (or pressing the button/'P'
        again) cancels at any point."""
        if not self.pil_image:
            self.app.set_status("Open an image first.")
            return
        if len(self.lines) < 2:
            self.app.set_status("Need at least two lines to make one parallel to another.")
            return
        self.cancel_click_draw()
        self.parallel_picking = "first"
        self.parallel_first_id = None
        self.app.set_status(
            "Make Parallel: click the REFERENCE line (canvas or list). Esc to cancel.")
        self.app.refresh_toolbar_hint(self)
        # If a single line was already selected, this immediately promotes
        # it to the reference and moves straight to picking the second
        # line -- see _maybe_advance_parallel_pick.
        self._maybe_advance_parallel_pick()

    def toggle_make_parallel(self):
        if self.parallel_picking:
            self.cancel_make_parallel()
        else:
            self.start_make_parallel()

    def cancel_make_parallel(self):
        if not self.parallel_picking:
            return
        self.parallel_picking = None
        self.parallel_first_id = None
        self.app.set_status("Make Parallel cancelled.")
        self.app.refresh_toolbar_hint(self)

    def _maybe_advance_parallel_pick(self):
        """Called after every selection change (canvas click or side-list
        click both funnel through select_line/on_tree_select) while the
        Make Parallel tool is active. A single freshly-selected line
        advances the tool to its next step; anything else (nothing
        selected, or a multi-select) just leaves it waiting."""
        if not self.parallel_picking:
            return
        if len(self.selected_line_ids) != 1:
            return
        picked_id = self.selected_line_ids[0]
        if self.parallel_picking == "first":
            self.parallel_first_id = picked_id
            self.parallel_picking = "second"
            ln = self._line_by_id(picked_id)
            self.app.set_status(
                f"Make Parallel: line #{ln.id} ({ln.color}) is the reference -- "
                "now click the line to make parallel to it (canvas or list). "
                "Esc to cancel.")
            self.app.refresh_toolbar_hint(self)
            return
        if picked_id == self.parallel_first_id:
            self.app.set_status(
                "Make Parallel: pick a different line than the reference. Esc to cancel.")
            return
        ref_id = self.parallel_first_id
        self.parallel_picking = None
        self.parallel_first_id = None
        self.app.refresh_toolbar_hint(self)
        self._apply_parallel(ref_id, picked_id)

    def _apply_parallel(self, ref_id, target_id):
        """Rotate the target line around its OWN first endpoint (x1, y1
        stays fixed) so it runs in the same direction as the reference
        line, keeping the target's own pixel length exactly as it was --
        this only changes direction, never length or the calibration/known
        length either line already has. Whichever of the two directions
        along that line (ref's angle, or its exact opposite) is closer to
        the target's current direction is used, so the line pivots into
        place rather than unexpectedly flipping end-for-end."""
        ref = self._line_by_id(ref_id)
        target = self._line_by_id(target_id)
        if not ref or not target:
            return
        length = target.pixel_length()
        if length <= 0:
            return
        ref_ang = ref.angle()
        cur_ang = target.angle()

        def ang_diff(a, b):
            return abs((a - b + math.pi) % (2 * math.pi) - math.pi)

        use_ang = ref_ang if ang_diff(ref_ang, cur_ang) <= ang_diff(ref_ang + math.pi, cur_ang) \
            else ref_ang + math.pi

        ellipse = self.ellipse_for_line(target.id)
        if ellipse:
            # A Circle diameter line can't just pivot around one endpoint
            # like an ordinary line (that would leave it off its own
            # circle) -- instead it ROTATES within its already-fixed
            # ellipse boundary to the nearest point in the reference's
            # direction, same mechanism as a manual rotate (Ctrl+Shift-
            # drag) -- see rotating_diameter / ellipse_param_for_direction.
            semi_a, semi_b, phi = ellipse.canonical(self)
            dx, dy = math.cos(use_ang), math.sin(use_ang)
            t = ellipse_param_for_direction(ellipse.cx, ellipse.cy, semi_a, semi_b, phi, dx, dy)
            px, py = ellipse_point_at(ellipse.cx, ellipse.cy, semi_a, semi_b, phi, t)
            self._push_undo()
            target.x2, target.y2 = px, py
            self.mirror_diameter_endpoint(ellipse, target, 2)
        else:
            self._push_undo()
            target.x2 = target.x1 + length * math.cos(use_ang)
            target.y2 = target.y1 + length * math.sin(use_ang)
        target.parallel_to = ref.id
        self.mark_dirty()
        self.select_line(target.id)
        self.app.set_status(
            f"Line #{target.id} ({target.color}) is now parallel to "
            f"line #{ref.id} ({ref.color}).")

    # ------------------------------------------------------------- undo/redo
    def _snapshot(self):
        """Everything an undoable action here can change: the line list
        (deep-copied -- Line objects are mutated in place elsewhere, so a
        shallow copy of the list would still share the same Line objects
        and "restoring" it would restore nothing), rotation, and the
        image/pyramid (only rotate_image actually reassigns these, but
        capturing a reference here is free -- it's the deep-copy of
        `lines` that costs anything, and line lists are small)."""
        return {
            "lines": copy.deepcopy(self.lines),
            "ellipses": copy.deepcopy(self.ellipses),
            "rotation_turns": self.rotation_turns,
            "pil_image": self.pil_image,
            "pyramid": self.pyramid,
            "selected_line_ids": list(self.selected_line_ids),
        }

    def _push_undo(self):
        """Call right BEFORE applying a mutation, so the pushed snapshot is
        the state as of just before it. Starting a new undoable action
        clears the redo stack, same as every other undo/redo implementation --
        once you've done something new, "redo" no longer means anything."""
        self.undo_stack.append(self._snapshot())
        if len(self.undo_stack) > MAX_UNDO_STEPS:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _restore_snapshot(self, snap):
        # rotate_image is the only action that reassigns pil_image/pyramid
        # (a new Image object each time, never mutated in place) -- when
        # a snapshot carries a different one than what's currently shown,
        # the on-screen background itself needs repainting (_render_image),
        # not just the line overlay (redraw alone never touches the
        # background image).
        image_changed = snap["pil_image"] is not self.pil_image
        self.lines = snap["lines"]
        self.ellipses = snap["ellipses"]
        self.rotation_turns = snap["rotation_turns"]
        self.pil_image = snap["pil_image"]
        self.pyramid = snap["pyramid"]
        valid_ids = {ln.id for ln in self.lines}
        self.selected_line_ids = [i for i in snap["selected_line_ids"] if i in valid_ids]
        self.mark_dirty()
        if image_changed:
            self._render_image()
        else:
            self.redraw()
        self.populate_known_length_field()

    def undo(self):
        if not self.undo_stack:
            self.app.set_status("Nothing to undo.")
            return
        self.redo_stack.append(self._snapshot())
        snap = self.undo_stack.pop()
        self._restore_snapshot(snap)
        self.app.set_status("Undid last action.")

    def redo(self):
        if not self.redo_stack:
            self.app.set_status("Nothing to redo.")
            return
        self.undo_stack.append(self._snapshot())
        snap = self.redo_stack.pop()
        self._restore_snapshot(snap)
        self.app.set_status("Redid last undone action.")

    def on_canvas_press(self, event):
        if not self.pil_image:
            return

        # "Make Parallel" is picking a line right now -- every click, in
        # EITHER Draw or Select mode, just picks whichever line is under
        # the cursor (or does nothing on a miss, staying in picking mode)
        # instead of drawing or doing anything else. See
        # start_make_parallel / _maybe_advance_parallel_pick.
        if self.parallel_picking:
            ix, iy = self.canvas_event_to_img(event)
            tol_img = HIT_TOLERANCE / self.scale
            ln = self.find_line_near(ix, iy, tol_img)
            if ln:
                self.select_line(ln.id)
            return

        # A line started by a short click is waiting for its closing click --
        # this press is that closing click, whatever mode we're in.
        if self.click_draw_start is not None:
            ix1, iy1 = self.click_draw_start
            self.click_draw_start = None
            ix2, iy2 = self.canvas_event_to_img(event)
            self._finalize_line(ix1, iy1, ix2, iy2, bool(event.state & 0x0001))
            return

        # A vertex picked up with Shift+click (released without dragging
        # past the threshold -- see on_canvas_release) is following the
        # cursor right now, waiting for this next click to place it. Hold
        # Shift on this closing click too to keep it collinear with the
        # line's original direction.
        if self.vertex_follow_active:
            ln, idx = self.dragging_vertex
            ix2, iy2 = self.canvas_event_to_img(event)
            ellipse = self.ellipse_for_line(ln.id)
            if ellipse:
                # Ellipse diameter endpoints reshape freely, no collinear-
                # extend constraint (that's a plain-line feature) -- and the
                # opposite end of this SAME diameter line is kept mirrored
                # through the center, which is what actually redefines the
                # ellipse's shape (see Ellipse.canonical).
                fx, fy = self._apply_snap(ix2, iy2, exclude_ids=(ln.id,))
            else:
                ax, ay, orig_angle = self.vertex_move_anchor
                fx, fy = self._constrained_vertex_point(
                    ln, (ax, ay), orig_angle, ix2, iy2, bool(event.state & 0x0001))
            if idx == 1:
                ln.x1, ln.y1 = fx, fy
            else:
                ln.x2, ln.y2 = fx, fy
            if ellipse:
                self.mirror_diameter_endpoint(ellipse, ln, idx)
                ellipse.recompute_shape(self)
            self.vertex_follow_active = False
            self.dragging_vertex = None
            self.vertex_move_anchor = None
            self.vertex_move_orig = None
            self.redraw()
            self.mark_dirty()
            self.app.set_status(f"Moved line #{ln.id}'s endpoint.")
            return

        ix, iy = self.canvas_event_to_img(event)
        tol_img = HIT_TOLERANCE / self.scale

        # Circle draw tool: every click just places the next of this
        # circle's 3 defining points (center, then 2 circumference points --
        # see _finalize_circle). Checked before Ctrl/Shift handling below so
        # those modifiers don't interfere with placing points -- Circle mode
        # doesn't use them for anything else.
        if self.app.mode.get() == "circle" and not (event.state & 0x0001) and not (event.state & 0x0004):
            # A plain click places the next point. Shift/Ctrl held falls
            # through to the normal Shift-grab (reshape/rotate/move an
            # EXISTING circle) or Ctrl-click (select) handling below, same
            # as Draw/Select mode -- Circle mode only owns plain clicks.
            if self.circle_points is None:
                self.circle_points = []
            ix, iy = self._apply_snap(ix, iy)
            self.circle_points.append((ix, iy))
            if len(self.circle_points) == 1:
                self.app.set_status(
                    "Circle: click a point on its circumference (1st diameter).")
            elif len(self.circle_points) == 2:
                self.app.set_status(
                    "Circle: click a 2nd circumference point in a different "
                    "direction across the circle to finish it, or Esc to cancel.")
            else:
                self._finalize_circle()
            return

        # Ctrl+Shift+grab on one of an ellipse's diameter endpoints rotates
        # that diameter WITHIN its already-fixed ellipse boundary -- the
        # ellipse's shape never changes, only where along it this point (and
        # its mirrored opposite) sit. Checked before the plain Ctrl+click
        # (select-any-color) handling just below, since Ctrl+Shift together
        # would otherwise just look like a Ctrl+click to that check.
        if bool(event.state & 0x0001) and bool(event.state & 0x0004):  # Shift+Control
            hit = self.find_vertex_near(ix, iy, tol_img)
            if hit:
                vline, vidx = hit
                ellipse = self.ellipse_for_line(vline.id)
                if ellipse:
                    self._push_undo()
                    semi_a, semi_b, phi = ellipse.canonical(self)
                    self.rotating_diameter = (ellipse, vline, vidx, semi_a, semi_b, phi)
                    self.select_line(vline.id)
                    return
            # not a rotate-able hit -- fall through to the normal handling below

        # Ctrl+click selects whatever line is under the cursor, regardless
        # of its color and regardless of Draw/Select mode -- a miss does
        # nothing (it doesn't clear the existing selection, since Ctrl here
        # means "add/remove this one", not "start over"). This is on top of
        # (not a replacement for) Select mode's own plain click-to-select.
        if bool(event.state & 0x0004):  # Control
            ln = self.find_line_near(ix, iy, tol_img)
            if ln:
                self.select_line(ln.id, additive=True)
            return

        # Shift+grab moves an existing line, any color, in either mode:
        # grabbing one of its endpoints moves just that point (dragging it
        # directly if you keep the button held, or -- if you just click and
        # let go -- picking it up to follow the cursor until the next
        # click, so you can hold Shift afterward to extend/shorten it along
        # its own direction; see the vertex_follow_active handling above and
        # in on_canvas_release). Grabbing the line's body anywhere else
        # translates the whole line (both endpoints shift by the same
        # amount, so its length and direction don't change). A miss
        # (nothing under the cursor) falls through to the normal per-mode
        # behavior below, so Shift-drag on empty canvas in Draw mode still
        # means "constrain the new line parallel to the reference", same as
        # before.
        if bool(event.state & 0x0001):  # Shift
            hit = self.find_vertex_near(ix, iy, tol_img)
            if hit:
                vline, vidx = hit
                # Pushed here, at the start of the gesture, not per pixel of
                # movement -- one undo step for the whole thing (a
                # press+release with no actual movement just pushes a
                # harmless no-op snapshot).
                self._push_undo()
                self.dragging_vertex = (vline, vidx)
                self.drag_start_img = None
                self.vertex_move_orig = (vline.x1, vline.y1) if vidx == 1 else (vline.x2, vline.y2)
                other = (vline.x2, vline.y2) if vidx == 1 else (vline.x1, vline.y1)
                self.vertex_move_anchor = (other[0], other[1], vline.angle())
                self.press_canvas = (event.x, event.y)
                self.press_moved = False
                self.select_line(vline.id)
                return
            ln = self.find_line_near(ix, iy, tol_img)
            ellipse = self.ellipse_for_line(ln.id) if ln else None
            if ellipse is None:
                ellipse = self.find_ellipse_near(ix, iy, tol_img)
            if ellipse:
                # Grabbing a diameter line's BODY (not its endpoint), or the
                # ellipse's own curve, moves the WHOLE circle -- center plus
                # both diameters' 4 points, all by the same delta -- rather
                # than just that one line (which would desync it from the
                # shared center).
                self._push_undo()
                a = self._line_by_id(ellipse.line_a_id)
                b = self._line_by_id(ellipse.line_b_id)
                self.moving_ellipse = (
                    ellipse, ellipse.cx, ellipse.cy,
                    (a.x1, a.y1, a.x2, a.y2), (b.x1, b.y1, b.x2, b.y2), ix, iy)
                self.select_ellipse(ellipse)
                return
            if ln:
                self._push_undo()
                self.moving_line = (ln, ln.x1, ln.y1, ln.x2, ln.y2, ix, iy)
                self.select_line(ln.id)
                return

        if self.app.mode.get() == "select":
            ln = self.find_line_near(ix, iy, tol_img)
            if ln:
                self.select_line(ln.id)
            elif self.find_ellipse_near(ix, iy, tol_img):
                self.select_ellipse(self.find_ellipse_near(ix, iy, tol_img))
            else:
                self.select_line(None)
            return

        # draw mode: hovering an endpoint -- of ANY color, same or different
        # -- snaps a new line's start to it (chaining a segment onto that
        # exact point, e.g. sharing a corner between axes, or continuing a
        # line of the same color). Moving an existing vertex is Shift+grab
        # now (handled above), not a plain click here.
        self.dragging_vertex = None
        self.press_canvas = (event.x, event.y)
        self.press_moved = False
        hit = self.find_vertex_near(ix, iy, tol_img)
        if hit:
            vline, vidx = hit
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
        self.drag_start_img = self._apply_snap(ix, iy)
        self.drag_temp_id = None

    def on_canvas_drag(self, event):
        if not self.pil_image:
            return

        if self.rotating_diameter is not None:
            ellipse, vline, vidx, semi_a, semi_b, phi = self.rotating_diameter
            ix, iy = self.canvas_to_img(event.x, event.y)
            dx, dy = ix - ellipse.cx, iy - ellipse.cy
            if dx != 0 or dy != 0:
                t = ellipse_param_for_direction(ellipse.cx, ellipse.cy, semi_a, semi_b, phi, dx, dy)
                px, py = ellipse_point_at(ellipse.cx, ellipse.cy, semi_a, semi_b, phi, t)
                if vidx == 1:
                    vline.x1, vline.y1 = px, py
                else:
                    vline.x2, vline.y2 = px, py
                self.mirror_diameter_endpoint(ellipse, vline, vidx)
            self.redraw()
            return

        if self.moving_ellipse is not None:
            ellipse, ocx, ocy, oa, ob, aix, aiy = self.moving_ellipse
            ix, iy = self.canvas_to_img(event.x, event.y)
            dx, dy = ix - aix, iy - aiy
            ellipse.cx, ellipse.cy = ocx + dx, ocy + dy
            a = self._line_by_id(ellipse.line_a_id)
            b = self._line_by_id(ellipse.line_b_id)
            a.x1, a.y1, a.x2, a.y2 = oa[0] + dx, oa[1] + dy, oa[2] + dx, oa[3] + dy
            b.x1, b.y1, b.x2, b.y2 = ob[0] + dx, ob[1] + dy, ob[2] + dx, ob[3] + dy
            self.redraw()
            return

        if self.dragging_vertex is not None:
            ln, idx = self.dragging_vertex
            # Once the mouse has moved far enough, this press-and-hold
            # counts as a real drag (finalized immediately on release); a
            # release before crossing this threshold is a short click
            # instead, which picks the vertex up to follow the cursor (see
            # on_canvas_release / on_canvas_press's vertex_follow_active
            # check) rather than finalizing anything here.
            if self.press_canvas is not None and \
                    dist((event.x, event.y), self.press_canvas) >= CLICK_MOVE_THRESHOLD:
                self.press_moved = True
            ix, iy = self.canvas_to_img(event.x, event.y)
            ellipse = self.ellipse_for_line(ln.id)
            if ellipse:
                # Ellipse diameter endpoints reshape freely -- no collinear-
                # extend constraint (that's a plain-line feature); the
                # opposite end of this SAME diameter line is kept mirrored
                # through the center, which is what actually redefines the
                # ellipse's shape (see Ellipse.canonical).
                ix, iy = self._apply_snap(ix, iy, exclude_ids=(ln.id,))
            else:
                ax, ay, orig_angle = self.vertex_move_anchor
                ix, iy = self._constrained_vertex_point(ln, (ax, ay), orig_angle, ix, iy,
                                                          bool(event.state & 0x0001))
            if idx == 1:
                ln.x1, ln.y1 = ix, iy
            else:
                ln.x2, ln.y2 = ix, iy
            if ellipse:
                self.mirror_diameter_endpoint(ellipse, ln, idx)
                ellipse.recompute_shape(self)
            self.redraw()
            return

        if self.moving_line is not None:
            ln, ox1, oy1, ox2, oy2, aix, aiy = self.moving_line
            ix, iy = self.canvas_to_img(event.x, event.y)
            dx, dy = ix - aix, iy - aiy
            ln.x1, ln.y1 = ox1 + dx, oy1 + dy
            ln.x2, ln.y2 = ox2 + dx, oy2 + dy
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

        self._update_temp_line(*self.drag_start_img, event.x, event.y,
                                bool(event.state & 0x0001))

    def _update_temp_line(self, ix1, iy1, cx, cy, shift_held):
        """Draw/update the dashed preview line from image point (ix1, iy1)
        to the current cursor position (cx, cy), applying the
        Shift-parallel constraint if held. Shared by hold-and-drag
        drawing, click-to-click drawing, and re-establishing the preview
        after a zoom/pan/resize wipes the canvas mid-draw (see
        _restore_temp_line) -- that last caller has no real event to read
        a live Shift state from, so it always passes False."""
        sx, sy = self.img_to_canvas(ix1, iy1)

        if shift_held:  # Shift held: constrain to parallel reference
            ref = self._parallel_reference()
            if ref:
                ix2, iy2 = self.canvas_to_img(cx, cy)
                ex, ey = self._constrain_to_reference(ix1, iy1, ix2, iy2, ref)
                cx, cy = self.img_to_canvas(ex, ey)

        if self.app.snap_mode.get():  # Snap Mode: pull onto nearby geometry
            ix2, iy2 = self.canvas_to_img(cx, cy)
            sx2, sy2 = self._apply_snap(ix2, iy2)
            cx, cy = self.img_to_canvas(sx2, sy2)

        color_hex = AXIS_COLORS[self.app.current_color.get()]["hex"]
        if self.drag_temp_id:
            self.canvas.coords(self.drag_temp_id, sx, sy, cx, cy)
        else:
            self.drag_temp_id = self.canvas.create_line(
                sx, sy, cx, cy, fill=color_hex, width=2, dash=(4, 2))

    def on_canvas_hover(self, event):
        """No button held. While a click-started line, or a picked-up
        vertex, is pending, this is what drives its live preview towards
        the cursor. Otherwise it just updates the cursor to hint what a
        click here would do (snap-start a new line at a vertex vs.
        Shift+grab to move something vs. plain draw)."""
        if not self.pil_image:
            return

        # A vertex picked up with Shift+click is following the cursor until
        # the next click places it (see on_canvas_release / on_canvas_press).
        # This works in either Draw or Select mode -- Shift+grab does too --
        # so it's checked before the Draw-mode-only logic below.
        if self.vertex_follow_active:
            ln, idx = self.dragging_vertex
            ix, iy = self.canvas_to_img(event.x, event.y)
            ellipse = self.ellipse_for_line(ln.id)
            if ellipse:
                ix, iy = self._apply_snap(ix, iy, exclude_ids=(ln.id,))
            else:
                ax, ay, orig_angle = self.vertex_move_anchor
                ix, iy = self._constrained_vertex_point(ln, (ax, ay), orig_angle, ix, iy,
                                                          bool(event.state & 0x0001))
            if idx == 1:
                ln.x1, ln.y1 = ix, iy
            else:
                ln.x2, ln.y2 = ix, iy
            if ellipse:
                self.mirror_diameter_endpoint(ellipse, ln, idx)
                ellipse.recompute_shape(self)
            self.redraw()
            return

        if self.app.mode.get() == "circle" and self.circle_points:
            self._update_circle_preview(event)
            return

        if self.app.mode.get() != "draw":
            return

        if self.click_draw_start is not None:
            self._update_temp_line(*self.click_draw_start, event.x, event.y,
                                    bool(event.state & 0x0001))
            return

        ix, iy = self.canvas_to_img(event.x, event.y)
        tol_img = HIT_TOLERANCE / self.scale

        if bool(event.state & 0x0001) or bool(event.state & 0x0004):  # Shift/Ctrl
            hit = self.find_vertex_near(ix, iy, tol_img)
            if hit or self.find_line_near(ix, iy, tol_img):
                self.canvas.config(cursor="fleur")
            else:
                self.canvas.config(cursor="crosshair")
            return

        hit = self.find_vertex_near(ix, iy, tol_img)
        if hit:
            self.canvas.config(cursor="hand2")
        else:
            self.canvas.config(cursor="crosshair")

    def on_canvas_release(self, event):
        if not self.pil_image:
            return

        if self.rotating_diameter is not None:
            ellipse, vline = self.rotating_diameter[0], self.rotating_diameter[1]
            self.rotating_diameter = None
            self.redraw()
            self.mark_dirty()
            self.app.set_status(f"Rotated line #{vline.id} within circle #{ellipse.id}.")
            return

        if self.moving_ellipse is not None:
            ellipse = self.moving_ellipse[0]
            self.moving_ellipse = None
            self.redraw()
            self.mark_dirty()
            self.app.set_status(f"Moved circle #{ellipse.id}.")
            return

        if self.dragging_vertex is not None:
            ln, idx = self.dragging_vertex
            if not self.press_moved:
                # A short click, not a drag: the vertex detaches and
                # follows the cursor instead of finalizing anything here --
                # same click-to-click pattern as drawing a new line. Lets
                # you let go of the mouse button and then hold Shift to
                # extend/shorten the line precisely along its own
                # direction, without having to keep the button held the
                # whole time. The next click (on_canvas_press) places it,
                # or Esc cancels it.
                self.vertex_follow_active = True
                self.app.set_status("Moving this point -- click again to "
                                     "place it (hold Shift to keep it in "
                                     "line with the rest of the line), or "
                                     "Esc to cancel.")
                return
            self.dragging_vertex = None
            self.vertex_move_anchor = None
            self.vertex_move_orig = None
            self.redraw()
            self.mark_dirty()
            self.app.set_status(f"Moved line #{ln.id}'s endpoint.")
            return

        if self.moving_line is not None:
            ln = self.moving_line[0]
            self.moving_line = None
            self.redraw()
            self.mark_dirty()
            self.app.set_status(f"Moved line #{ln.id}.")
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

        if self.app.snap_mode.get():
            ix2, iy2 = self._apply_snap(ix2, iy2)

        if dist((ix1, iy1), (ix2, iy2)) < 3 / self.scale:
            self.app.set_status("Line too short -- not created.")
            return

        color = self.app.current_color.get()
        # No unit is stamped on the line yet -- it stays None (no known
        # length either) until a real known length is actually committed
        # for it, so the known-length field keeps showing whatever
        # Settings > Default Unit currently is, live, for as long as this
        # line has no known length of its own (see populate_known_length_
        # field and commit_known_length). Stamping a unit here at draw
        # time used to freeze it in immediately, which meant changing
        # Default Unit afterward had no visible effect on this line even
        # though nothing had actually been measured for it yet.
        line = Line(color, ix1, iy1, ix2, iy2, parallel_to=parallel_to)
        self._push_undo()
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

    def _ellipse_by_id(self, ellipse_id):
        for el in self.ellipses:
            if el.id == ellipse_id:
                return el
        return None

    def ellipse_for_line(self, line_id):
        """The Ellipse a diameter Line belongs to, or None if it's an
        ordinary (non-diameter) line."""
        for el in self.ellipses:
            if el.line_a_id == line_id or el.line_b_id == line_id:
                return el
        return None

    def mirror_diameter_endpoint(self, ellipse, line, moved_end):
        """Keep a diameter Line's 2 endpoints exactly opposite the shared
        center: whichever endpoint (1 or 2) was just moved, force the OTHER
        one to 2*center - moved_point. Called after directly setting
        line.x{moved_end},y{moved_end} so both ends stay a straight line
        through the center at all times."""
        if moved_end == 1:
            line.x2 = 2 * ellipse.cx - line.x1
            line.y2 = 2 * ellipse.cy - line.y1
        else:
            line.x1 = 2 * ellipse.cx - line.x2
            line.y1 = 2 * ellipse.cy - line.y2

    def find_ellipse_near(self, ix, iy, tolerance_img):
        """Return the Ellipse whose CURVE (not its 2 diameter lines, which
        find_line_near/find_vertex_near already cover) is within tolerance
        of (ix, iy), or None. Used so clicking/Shift-grabbing the boundary
        of a circle -- not one of the straight lines through it -- still
        hits something (selects it / moves the whole thing)."""
        best, best_d = None, tolerance_img
        for el in self.ellipses:
            semi_a, semi_b, phi = el.canonical(self)
            nx, ny = ellipse_nearest_point(el.cx, el.cy, semi_a, semi_b, phi, ix, iy)
            d = dist((ix, iy), (nx, ny))
            if d <= best_d:
                best, best_d = el, d
        return best

    def select_ellipse(self, ellipse):
        """Select both of an Ellipse's diameter lines together, so the
        whole circle highlights (rather than just one line)."""
        a = self._line_by_id(ellipse.line_a_id)
        b = self._line_by_id(ellipse.line_b_id)
        self.selected_line_ids = [i for i in (a.id if a else None, b.id if b else None) if i]
        self.redraw()
        self.populate_known_length_field()
        self._maybe_advance_parallel_pick()

    # --------------------------------------------------------- circle tool
    def cancel_circle(self, event=None):
        """Escape, or switching modes/tabs/images: abandon a circle that's
        mid-click (center and/or 1st circumference point placed, but not
        yet finished)."""
        if not self.circle_points:
            self.circle_points = None
            return
        self.circle_points = None
        if self._circle_preview_ids:
            self.canvas.delete(*self._circle_preview_ids)
            self._circle_preview_ids = []
        self.app.set_status("Circle cancelled.")

    def _update_circle_preview(self, event):
        """Live dashed preview while a circle's center (and maybe its 1st
        circumference point) has been placed and the 2nd/3rd click is
        still pending -- shows the diameter(s) already committed, full-
        length through the center, plus a preview of the one following the
        cursor right now."""
        if self._circle_preview_ids:
            self.canvas.delete(*self._circle_preview_ids)
            self._circle_preview_ids = []
        ix, iy = self.canvas_to_img(event.x, event.y)
        ix, iy = self._apply_snap(ix, iy)
        cx, cy = self.circle_points[0]
        color_hex = ORANGE_META["hex"]

        def preview_diameter(px, py, dash):
            sx, sy = self.img_to_canvas(2 * cx - px, 2 * cy - py)
            ex, ey = self.img_to_canvas(px, py)
            self._circle_preview_ids.append(self.canvas.create_line(
                sx, sy, ex, ey, fill=color_hex, width=2, dash=dash))

        if len(self.circle_points) >= 2:
            ax, ay = self.circle_points[1]
            preview_diameter(ax, ay, None)   # 1st diameter, already placed
        preview_diameter(ix, iy, (4, 2))     # the one following the cursor

    def _finalize_circle(self):
        """The 3rd click of the Circle tool: creates the Ellipse and its 2
        orange diameter Lines from the 3 points collected (center, then 2
        circumference points -- see on_canvas_press). The 2 points must
        genuinely define 2 different directions from the center (not the
        same line through it), or there's no ellipse to build."""
        (cx, cy), (ax, ay), (bx, by) = self.circle_points
        self.circle_points = None
        if self._circle_preview_ids:
            self.canvas.delete(*self._circle_preview_ids)
            self._circle_preview_ids = []

        ux, uy = ax - cx, ay - cy
        vx, vy = bx - cx, by - cy
        min_radius_px = 3 / self.scale
        if dist((0, 0), (ux, uy)) < min_radius_px or dist((0, 0), (vx, vy)) < min_radius_px:
            self.app.set_status("Circle too small -- not created.")
            return
        cross = ux * vy - uy * vx
        if abs(cross) < 1e-6 * (abs(ux) + abs(uy) + abs(vx) + abs(vy) + 1):
            self.app.set_status(
                "Those 2 circumference points are on the same line through the "
                "center -- pick a 2nd point in a different direction to define "
                "the circle.")
            return

        self._push_undo()
        line_a = Line(ORANGE_COLOR, cx - ux, cy - uy, cx + ux, cy + uy)
        line_b = Line(ORANGE_COLOR, cx - vx, cy - vy, cx + vx, cy + vy)
        self.lines.append(line_a)
        self.lines.append(line_b)
        ellipse = Ellipse(cx, cy, line_a.id, line_b.id)
        ellipse.recompute_shape(self)
        self.ellipses.append(ellipse)
        self.select_ellipse(ellipse)
        self.mark_dirty()
        self.app.set_status(
            f"Circle #{ellipse.id} created (diameter lines #{line_a.id}, #{line_b.id} -- "
            "orange until assigned to an axis; see the known-length panel).")

    # ------------------------------------------------------------- mode
    def on_mode_changed(self):
        """Called for the active tab whenever the shared draw/select mode
        changes, and again right after this tab becomes active (in case the
        mode changed while it was in the background)."""
        self.cancel_vertex_follow()
        self.dragging_vertex = None
        self.moving_line = None
        self.moving_ellipse = None
        self.rotating_diameter = None
        self.drag_start_img = None
        self.cancel_click_draw()
        self.cancel_circle()
        self.canvas.config(cursor="crosshair" if self.app.mode.get() in ("draw", "circle") else "hand2")

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

    def cancel_vertex_follow(self):
        """Escape, or switching modes/tabs/images: abandon a vertex that
        was picked up with Shift+click (released without dragging) and is
        following the cursor, putting it back exactly where it was before
        the pickup and discarding the undo snapshot pushed for this
        gesture -- nothing actually ended up changing, so it shouldn't
        leave a no-op entry in the undo history."""
        if not self.vertex_follow_active:
            return
        ln, idx = self.dragging_vertex
        ox, oy = self.vertex_move_orig
        if idx == 1:
            ln.x1, ln.y1 = ox, oy
        else:
            ln.x2, ln.y2 = ox, oy
        if self.undo_stack:
            self.undo_stack.pop()
        self.vertex_follow_active = False
        self.dragging_vertex = None
        self.vertex_move_anchor = None
        self.vertex_move_orig = None
        self.redraw()
        self.app.set_status("Vertex move cancelled.")

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
        its pre-edit value (if that field has focus), cancels an in-
        progress Make Parallel pick, cancels a vertex that's mid-follow
        after a Shift+click pickup, cancels a circle that's mid-click, or
        cancels a line that was started with a short click and is waiting
        for its closing click -- whichever of those is actually in
        progress. Otherwise, if nothing is in progress but one or more
        lines are selected, Escape just clears that selection."""
        focused = self.app.root.focus_get()
        if focused in (self.known_length_entry, self.known_unit_box):
            self.cancel_length_edit()
            self.canvas.focus_set()
            return
        if self.parallel_picking:
            self.cancel_make_parallel()
            return
        if self.vertex_follow_active:
            self.cancel_vertex_follow()
            return
        if self.circle_points:
            self.cancel_circle()
            return
        if self.click_draw_start is not None:
            self.cancel_click_draw()
            return
        if self.selected_line_ids:
            self.select_line(None)
            self.app.set_status("Selection cleared.")

    # -------------------------------------------------------------- redraw
    def redraw(self):
        if not self.pil_image:
            self._show_placeholder()
            return
        self.canvas.delete("line", "label", "handle", "ellipse")
        for el in self.ellipses:
            self._draw_ellipse(el)
        for ln in self.lines:
            self._draw_line(ln)
        self._update_tree()
        self._update_axis_labels()
        self.app.refresh_toolbar_hint(self)

    def _draw_ellipse(self, el):
        """The circle's curve itself -- drawn as a many-segment polygon
        (tk canvas has no rotated-oval primitive) UNDER its 2 diameter
        lines/handles/labels, which _draw_line draws separately right
        after this. Orange until BOTH diameter lines have been assigned to
        a real axis, at which point it's shown in whichever color reads
        as "most complete" -- a single axis color if both lines share one,
        else it stays orange (mixed) since the circle as a whole doesn't
        have one single axis."""
        semi_a, semi_b, phi = el.canonical(self)
        a = self._line_by_id(el.line_a_id)
        b = self._line_by_id(el.line_b_id)
        colors = {c for c in (a.color if a else None, b.color if b else None) if c}
        color = colors.pop() if len(colors) == 1 else ORANGE_COLOR
        meta = line_color_meta(color)
        selected = bool(a and a.id in self.selected_line_ids) or bool(b and b.id in self.selected_line_ids)
        outline = meta["select_hex"] if selected else meta["hex"]
        pts = []
        n = 72
        for i in range(n + 1):
            t = 2 * math.pi * i / n
            px, py = ellipse_point_at(el.cx, el.cy, semi_a, semi_b, phi, t)
            cx, cy = self.img_to_canvas(px, py)
            pts.extend((cx, cy))
        self.canvas.create_line(*pts, fill=outline, width=2 if not selected else 3,
                                 tags=("line", "ellipse"))

    def _draw_line(self, ln):
        x1, y1 = self.img_to_canvas(ln.x1, ln.y1)
        x2, y2 = self.img_to_canvas(ln.x2, ln.y2)
        meta = line_color_meta(ln.color)
        selected = ln.id in self.selected_line_ids
        width = 4 if selected else 2
        outline = meta["select_hex"] if selected else meta["hex"]

        self.canvas.create_line(x1, y1, x2, y2, fill=outline, width=width, tags=("line",))
        for (x, y) in [(x1, y1), (x2, y2)]:
            self.canvas.create_oval(x - HANDLE_RADIUS, y - HANDLE_RADIUS,
                                     x + HANDLE_RADIUS, y + HANDLE_RADIUS,
                                     fill=outline, outline="white", tags=("handle",))

        value, unit, mode, denom = self.effective_display(ln)
        label = f"#{ln.id} {self.format_display(value, unit, mode, denom) if value is not None else '? (uncalibrated)'}"
        if ln.known_length:
            label += " [known]"
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        self.canvas.create_text(mx, my - 12, text=label, fill="white",
                                 font=("", 9, "bold"), tags=("label",))

    def _update_tree(self):
        self.tree.delete(*self.tree.get_children())
        for ln in self.lines:
            value, unit, mode, denom = self.effective_display(ln)
            axis = line_color_meta(ln.color)["axis"] or "-"
            self.tree.insert("", "end", iid=str(ln.id), values=(
                ln.color, axis, f"{ln.pixel_length():.1f}",
                self.format_display(value, unit, mode, denom) if value is not None else "?",
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
        self._maybe_advance_parallel_pick()

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
        self._maybe_advance_parallel_pick()

    # ------------------------------------------------- known-length field
    def _update_denom_row_visibility(self, ln):
        """Show the Fraction denom row only when the display mode actually
        in effect for `ln` right now (its own override, else the live
        Settings > Default Display Mode) is 'fraction' -- it's meaningless
        in decimal mode, so it's removed from the layout entirely rather
        than just greyed out."""
        mode = (ln.display_mode or self.app.default_display_mode.get()) if ln else None
        visible = mode == "fraction"
        if visible and not self._denom_row_visible:
            self.denom_row.pack(fill="x", pady=(4, 0))
        elif not visible and self._denom_row_visible:
            self.denom_row.pack_forget()
        self._denom_row_visible = visible

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
                    text=f"Line #{ln.id} ({ln.color}, {line_color_meta(ln.color)['axis'] or 'no axis'}) "
                         f"-- {ln.pixel_length():.1f} px")
                self.display_mode_box.config(state="readonly")
                self.display_unit_box.config(state="readonly")
                self.display_denominator_box.config(state="readonly")
                self.display_mode_var.set(ln.display_mode or "(auto)")
                self.display_unit_var.set(ln.display_unit or "(auto)")
                self.display_denominator_var.set(
                    f"1/{ln.display_denominator}" if ln.display_denominator else "(auto)")
                self._update_denom_row_visibility(ln)
                for btn in self.line_color_buttons.values():
                    btn.config(state="normal")
                self.line_color_var.set(ln.color)
                return
        self.known_length_var.set("")
        self.known_length_entry.config(state="disabled")
        self.known_unit_box.config(state="disabled")
        self.display_mode_var.set("(auto)")
        self.display_unit_var.set("(auto)")
        self.display_denominator_var.set("(auto)")
        self.display_mode_box.config(state="disabled")
        self.display_unit_box.config(state="disabled")
        self.display_denominator_box.config(state="disabled")
        self._update_denom_row_visibility(None)
        for btn in self.line_color_buttons.values():
            btn.config(state="disabled")
        if len(self.selected_line_ids) == 0:
            self.selection_label.config(text="No line selected")
        else:
            self.selection_label.config(text=f"{len(self.selected_line_ids)} lines selected")

    def commit_line_color(self):
        """Radiobutton in the known-length panel: reassign the selected
        line's color/axis. See set_line_color."""
        if len(self.selected_line_ids) != 1:
            return
        ln = self._line_by_id(self.selected_line_ids[0])
        if not ln:
            return
        self.set_line_color(ln, self.line_color_var.get())

    def set_line_color(self, line, new_color):
        """Change ANY line's color/axis -- from orange (no axis) onto a
        real measurement axis (red/green/blue), back to orange, or between
        axes. Works the same for an ordinary line and a circle's diameter
        line; stays fully movable/rotatable/reshapable afterward either
        way. The line's own known_length (if it has one) is left exactly
        as it was -- recoloring doesn't change what the line itself
        measures, only which axis it calibrates. If this line is one of a
        Circle's 2 diameters, see _draw_ellipse for how the circle's own
        outline color follows its 2 lines' colors -- nothing else about
        the circle changes here."""
        if line.color == new_color:
            return
        self._push_undo()
        line.color = new_color
        self.mark_dirty()
        self.redraw()
        self.populate_known_length_field()
        self.app.set_status(
            f"Line #{line.id} is now {'unassigned (orange)' if new_color == ORANGE_COLOR else new_color}.")

    def commit_display_settings(self, event=None):
        """Apply the Display mode/unit/denominator combos to the single
        selected line -- an override independent of its known length, used
        for both known and computed lines. '(auto)' clears an override
        (back to the live Settings > Default Display Mode / calibration
        unit / program-wide Fraction Denominator setting, respectively)."""
        if len(self.selected_line_ids) != 1:
            return
        ln = self._line_by_id(self.selected_line_ids[0])
        if not ln:
            return
        mode = self.display_mode_var.get()
        unit = self.display_unit_var.get()
        denom = self.display_denominator_var.get()
        new_mode = None if mode == "(auto)" else mode
        new_unit = None if unit == "(auto)" else unit
        new_denom = None if denom == "(auto)" else int(denom.split("/")[1])
        if (new_mode, new_unit, new_denom) == \
                (ln.display_mode, ln.display_unit, ln.display_denominator):
            return  # nothing actually changed -- no undo step, no redraw needed
        self._push_undo()
        ln.display_mode = new_mode
        ln.display_unit = new_unit
        ln.display_denominator = new_denom
        self._update_denom_row_visibility(ln)
        self.mark_dirty()
        self.redraw()

    def commit_known_length(self, event=None):
        """Apply whatever is currently typed in the known-length field to
        the single selected line. Never required -- blank just clears it."""
        if len(self.selected_line_ids) != 1:
            return
        ln = self._line_by_id(self.selected_line_ids[0])
        if not ln:
            return
        text = self.known_length_var.get().strip()
        new_unit = self.known_unit_var.get().strip() or self.app.default_unit.get()
        if text == "":
            new_value = None
        else:
            parsed = parse_measurement(text)
            if parsed is None:
                self.app.set_status(
                    "Known length must be a number, fraction, mixed number, or simple "
                    "math (e.g. 2.5, 1/2, 2 1/2, 2 + 1/2) -- or leave it blank.")
                return
            new_value = float(parsed)
            if new_value <= 0:
                self.app.set_status("Known length must be greater than 0.")
                return
        changed = ln.known_length != new_value or ln.unit != new_unit
        if changed:
            self._push_undo()
        ln.known_length = new_value
        # Typing a known length no longer has any side effect on display
        # mode (decimal vs. fraction), for this line or any other -- it
        # used to auto-lock the whole axis based on whether the text
        # looked like a fraction ('/') or had a decimal point, which meant
        # Settings > Default Display Mode silently stopped doing anything
        # the moment any line on that axis had ever been typed with a '.',
        # i.e. almost immediately for most real measurements. Display mode
        # is controlled by exactly two things now: Settings > Default
        # Display Mode (applies live, everywhere, to every line with no
        # override), and the Display section's mode dropdown for a
        # specific line (an explicit, visible override) -- nothing hidden
        # in between.
        ln.unit = new_unit
        # If this line is one of a Circle's 2 diameters, the OTHER one is
        # a measurement of the exact same physical distance (both are
        # diameters of the same circle), so it's kept linked to the same
        # known_length/unit -- whichever of the 2 you type a known length
        # into, the other one just follows.
        ellipse = self.ellipse_for_line(ln.id)
        if ellipse:
            other_id = (ellipse.line_b_id if ln.id == ellipse.line_a_id
                        else ellipse.line_a_id)
            other = self._line_by_id(other_id)
            if other:
                other.known_length = new_value
                other.unit = new_unit
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
        # Display-only formatting -- b_real/a_unit (the exact decimal values
        # actually applied below) are unaffected by this.
        a_mode = a.display_mode or self.app.default_display_mode.get()
        b_mode = b.display_mode or self.app.default_display_mode.get()
        a_denom = a.display_denominator or self.app.fraction_denominator.get()
        b_denom = b.display_denominator or self.app.fraction_denominator.get()

        msg = (
            f"Line #{a.id} ({a.color}): {a.pixel_length():.1f} px = "
            f"{self.format_display(a_real, a_unit, a_mode, a_denom)}\n"
            f"Line #{b.id} ({b.color}): {b.pixel_length():.1f} px\n\n"
            f"Pixel ratio (B/A): {ratio:.4f}\n"
            f"=> Line #{b.id} is approximately {self.format_display(b_real, a_unit, b_mode, b_denom)}"
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
        self._push_undo()
        # Deleting either of a Circle's 2 diameter lines takes the WHOLE
        # circle with it -- a lone diameter line (or an Ellipse with a
        # missing line) doesn't mean anything on its own.
        delete_ids = set(self.selected_line_ids)
        remaining_ellipses = []
        for el in self.ellipses:
            if el.line_a_id in delete_ids or el.line_b_id in delete_ids:
                delete_ids.add(el.line_a_id)
                delete_ids.add(el.line_b_id)
            else:
                remaining_ellipses.append(el)
        self.ellipses = remaining_ellipses
        self.lines = [ln for ln in self.lines if ln.id not in delete_ids]
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
            "ellipses": [el.to_dict() for el in self.ellipses],
            "axis_display_mode": self.axis_display_mode,
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
        self.ellipses = [Ellipse.from_dict(d) for d in data.get("ellipses", [])]
        self.selected_line_ids = []
        self.project_path = path
        saved_modes = data.get("axis_display_mode") or {}
        self.axis_display_mode = {
            color: saved_modes.get(color) for color in AXIS_COLORS}
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
                        "computed_length", "displayed_length", "parallel_to"])
            for ln in self.lines:
                real, unit = self.computed_length(ln)
                value, disp_unit, mode, denom = self.effective_display(ln)
                w.writerow([ln.id, ln.color, line_color_meta(ln.color)["axis"] or "",
                            f"{ln.x1:.2f}", f"{ln.y1:.2f}", f"{ln.x2:.2f}", f"{ln.y2:.2f}",
                            f"{ln.pixel_length():.2f}",
                            ln.known_length if ln.known_length else "",
                            ln.unit or "", f"{real:.4f}" if real else "",
                            self.format_display(value, disp_unit, mode, denom) if value is not None else "",
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
            "ellipses": [el.to_dict() for el in self.ellipses],
            "axis_display_mode": self.axis_display_mode,
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
        # silently become the default for everything drawn after it. A
        # line's own unit (Line.unit) stays None -- and keeps following
        # this setting live -- until a known length is actually committed
        # for it, at which point it's real measured data and stops
        # following (see ProjectTab.on_canvas_release / commit_known_length).
        self.default_unit = tk.StringVar(value=DEFAULT_UNIT)
        self.default_unit.trace_add("write", self._on_default_unit_change)
        # How finely a "fraction" display rounds (Settings menu), program-
        # wide -- e.g. 32 means "nearest 1/32". Affects every line currently
        # showing in fraction mode, on every open tab.
        self.fraction_denominator = tk.IntVar(value=DEFAULT_FRACTION_DENOMINATOR)
        self.fraction_denominator.trace_add("write", self._on_fraction_denominator_change)
        # Whether a line with no Display-dropdown override of its own
        # shows decimal or fraction lengths, program-wide (Settings menu).
        # effective_display() falls back to this for every such line, so
        # changing it takes effect immediately, everywhere, in every open
        # tab -- typing a known length never overrides it (that's what the
        # Display section's own mode dropdown is for, per line).
        self.default_display_mode = tk.StringVar(value=DEFAULT_DISPLAY_MODE)
        self.default_display_mode.trace_add("write", self._on_default_display_mode_change)
        # Snap Mode (toggled by the 'S' key, the toolbar checkbutton, or the
        # Edit menu) -- while on, placing or moving a vertex (drawing a new
        # line's endpoints, or Shift-dragging an existing one) pulls it onto
        # a nearby vertex first, else the nearest point along a nearby
        # line's body, within the usual hit-test tolerance. See
        # ProjectTab._apply_snap.
        self.snap_mode = tk.BooleanVar(value=False)

        self._build_menu()
        self._build_toolbar()

        self.notebook = ClosableNotebook(self.root)
        self.notebook.pack(side="top", fill="both", expand=True)
        self.notebook.bind("<<NotebookTabClosed>>", self._on_notebook_tab_closed)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_statusbar()

        self.root.bind("<Delete>", self._delete_selected_shortcut)
        self.root.bind("<BackSpace>", self._delete_selected_shortcut)
        self.root.bind("<p>", self._make_parallel_shortcut)
        self.root.bind("<P>", self._make_parallel_shortcut)
        self.root.bind("<s>", self._snap_mode_shortcut)
        self.root.bind("<S>", self._snap_mode_shortcut)
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
        self.root.bind("<Control-z>", lambda e: self._dispatch("undo"))
        self.root.bind("<Control-y>", lambda e: self._dispatch("redo"))
        self.root.bind("<Control-Shift-Z>", lambda e: self._dispatch("redo"))

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
        editmenu.add_command(label="Undo", accelerator="Ctrl+Z",
                              command=lambda: self._dispatch("undo"))
        editmenu.add_command(label="Redo", accelerator="Ctrl+Y",
                              command=lambda: self._dispatch("redo"))
        editmenu.add_separator()
        editmenu.add_command(label="Delete Selected Line(s)",
                              command=lambda: self._dispatch("delete_selected"))
        editmenu.add_command(label="Edit Known Length",
                              command=lambda: self._dispatch("focus_known_length"))
        editmenu.add_command(label="Compare Selected Two Lines...",
                              command=lambda: self._dispatch("compare_selected"))
        editmenu.add_command(label="Make Parallel...", accelerator="P",
                              command=lambda: self._dispatch("toggle_make_parallel"))
        editmenu.add_checkbutton(label="Snap Mode", accelerator="S", variable=self.snap_mode)
        editmenu.add_separator()
        editmenu.add_command(label="Rotate Image 90°", command=lambda: self._dispatch("rotate_image"))
        menubar.add_cascade(label="Edit", menu=editmenu)

        settingsmenu = tk.Menu(menubar, tearoff=0)
        unitmenu = tk.Menu(settingsmenu, tearoff=0)
        for unit in UNIT_CHOICES:
            unitmenu.add_radiobutton(label=unit, variable=self.default_unit, value=unit)
        settingsmenu.add_cascade(label="Default Unit", menu=unitmenu)
        denommenu = tk.Menu(settingsmenu, tearoff=0)
        for denom in FRACTION_DENOMINATOR_CHOICES:
            denommenu.add_radiobutton(label=f"Nearest 1/{denom}", variable=self.fraction_denominator,
                                       value=denom)
        settingsmenu.add_cascade(label="Fraction Denominator", menu=denommenu)
        displaymenu = tk.Menu(settingsmenu, tearoff=0)
        displaymenu.add_radiobutton(label="Decimal", variable=self.default_display_mode,
                                     value="decimal")
        displaymenu.add_radiobutton(label="Fraction", variable=self.default_display_mode,
                                     value="fraction")
        settingsmenu.add_cascade(label="Default Display Mode", menu=displaymenu)
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
        ttk.Radiobutton(bar, text="Circle", variable=self.mode, value="circle").pack(side="left")
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
        self.parallel_button = ttk.Button(
            bar, text="Make Parallel (P)",
            command=lambda: self._dispatch("toggle_make_parallel"))
        self.parallel_button.pack(side="left", padx=2)
        ttk.Checkbutton(bar, text="Snap (S)", variable=self.snap_mode).pack(side="left", padx=2)

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
            "5. The known-length field understands fractions and simple math, "
            "not just plain decimals: 1/2, 2 1/2 (a mixed number -- no + needed), "
            "2 + 1/2, and 3/4 - 1/8 all work -- but typing a value never changes "
            "how lengths are DISPLAYED. Settings > Default Display Mode picks "
            "decimal or fraction, program-wide, for every line that doesn't have "
            "its own override -- flip it and every such line updates immediately, "
            "in every open tab, rounded to the nearest 1/32 by default (Settings "
            "> Fraction Denominator sets that program-wide too). The 'Display' "
            "row below the known-length field overrides just "
            "the SELECTED line -- decimal vs. fraction, a different unit (e.g. "
            "show a line as decimal mm even though its axis was calibrated in "
            "fractional inches), and/or its OWN nearest-fraction denominator "
            "regardless of the program-wide setting -- independent of every "
            "other line, and independent of whether that line has its own "
            "known length or is only computed from the axis.\n"
            "6. 'Compare 2 Lines' lets you compare any two lines directly "
            "(they don't need to be the same color), even without calibrating "
            "a whole axis.\n"
            "7. Hold Shift while drawing a new line to lock its direction "
            "parallel to the selected line (or the last line drawn).\n"
            "8. 'Make Parallel' (toolbar button, Edit menu, or press P) does the "
            "same thing to a line you already drew, instead of only while "
            "drawing a new one -- pick a reference line, then the line to "
            "rotate to match it (its length doesn't change), using the photo "
            "or the side list in any combination. Esc cancels.\n"
            "9. 'Rotate 90°' rotates the photo a quarter turn clockwise and keeps "
            "every line attached to the same spot on the image.\n"
            "10. Hovering the end of any line (same color or different) turns "
            "the cursor into a hand -- clicking it and dragging away starts a "
            "new line snapped onto that exact point, instead of moving it. "
            "Ctrl+click selects any line under the cursor, whatever color it "
            "is and in either Draw or Select mode -- Ctrl-clicking a second line "
            "adds it to the selection, and Ctrl-clicking a selected line removes "
            "it again. Shift+drag moves an existing line, any color, in either "
            "mode: grab an endpoint and just that point moves; grab anywhere "
            "else on the line and the whole thing translates, keeping its "
            "length and direction exactly the same. Shift+CLICK an endpoint "
            "(instead of holding the button) picks it up to follow the cursor "
            "until you click again to place it -- hold Shift while it follows "
            "to keep it exactly collinear with the line's original direction, "
            "so you can extend or shorten the line precisely; Esc puts it back.\n"
            "11. Snap Mode (toolbar checkbox, Edit menu, or press S) makes "
            "placing or moving a point -- a new line's endpoint, or a vertex "
            "you're Shift-dragging -- jump onto a nearby vertex, or the nearest "
            "point along a nearby line if no vertex is close enough. Off by "
            "default.\n"
            "12. Ctrl+Z undoes and Ctrl+Y (or Ctrl+Shift+Z) redoes -- drawing a "
            "line, deleting line(s), dragging an endpoint or moving a whole "
            "line, Make Parallel, a known-length or Display-section change, and "
            "Rotate 90° all step back one action at a time. Opening a different "
            "image or project starts that tab's undo history fresh.\n"
            "13. Save Project keeps one tab's image (a full copy, not just its "
            "path), rotation, and lines in a single .imt file you can reopen "
            "later -- even if the photo has since moved or is on another "
            "computer. Export CSV for a spreadsheet of measurements.\n"
            "14. The whole window -- every open tab, saved or not -- is "
            "remembered automatically and restored next time you launch the app.\n"
            "15. 'Circle' mode draws a circle-in-perspective (an ellipse): click "
            "its center, then 2 points on its circumference in different "
            "directions -- the 2nd point doesn't need to be a right angle from "
            "the 1st, any 2 different directions work. This makes an ellipse "
            "plus its 2 diameter LINES (shown in orange, a 4th color that isn't "
            "a measurement axis until you assign one -- see 'Color' below, same "
            "as any other line). Shift-drag a diameter endpoint to reshape the "
            "ellipse; Ctrl+Shift-drag one instead to ROTATE it -- slide it "
            "around the circle's already-fixed boundary without changing the "
            "circle's shape (Make Parallel does this too, for a diameter "
            "line). Shift-drag the circle's own curve (not either diameter "
            "line) to move the whole thing. The 2 diameter lines always share "
            "the same known length -- type it into either one's known-length "
            "field and the other updates to match automatically, since "
            "they're 2 measurements of the same circle. Snap Mode also pulls "
            "onto a circle's exact center and its edge.\n"
            "15b. Every line -- not just a circle's -- can be reassigned to a "
            "different color/axis (or back to orange, meaning 'no axis') any "
            "time via the 'Color' row in the known-length panel, right under "
            "the length field. Recoloring never changes the line's own known "
            "length, only which axis it calibrates.\n"
            "16. The left-side options panel can be resized -- drag the "
            "divider between it and the photo -- and scrolls with the mouse "
            "wheel if the window is too short to show everything at once. "
            "Esc, when nothing else is mid-action (drawing a line, dragging "
            "a point, etc.), just clears whatever's currently selected."
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

    def _on_fraction_denominator_change(self, *args):
        # Every open tab may have lines currently shown as fractions, not
        # just the active one -- redraw all of them so the new rounding
        # takes effect everywhere at once.
        for tab in self._all_tabs():
            tab.redraw()

    def _on_default_display_mode_change(self, *args):
        # Any axis that's never had an explicit mode set (axis_display_mode
        # is None for it) follows this setting live -- redraw every open
        # tab so that takes effect immediately, and refresh the active
        # tab's known-length panel too since the Fraction denom row's
        # visibility depends on the now-current effective mode.
        for tab in self._all_tabs():
            tab.redraw()
        tab = self.active_tab()
        if tab:
            tab.populate_known_length_field()

    def _on_default_unit_change(self, *args):
        # Only the active tab's known-length panel can be showing right
        # now -- refresh it so a selected line with no unit of its own yet
        # (nothing measured for it) immediately shows the new default in
        # its unit box, instead of only picking it up next time that line
        # gets (re)selected. A background tab's panel refreshes itself the
        # same way whenever it becomes active (see _on_tab_changed).
        tab = self.active_tab()
        if tab:
            tab.populate_known_length_field()

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

    def _make_parallel_shortcut(self, event=None):
        """'P' toggles the Make Parallel tool -- but not while typing/
        picking in the known-length field or any of the Display combos,
        where 'p' obviously means the letter (e.g. typing into a unit box
        that has 'px' as a choice), not the shortcut."""
        tab = self.active_tab()
        if tab is None:
            return None
        guarded = (tab.known_length_entry, tab.known_unit_box, tab.display_mode_box,
                   tab.display_unit_box, tab.display_denominator_box)
        if self.root.focus_get() in guarded:
            return None
        tab.toggle_make_parallel()
        return "break"

    def _snap_mode_shortcut(self, event=None):
        """'S' toggles Snap Mode -- guarded the same way as 'P' (Make
        Parallel) so typing 's' in a text field isn't hijacked."""
        tab = self.active_tab()
        guarded = ()
        if tab is not None:
            guarded = (tab.known_length_entry, tab.known_unit_box, tab.display_mode_box,
                       tab.display_unit_box, tab.display_denominator_box)
        if self.root.focus_get() in guarded:
            return None
        self.snap_mode.set(not self.snap_mode.get())
        self.set_status(f"Snap mode {'on' if self.snap_mode.get() else 'off'} -- "
                         f"placing or moving a vertex now "
                         f"{'snaps to nearby lines/vertices' if self.snap_mode.get() else 'no longer snaps'}.")
        return "break"

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
        if tab.parallel_picking:
            self.parallel_button.config(text="Cancel Parallel (Esc)")
            if tab.parallel_picking == "first":
                self.parallel_hint.config(
                    text="Make Parallel: click the REFERENCE line (canvas or list)")
            else:
                ln = tab._line_by_id(tab.parallel_first_id)
                ref_label = f"#{ln.id} ({ln.color})" if ln else "?"
                self.parallel_hint.config(
                    text=f"Make Parallel: click the line to make parallel to {ref_label}")
            return
        self.parallel_button.config(text="Make Parallel (P)")
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
                    "default_unit": self.default_unit.get(),
                    "fraction_denominator": self.fraction_denominator.get(),
                    "default_display_mode": self.default_display_mode.get()}
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
        if data.get("fraction_denominator") in FRACTION_DENOMINATOR_CHOICES:
            self.fraction_denominator.set(data["fraction_denominator"])
        if data.get("default_display_mode") in DISPLAY_MODE_CHOICES:
            self.default_display_mode.set(data["default_display_mode"])

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
                tab.ellipses = [Ellipse.from_dict(d) for d in entry.get("ellipses", [])]
                tab.selected_line_ids = []
                saved_modes = entry.get("axis_display_mode") or {}
                tab.axis_display_mode = {
                    color: saved_modes.get(color) for color in AXIS_COLORS}
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
