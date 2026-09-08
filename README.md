# Image Measure Tool

A desktop tool for measuring real-world distances from a photo, with X/Y/Z axis
color-coding, cross-line comparisons, and parallel-line drawing. Each open
photo lives in its own tab, and the whole window -- every tab, even ones you
never explicitly saved -- is remembered automatically between runs.

## Setup

```
pip install pillow tkinterdnd2
```

`tkinterdnd2` is optional and only enables dragging an image file onto a tab
to open it -- everything else works without it, you'd just use File > Open
Image instead. (Tkinter ships with the standard Python installer on
Windows/macOS. On Linux, install your distro's `python3-tk` package if
`import tkinter` fails.)

## Run

```
python image_measure_tool.py
```

## Tabs

- Every open photo/project is its own tab, with its own image, lines, zoom
  and pan -- so you can work on several at once. **New Tab** (Ctrl+T) or
  **File > Open Image** (Ctrl+O) starts another; the small **✕** on a tab
  closes it (middle-click a tab, or Ctrl+W / File > Close Tab, do the same
  thing). If a tab has measurements that were never saved as a project
  file, closing it asks you to confirm first.
- The **Tabs ▾** button in the toolbar, next to New Tab and Close Tab, lists
  every open tab by name (with a ✓ on whichever one's active) so you can
  jump straight to one even if it's been clipped off-screen by a narrow
  window or a lot of open tabs.
- The color/mode toolbar at the top and the Edit menu always act on
  whichever tab is currently in front.
- The toolbar itself scrolls horizontally rather than clipping buttons at a
  narrow window width -- shrink the window enough and a thin scrollbar
  appears right under it (scroll it with the mouse wheel too, no need to
  grab the scrollbar itself), so every button stays reachable no matter
  how small the window gets. It disappears again once the window's wide
  enough that nothing overflows.
- Drag either an image **or a `.imt` project file** onto a tab to open it --
  same rule either way: onto a tab that already has something open, it
  loads into a **new** tab; onto an empty tab, it loads right there.

## Where things are saved

There are two separate kinds of file, for two separate purposes:

- **Project files** (`File > Save Project`, `.imt`) are the deliberate,
  portable save format -- one file per project, containing that tab's image
  path, **a full copy of the image itself**, its rotation, and every line
  (color, endpoints, known length, unit). Because the image is embedded,
  the project still opens correctly even if the photo gets moved, renamed,
  or the `.imt` file ends up on a different computer without it -- opening
  it just falls back to the embedded copy and tells you so in the status
  bar. The tradeoff is size: a `.imt` file is roughly the image's size plus
  ~33% (base64 encoding overhead), so it's noticeably bigger than the photo
  alone -- fine for normal use, just don't be surprised. (Older projects
  saved as `.imt.json` before this still open fine; they just don't have an
  embedded image, so if the original file has also moved you'll be asked to
  locate it, same as before.) Save it next to the photo, move it, share it,
  keep it in version control -- it's a normal file you control.
- **The session** is a small file the app writes to on its own, every time
  it closes (and every minute while it's open, in case of a crash): which
  tabs were open and every line in them (whether or not you ever hit Save
  Project), plus program-wide settings like Default Unit. The next time you
  launch the app, it's read back automatically and the window comes back
  exactly as you left it. It lives in your
  per-user app-data folder (`%APPDATA%\ImageMeasureTool\session.json` on
  Windows; `~/Library/Application Support/ImageMeasureTool/session.json` on
  macOS; `~/.config/ImageMeasureTool/session.json` on Linux) rather than
  next to the script, so it stays local to each computer instead of getting
  synced by OneDrive/Git, and each machine keeps its own "last state"
  independently. If a tab's image file is missing when the session is
  restored (moved, renamed, deleted) but that tab was ever saved as a
  project, the session falls back to the image embedded in that `.imt`
  file automatically, the same way opening the project directly would.

Closing a tab with the ✕ removes it right away, even from the session --
only tabs still open when the app quits get carried forward automatically.
If you want a project to survive on its own regardless, use Save Project.

## How it works

- **File > Open Image** loads a photo, or just drag an image file onto a
  tab. A photo from a phone or camera
  that comes in sideways is automatically rotated to match how Windows/Photos
  displays it (both read the same EXIF orientation tag in the file).
- Pick a color at the top: **Red = X**, **Green = Y**, **Blue = Z** -- or just
  press the **Space bar** to cycle to the next color, so you don't have to
  reach for the mouse between measurements. Space is ignored (and can't type
  a stray space) while the known-length field has focus -- typing a length
  always takes priority there. These are just labels for three independent
  axes you calibrate separately (e.g.
  red for horizontal, green for vertical, blue for depth) -- use them however
  makes sense for your shot.
- Draw a line in the selected color two ways: **hold** the mouse button and
  drag, releasing to finish it -- or just **click** once to start it, move
  the mouse to aim (the line follows your cursor live, no button held), and
  **click again** to finish it wherever you land. **Esc** cancels a line
  started this way before you've closed it. Either way, the finished line is
  selected automatically and the **known length** field in the panel on the
  left shows it -- no popup, and nothing is focused yet, so the **Space bar**
  still switches color immediately if that's what you want to do next. To
  set the length, just start typing a number -- that's what focuses the
  field, replacing whatever was there, and it starts out in whatever unit
  you've set as the **default unit** (Settings menu -- see below) rather
  than making you re-pick a unit for every line. Enter commits it. While
  you're editing, **Esc reverts the field to what it was before you started
  typing** (without changing the line at all) and **Space does the same,
  then immediately switches color** -- so you can always bail out of an
  edit and keep moving instead of having to clear it by hand. Nothing is
  required, and you can always come back later by clicking the line (or
  its row in the list below) and typing again.
- **Settings > Default Unit** sets which unit (mm/cm/m/in/ft/px) a newly
  drawn line starts with, program-wide -- change it once instead of on
  every line, and it's remembered the same way everything else is (see
  "Where things are saved" above). Any individual line's unit can still be
  changed afterward in its own known-length field regardless of this
  setting.
