"""Build self-contained HTML wrappers and crisp PNG exports for V2 diagrams.

The SVG files are the editable source.  The HTML wrappers keep each figure
portable for browser review, while headless Chrome creates high-resolution
PNG assets for the Word document.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SVG_DIR = ROOT / "assets"
HTML_DIR = ROOT / "assets" / "v2_html"
PNG_DIR = ROOT / "assets" / "v2_png"
CHROME = "/usr/bin/google-chrome"


def viewbox_size(svg: str) -> tuple[int, int]:
    match = re.search(r'viewBox="\s*0\s+0\s+(\d+)\s+(\d+)\s*"', svg)
    if not match:
        raise ValueError("SVG is missing a numeric viewBox")
    return int(match.group(1)), int(match.group(2))


def wrap_svg(svg: str, width: int, height: int) -> str:
    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width={width}, initial-scale=1">
  <title>AI GURU — diagram</title>
  <style>
    html, body {{ margin: 0; padding: 0; background: #FAF9F5; }}
    body {{ width: {width}px; min-height: {height}px; overflow: hidden; }}
    svg {{ display: block; width: {width}px; height: {height}px; }}
  </style>
</head>
<body>
{svg}
</body>
</html>
"""


def main() -> None:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    names = sorted(SVG_DIR.glob("*_v2.svg"))
    if not names:
        raise SystemExit("No *_v2.svg files found")

    for svg_path in names:
        svg = svg_path.read_text(encoding="utf-8")
        width, height = viewbox_size(svg)
        html_path = HTML_DIR / f"{svg_path.stem}.html"
        png_path = PNG_DIR / f"{svg_path.stem}.png"
        html_path.write_text(wrap_svg(svg, width, height), encoding="utf-8")

        command = [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--force-device-scale-factor=2",
            f"--window-size={width},{height}",
            f"--screenshot={png_path}",
            html_path.as_uri(),
        ]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"{html_path} -> {png_path}")


if __name__ == "__main__":
    main()
