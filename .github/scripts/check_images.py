"""Check that every image in the Markdown files actually renders.

Stats cards and badges often answer 200 even when broken: the error is drawn
inside the SVG ("Something went wrong!", "inaccessible", ...). A plain link
checker cannot see that, so this script downloads each image and looks inside.

Images are discovered automatically from Markdown (`![alt](url)`) and HTML
(`<img src>`, `<source srcset>`), so new images need no changes here.

Usage: python check_images.py [FILE.md ...]   (default: every *.md in the repo)
"""

from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

IMAGE_PATTERNS = [
    re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)"),
    re.compile(r"<img\b[^>]*?\ssrc=\"(https?://[^\"]+)\"", re.S),
    re.compile(r"<source\b[^>]*?\ssrcset=\"(https?://[^\"]+)\"", re.S),
]

# Text that services draw inside an image when something went wrong.
ERROR_MARKERS = [
    "something went wrong",  # github-readme-stats
    "could not resolve",  # github-readme-stats: unknown user
    "bad credentials",  # github-readme-stats: expired/invalid token
    "rate limit",  # any GitHub-backed card
    "inaccessible",  # shields.io: upstream unreachable
    "invalid",  # shields.io: bad parameters
    "not found",  # shields.io / streak stats
]

ATTEMPTS = 3
TIMEOUT = 30


def find_images(files: list[Path]) -> dict[str, list[str]]:
    """Map each image URL to the files that reference it."""
    images: dict[str, list[str]] = {}
    for file in files:
        text = file.read_text(encoding="utf-8")
        for pattern in IMAGE_PATTERNS:
            for url in pattern.findall(text):
                images.setdefault(unescape(url), []).append(str(file))
    return images


def fetch(url: str) -> tuple[int, str, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "readme-image-check"})
    last_error: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.status, response.headers.get("Content-Type", ""), response.read()
        except urllib.error.HTTPError as error:
            if error.code < 500 and error.code != 429:
                return error.code, error.headers.get("Content-Type", ""), error.read()
            last_error = error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"no response after {ATTEMPTS} attempts: {last_error}")


def problems_for(url: str) -> list[str]:
    try:
        status, content_type, body = fetch(url)
    except RuntimeError as error:
        return [str(error)]

    if not 200 <= status < 300:
        return [f"HTTP {status}"]
    if not content_type.startswith("image/"):
        return [f"not an image (Content-Type: {content_type or 'missing'})"]
    if "svg" not in content_type:
        return []

    svg = body.decode("utf-8", errors="replace")
    visible_text = " ".join(re.findall(r">([^<]+)<", svg)).lower()
    problems = [f"shows '{marker}'" for marker in ERROR_MARKERS if marker in visible_text]

    # skillicons.dev silently drops unknown icon names: compare requested vs rendered.
    parsed = urlparse(url)
    if parsed.hostname == "skillicons.dev":
        requested = [i for i in parse_qs(parsed.query).get("i", [""])[0].split(",") if i]
        rendered = svg.count("<svg") - 1  # one outer <svg> wraps one <svg> per icon
        if rendered != len(requested):
            problems.append(f"renders {rendered} of {len(requested)} requested icons")

    return problems


def main() -> int:
    files = [Path(f) for f in sys.argv[1:]] or sorted(
        p for p in Path(".").rglob("*.md") if not any(part.startswith(".") for part in p.parts)
    )
    images = find_images(files)
    if not images:
        print("No images found.")
        return 1

    failures = 0
    for url, sources in images.items():
        problems = problems_for(url)
        status = "FAIL" if problems else "ok"
        print(f"[{status}] {url}")
        for problem in problems:
            failures += 1
            print(f"       -> {problem} (in {', '.join(sorted(set(sources)))})")
            print(f"::error file={sources[0]}::{url} {problem}")

    print(f"\n{len(images)} images checked, {failures} problem(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
