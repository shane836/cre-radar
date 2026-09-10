"""Re-shoot the link-preview card into `assets/og-card.png`.

    uv run python scripts/shoot-card.py

**Why a narrow viewport at a high scale factor, and not 1200x627 directly.**
The page's content column is 640px. Shot at a 1200px viewport it floats in
whitespace, and once LinkedIn renders the card at a few hundred pixels wide the
header and headline turn to mush — which is exactly what the first version did.
Shooting at 628px means the column fills the frame, so the brand and the
headline survive the downscale. The listing below them is allowed to go soft:
it reads as texture, not as content anyone squints at.

The card goes stale by design — it shows whatever events were on the page the
day it was taken. Re-run this when the listing on it looks old.
"""
from __future__ import annotations

import pathlib

from playwright.sync_api import sync_playwright

URL = "https://radar.masonequitypartners.com/"
OUT = pathlib.Path(__file__).resolve().parents[1] / "assets" / "og-card.png"

# 628 x 328 at 3.83x lands on ~2400x1256: LinkedIn's 1.91:1 crop at 2x, with
# the content column filling the width.
WIDTH, HEIGHT, SCALE = 628, 328, 3.83


def main() -> None:
    with sync_playwright() as play:
        browser = play.chromium.launch()
        # Forced light: the card is shown on both LinkedIn themes and the dark
        # page reads as a bug against their white feed.
        page = browser.new_page(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=SCALE,
            color_scheme="light",
        )
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.screenshot(path=OUT)
        browser.close()
    print(f"card -> {OUT}")


if __name__ == "__main__":
    main()
