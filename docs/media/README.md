# Showcase assets

`demo.gif` is a program-drawn, 24-second feature illustration with synthetic data, not a screen recording or a new product UI. `demo-poster.png` is the static alternative. `architecture.svg` is the editable technical diagram.

## Rebuild

From the repository root, using a separate Python environment:

```sh
python -m pip install -r docs/media/requirements.txt
python docs/media/generate.py
```

`generate.py` owns the project-specific scenes; `motion.py` draws the shallow physical cards, spring motion, and signal paths. Pillow is an asset-authoring dependency only, not a product runtime dependency. Fonts are discovered on Windows, Linux and macOS; use `SHOWCASE_FONT` and `SHOWCASE_FONT_BOLD` to select alternate local TrueType fonts. Font files are not redistributed.

Output: 1120 × 640 GIF, 12 fps, one global palette, a final reading hold, and a static PNG. Set `SHOWCASE_REVIEW` to an output PNG path for a six-frame review sheet. The SVG can be edited directly without a build tool.

Animation and architecture are illustrative. Current behavior and verification boundaries remain defined by the source, README and linked validation records.
