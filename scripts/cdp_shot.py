"""Convert a saved CDP Page.captureScreenshot response into an optimized WebP (or PNG).

Usage: uv run python scripts/cdp_shot.py <cdp-response.json> <out.webp> [max_width]
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    width = sys.argv[3] if len(sys.argv) > 3 else "1600"
    data = json.loads(source.read_text())
    raw = base64.b64decode(data.get("data") or data["result"]["data"])
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".png") as png:
        png.write(raw)
        png.flush()
        args = ["magick", png.name, "-resize", f"{width}x>", "-strip"]
        if target.suffix == ".webp":
            args += ["-quality", "82"]
        subprocess.run([*args, str(target)], check=True)
    print(target, target.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
