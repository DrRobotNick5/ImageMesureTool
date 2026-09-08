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
  press **Tab** (**Shift+Tab** to go back) to cycle to the next color, so you
  don't have to reach for the mouse between measurements. Tab does this even
  while the known-length field has focus -- it never jumps to another field
  there, it cycles the color instead (see below for exactly what it does to
  the field itself). These are just labels for three independent
  axes you calibrate separately (e.g.
  red for horizontal, green for vertical, blue for depth) -- use them however
  makes sense for your shot.
- Draw a line in the selected color two ways: **hold** the mouse button and
  drag, releasing to finish it -- or just **click** once to start it, move
  the mouse to aim (the line follows your cursor live, no button held), and
  **click again** to finish it wherever you land. **Esc** cancels a line
  started this way before you've closed it. Either way, the finished line is
  selected automatically and the **known length** field in the panel on the
  left shows it -- no popup, and nothing is focused yet, so **Tab**
  still switches color immediately if that's what you want to do next. To
  set the length, just start typing -- that's what focuses the field,
  replacing whatever was there, and it starts out in whatever unit you've
  set as the **default unit** (Settings menu -- see below) rather than
  making you re-pick a unit for every line. It doesn't have to be a plain
  decimal: fractions, mixed numbers, and simple math all work too --
  `1/2`, `2 1/2` (no `+` needed), `2 + 1/2`, `3/4 - 1/8` are all valid and
  parsed exactly, not through floating-point. Enter commits it. While
  you're editing, **Esc reverts the field to what it was before you started
  typing** (without changing the line at all) and **Tab (or Shift+Tab) does
  the same revert, then immediately switches color** -- so you can always
  bail out of an edit and keep moving without reaching for the mouse; Tab
  never jumps to another field while you're editing. Nothing is required,
  and you can always come back later by clicking the line (or its row in
  the list below) and typing again.
- **Settings > Default Unit** sets which unit (mm/cm/m/in/ft/px) the
  known-length field starts on for any line that hasn't actually been
  measured yet, program-wide -- change it once instead of on every line,
  and it's remembered the same way everything else is (see "Where things
  are saved" above). It applies live: a line you already drew but haven't
  typed a known length for yet keeps tracking this setting -- even while
  it's the one currently selected -- right up until you actually commit a
  known length for it. The moment you do, that unit becomes real measured
  data for that specific line and stays put from then on, unaffected by
  further Default Unit changes (exactly like any other line's unit can
  always be changed by hand afterward in its own known-length field,
  regardless of this setting).
- **Fractions and per-line display**: the known-length field itself always
  understands fraction notation (`1/2`, `2 1/2`, `2 + 1/2`) no matter how
  lengths are currently being displayed -- but *typing* one never changes
  how anything is displayed. Display mode (decimal vs. fraction) is
  controlled by exactly two things, and nothing else: **Settings > Default
  Display Mode**, and the per-line **Display** section described below.
  **Settings > Default Display Mode** sets whether decimal or fraction is
  used everywhere, program-wide, for any line that doesn't have its own
  override -- flip it and every such line updates immediately, in every
  open tab, whether it already existed or gets drawn afterward. A
  displayed fraction always shows as one dash-joined mixed number, e.g.
  `6-5/8 in`, not `6 5/8 in` -- so it can't be misread as two separate
  numbers. **Settings > Fraction Denominator** changes
  what "nearest" rounds to program-wide (1/2 through 1/64). The **Display**
  section under the known-length field overrides just the *selected* line,
  independent of every other line and independent of whether it has its
  own known length or is only computed from the axis: pick **decimal** or
  **fraction**, a different unit than the axis was calibrated in (e.g. a
  line calibrated in fractional inches can still show as decimal mm -- the
  length is converted, not just relabeled), and/or that line's *own*
  nearest-fraction denominator, regardless of the program-wide Fraction
  Denominator setting (handy for a line that needs finer or coarser
  rounding than the rest -- e.g. nearest 1/64 on one line while everything
  else stays at 1/32). Leave all three on **(auto)** to just follow the
  program-wide Default Display Mode, calibration unit, and Fraction
  Denominator, which is what most lines want. (Pixels can't convert to/from
  a physical unit, so a display unit of `px` only applies to a line whose
  axis is also calibrated in `px`.) The **Fraction denom** row only appears
  when the selected line is actually showing as a fraction (its own
  override, or the live Default Display Mode setting) -- it's meaningless
  in decimal mode, so it stays out of the way rather than sitting there
  disabled.
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
- **Make Parallel** (toolbar button, Edit menu, or just press **P**): turns
  a line you *already drew* to make it run parallel to another one, instead
  of only helping while drawing a brand-new one. Its length doesn't change
  -- only its direction -- pivoting around its own first point, so if that
  point happens to be one you snapped onto another line's vertex, that
  connection stays put. If you already have exactly one line selected when
  you activate it, that line becomes the reference immediately and it asks
  you to click the line that should become parallel to it; otherwise it
  asks for the reference first, then the one to change -- either way, pick
  both by clicking on the photo or by clicking a row in the side list, in
  any combination. Esc cancels at any point, and pressing **P** (or the
  button) again while it's active cancels it too.
- **Chaining a new line onto an existing vertex**: hover over the end of any
  line -- same color or different -- and the cursor turns into a hand;
  clicking there and dragging away starts a brand-new line with its start
  point snapped exactly onto that vertex, so segments measuring different
  axes (or another line of the same axis) can share a precise corner (e.g. a
  red X-edge and a green Y-edge meeting at the same pixel). It never moves
  the vertex you clicked on -- to move an existing point, see Shift+drag
  below.