- **Rotate 90°** (toolbar, or Edit menu) rotates the photo a quarter turn
  clockwise and keeps every line attached to the same spot on the picture.
  This rotation is remembered along with the project/session, so reopening
  a rotated project shows it rotated the same way.
- Once one line of a color has a known length, every other line of that same
  color automatically shows a computed real-world length, both on the canvas
  and in the side list -- because the tool now knows that color's
  pixels-per-unit scale.
- **Compare 2 Lines** (Edit menu): select any two lines in the side list
  (Ctrl/Shift-click) first, then use it. It computes the second line's
  real-world length from the pixel-length ratio to the first -- even if
  they're different colors/axes, and without needing a fully calibrated
  axis. You can apply the result as that line's known length. (Deleting a
  line is also Edit menu / Delete or BackSpace now -- both used to also
  have their own toolbar buttons, removed to keep the toolbar to the
  things you reach for on every line.)
- **Parallel (Shift)**: hold Shift while dragging a new line and it snaps to
  run parallel to a reference line -- whichever line is selected in the side
  list, or the last line you drew if nothing's selected. The current
  reference is always shown next to the toolbar buttons. Useful for
  measuring opposite edges of an object (e.g. both long sides of a box)
  even if your drag isn't pixel-perfect.
- **Editing endpoints**: hover over the end of a line drawn in the *current*
  color and the cursor turns into a move icon -- drag it to reposition that
  point. Hover over the end of a line in a *different* color and the cursor
  turns into a hand instead: starting a new line there snaps its start point
  exactly onto that vertex, so segments measuring different axes can share
  a precise corner (e.g. a red X-edge and a green Y-edge meeting at the same
  pixel).
- **Save Project / Open Project** stores the image path, a copy of the image
  itself, rotation, and every line (color, endpoints, known length, unit) in
  a single `.imt` file so you can pick up where you left off -- even from
  another computer. See "Where things are saved" above for how this differs
  from the automatic session.
- **Export Measurements (CSV)** dumps every line's pixel length, computed
  real length, unit, color/axis, and endpoints to a spreadsheet-friendly file.

## Notes / limitations

- This assumes a roughly fronto-parallel view for each axis you calibrate --
  it does not correct for lens distortion or perspective. If your photo has
  strong perspective, calibrate a red/green/blue reference line *close to*
  each set of lines you're measuring with that axis, rather than one
  calibration for the whole image.
- Scroll the mouse wheel to zoom in/out towards wherever your cursor is
  pointing (or use the Zoom In/Out/Fit toolbar buttons, which zoom towards
  the center). Hold the **middle mouse button** and drag to pan. Only the
  portion of the photo actually visible on screen gets resized each frame --
  and zooming out uses a pre-shrunk version of the photo instead of
  downsizing the full-resolution original every frame -- so both zooming in
  tight and zooming way out on a large photo stay smooth.
- If you zoom or pan (scroll wheel / middle-mouse drag) in the middle of
  dragging out a new line, the line's start point stays pinned to the actual
  spot on the photo you clicked, instead of stretching to wherever that
  screen pixel ended up.
- This is the mirror image of the other direction: the `.imt` project file
  embeds a copy of the *photo* so it's self-contained, but measurements
  still don't get written into the photo's own metadata (EXIF/XMP for JPEG,
  a text chunk for PNG). That's a deliberate choice -- some tools strip
  embedded metadata on re-save, and it's harder to inspect/diff than the
  `.imt` file -- but ask if it'd be useful as an additional, optional
  export and it can be added.
