# Image Measure Tool

A desktop tool for measuring real-world distances from a photo, with X/Y/Z axis
color-coding, cross-line comparisons, and parallel-line drawing.

## Setup

```
pip install pillow tkinterdnd2
```

`tkinterdnd2` is optional and only enables dragging an image file onto the
window to open it -- everything else works without it, you'd just use
File > Open Image instead. (Tkinter ships with the standard Python installer
on Windows/macOS. On Linux, install your distro's `python3-tk` package if
`import tkinter` fails.)

## Run

```
python image_measure_tool.py
```

## How it works

- **File > Open Image** loads a photo, or just drag an image file onto the
  window (needs `tkinterdnd2` -- see Setup). A photo from a phone or camera
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
  left is focused, ready to type into -- no popup. Type a number and press
  Enter (any unit: mm/cm/m/in/ft/px), or leave it blank; nothing is required,
  and you can always come back later by clicking the line (or its row in the
  list below) and typing into that same field.
- **Rotate 90°** (toolbar, or Edit menu) rotates the photo a quarter turn
  clockwise and keeps every line attached to the same spot on the picture.
- Once one line of a color has a known length, every other line of that same
  color automatically shows a computed real-world length, both on the canvas
  and in the side list -- because the tool now knows that color's
  pixels-per-unit scale.
- **Compare 2 Lines**: select any two lines in the side list (Ctrl/Shift-click)
  and click this button. It computes the second line's real-world length from
  the pixel-length ratio to the first -- even if they're different colors/axes,
  and without needing a fully calibrated axis. You can apply the result as
  that line's known length.
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
- **Save Project / Open Project** stores the image path and every line
  (color, endpoints, known length, unit) in a `.json` file so you can pick up
  where you left off.
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