- **Ctrl+click** selects whatever line is under the cursor, whatever color it
  is and whatever mode (Draw or Select) you're in -- clicking a second line
  while holding Ctrl adds it to the selection, and Ctrl-clicking an
  already-selected line removes it. A Ctrl+click on empty space leaves the
  current selection alone rather than clearing it.
- **Shift+drag** moves an existing line, any color, in either mode: grab one
  of its endpoints and only that point moves. Grab anywhere else along the
  line's body and the whole line translates -- both endpoints shift by the
  same amount, so its length and direction stay exactly the same, just
  moved. Shift-dragging on empty canvas still works as before (constrains a
  new line parallel to the reference); this only kicks in when
  Shift-grabbing an existing line or vertex.
  - Grabbing a vertex with a quick Shift+**click** (rather than holding the
    button down) picks the point up instead: it detaches and follows the
    cursor -- no button held -- until you click again to place it, the
    same click-then-click pattern as drawing a line. While it's following,
    hold Shift to keep it exactly collinear with the line's *original*
    direction, so you can extend or shorten the line precisely along its
    own axis (handy when the endpoint you're chasing is far away, or you
    want to zoom/pan first without having to keep the mouse button held
    the whole time). Let go of Shift mid-follow and it moves freely again;
    hold it again on the finishing click to snap back in line. Esc cancels
    and puts the point back exactly where it was. An actual press-and-hold
    drag past the usual click threshold still finalizes immediately on
    release, same as before -- this is only for a genuine short click.
- **Circles** (a 4th, "orange" line type): pick **Circle** mode and click 3
  points -- the center, then a point on the circumference, then a 2nd point
  on the circumference in a *different* direction (it doesn't need to be a
  right angle from the 1st one). This draws an ellipse -- a circle seen at
  whatever perspective the photo shows it from -- plus its 2 diameter
  **lines**, each running the full width of the circle through the center.
  Both start out **orange**, a 4th line color that (unlike Red/Green/Blue)
  isn't a measurement axis on its own -- it just marks "this diameter isn't
  assigned to an axis yet."
    - **Reshaping**: Shift-drag any of the ellipse's 4 defining points (the
      2 diameter lines' 4 endpoints) to move it freely -- the point directly
      opposite it (through the center, on the same line) mirrors
      automatically, and the ellipse's shape updates to match. This is how
      you fit the ellipse to what's actually in the photo.
    - **Rotating**: Ctrl+Shift-drag a diameter endpoint instead to *rotate*
      it -- this slides the point around the ellipse's current (already-
      fixed) boundary instead of reshaping it, so the circle's size/shape
      never changes, only where its 2 diameters point. **Make Parallel**
      does the same thing to a diameter line: it rotates within the fixed
      ellipse to match the reference direction, rather than pivoting freely
      like an ordinary line would (which would pull it off the circle).
    - **Moving the whole circle**: Shift-drag the ellipse's own curve
      (not one of the 2 straight diameter lines) to translate the entire
      thing -- center and all 4 points together, shape unchanged.
    - **Known size and axis assignment**: select one of a circle's diameter
      lines and the **Circle** panel (below the known-length field) shows
      buttons to assign that line to a real axis (Red/Green/Blue) -- or
      back to orange/unassigned -- plus a field for the circle's own known
      radius or diameter. Once a diameter is assigned to an axis, that
      known radius/diameter (doubled, if you entered a radius) becomes
      *that line's* known length automatically, calibrating the axis just
      like typing a known length in directly. It stays fully movable,
      reshapable, and rotatable afterward, same as any other line.
      Reassigning it (to a different axis, or back to orange) clears that
      known length, since it no longer means the same thing.
    - Deleting either of a circle's 2 diameter lines deletes the whole
      circle. Undo/redo, Save Project, and Snap Mode (below) all cover
      circles the same way they cover ordinary lines.
- **Snap Mode** (toolbar checkbox, Edit menu, or press **S**): while on,
  placing or moving a point -- drawing a new line's endpoints, or
  Shift-dragging an existing vertex -- pulls it onto a nearby vertex first
  (a circle's exact center counts as a vertex here too), or the nearest
  point along a nearby line's body or a nearby circle's edge if no vertex
  is close enough, within the same hit-test distance used for clicking a
  line. A point never snaps onto the very line/vertex it's already part of.
  Off by default; toggling it back off goes back to placing points exactly
  where you click.
- **Undo / Redo** (**Ctrl+Z** / **Ctrl+Y**, or Edit menu): steps back through
  drawing a line, deleting line(s), dragging an endpoint (one step per drag,
  not per pixel it moved), Make Parallel, a known-length or Display-section
  change, and Rotate 90° -- one step per action, in order, same as any other
  editor. Ctrl+Y redoes; doing anything new after an undo drops whatever was
  available to redo, same as everywhere else. Opening a different image (or
  a project) into a tab starts that tab's undo history fresh.
- **Save Project / Open Project** stores the image path, a copy of the image
  itself, rotation, and every line (color, endpoints, known length, unit) in
  a single `.imt` file so you can pick up where you left off -- even from
  another computer. See "Where things are saved" above for how this differs
  from the automatic session.
- **Export Measurements (CSV)** dumps every line's pixel length, computed
  real length, unit, color/axis, and endpoints to a spreadsheet-friendly
  file -- plus a `displayed_length` column with the same value formatted
  the way it actually shows in the app (fraction or decimal, in that
  line's own display unit).

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
