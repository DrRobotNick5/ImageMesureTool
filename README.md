# Image Measure Tool

A desktop tool for measuring real-world distances from a photo, with X/Y/Z axis
color-coding, cross-line comparisons, and parallel-line drawing.

## Setup

```
pip install pillow
```

(Tkinter ships with the standard Python installer on Windows/macOS. On Linux,
install your distro's `python3-tk` package if `import tkinter` fails.)

## Run

```
python image_measure_tool.py
```

## How it works

- **File > Open Image** loads a photo.
- Pick a color at the top: **Red = X**, **Green = Y**, **Blue = Z**. These are
  just labels for three independent axes you calibrate separately (e.g. red
  for horizontal, green for vertical, blue for depth) -- use them however
  makes sense for your shot.
- Drag on the image to draw a line in the selected color. You'll be prompted
  for its real-world length (any unit: mm/cm/m/in/ft/px). You can skip this
  and fill it in later by selecting the line and clicking **Set Known Length**.
- Once one line of a color has a known length, every other line of that same
  color automatically shows a computed real-world length, both on the canvas
  and in the side list -- because the tool now knows that color's
  pixels-per-unit scale.
- **Compare 2 Lines**: select any two lines in the side list (Ctrl/Shift-click)
  and click this button. It computes the second line's real-world length from
  the pixel-length ratio to the first -- even if they're different colors/axes,
  and without needing a fully calibrated axis. You can apply the result as
  that line's known length.
- **Draw parallel**: switch to this mode, click near an existing line (locks
  its direction), then drag anywhere on the image -- the new line is forced
  to the same angle as the reference line. Useful for measuring opposite
  edges of an object (e.g. both long sides of a box) even if your drag isn't
  pixel-perfect.
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
- Zoom with the mouse wheel or the Zoom In/Out/Fit buttons; scrollbars pan
  around when zoomed in, which helps with precise clicks on high-res photos.
"# ImageMesureTool" 
