"""Check that every image in the Markdown files actually renders.

Stats cards and badges often answer 200 even when broken: the error is drawn
inside the SVG ("Something went wrong!", "inaccessible", ...). A plain link
checker cannot see that, so this script downloads each image and looks inside.

Images are discovered automatically from Markdown (inline and reference-style)
and HTML (`<img src>`, `<source srcset>`), so new images need no changes here.
Only remote (http/https) images are checked; local files are lychee's job.

Usage: python check_images.py [FILE.md ...]   (default: every *.md in the repo)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import urllib3
from urllib3.util import Retry, Timeout

INLINE_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?(https?://[^)\s>]+)")
REFERENCE_IMAGE = re.compile(r"!\[([^\]]*)\]\[([^\]]*)\]")
REFERENCE_DEFINITION = re.compile(r"^[ ]{0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s|$)", re.M)
HTML_IMAGE = re.compile(r"<(?:img|source)\b[^>]*?\s(?:src|srcset)\s*=\s*([\"'])(https?://.+?)\1", re.S | re.I)

# Text that services draw inside an image when something went wrong.
# Markers under None apply to every image: they are specific enough not to appear in a healthy one.
ERROR_MARKERS: dict[str | None, tuple[str, ...]] = {
    None: (
        "something went wrong",  # github-readme-stats (any self-hosted instance)
        "could not resolve",  # github-readme-stats: unknown user
        "bad credentials",  # GitHub-backed cards: expired or invalid token
        "rate limit",  # GitHub-backed cards
    ),
    # shields.io and services built on it draw short generic words: only trust them there.
    "img.shields.io": ("inaccessible", "invalid", "not found"),
    "custom-icon-badges.demolab.com": ("inaccessible", "invalid", "not found"),
}

RETRY = Retry(
    total=3,
    backoff_factor=2,  # waits 0s, 4s, 8s between attempts
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",),
    raise_on_status=False,  # after the last retry, return the response so it can be reported
)
HTTP = urllib3.PoolManager(
    retries=RETRY,
    timeout=Timeout(connect=10, read=30),
    headers={"User-Agent": "readme-image-check"},
)


@dataclass(frozen=True)
class Location:
    file: str
    line: int


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def find_images(files: list[Path]) -> dict[str, list[Location]]:
    """Map each remote image URL to every place that references it."""
    images: dict[str, list[Location]] = {}

    def add(url: str, file: Path, text: str, index: int) -> None:
        url = unescape(url)
        if url.startswith(("http://", "https://")):
            images.setdefault(url, []).append(Location(str(file), _line_of(text, index)))

    for file in files:
        text = file.read_text(encoding="utf-8")
        definitions = {m[1].strip().lower(): m[2] for m in REFERENCE_DEFINITION.finditer(text)}

        for match in INLINE_IMAGE.finditer(text):
            add(match[1], file, text, match.start())
        for match in HTML_IMAGE.finditer(text):
            add(match[2], file, text, match.start())
        for match in REFERENCE_IMAGE.finditer(text):
            label = (match[2] or match[1]).strip().lower()  # `![alt][]` uses the alt text as label
            if label in definitions:
                add(definitions[label], file, text, match.start())

    # Report in document order, whatever pattern found each image.
    for locations in images.values():
        locations.sort(key=lambda loc: (loc.file, loc.line))
    return dict(sorted(images.items(), key=lambda item: (item[1][0].file, item[1][0].line)))


def inspect(url: str, status: int, content_type: str, body: bytes) -> list[str]:
    """Return what is wrong with a downloaded image (empty list if it is fine)."""
    if not 200 <= status < 300:
        return [f"HTTP {status}"]
    if not content_type.lower().startswith("image/"):
        return [f"not an image (Content-Type: {content_type or 'missing'})"]
    if "svg" not in content_type.lower():
        return []

    svg = body.decode("utf-8", errors="replace")
    visible_text = " ".join(re.findall(r">([^<]+)<", svg)).lower()
    host = urlparse(url).hostname
    markers = ERROR_MARKERS[None] + ERROR_MARKERS.get(host, ())
    problems = [f"shows '{marker}'" for marker in markers if marker in visible_text]

    # skillicons.dev silently drops unknown icon names: compare requested vs rendered.
    if host == "skillicons.dev":
        requested = [i for i in parse_qs(urlparse(url).query).get("i", [""])[0].split(",") if i]
        rendered = max(svg.count("<svg") - 1, 0)  # one outer <svg> wraps one <svg> per icon
        if rendered != len(requested):
            problems.append(f"renders {rendered} of {len(requested)} requested icons")

    return problems


def check(url: str) -> list[str]:
    try:
        response = HTTP.request("GET", url)
    except urllib3.exceptions.HTTPError as error:  # includes MaxRetryError, timeouts, protocol errors
        return [f"request failed: {error}"]
    return inspect(url, response.status, response.headers.get("Content-Type", ""), response.data)


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.md") if not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def main(argv: list[str]) -> int:
    files = [Path(f) for f in argv] or markdown_files(Path("."))
    images = find_images(files)
    if not images:
        print("::error::No remote images found: is the image discovery broken?")
        return 1

    failures = 0
    for url, locations in images.items():
        problems = check(url)
        print(f"[{'FAIL' if problems else 'ok'}] {url}")
        for problem in problems:
            failures += 1
            where = locations[0]
            print(f"::error file={where.file},line={where.line}::{problem}: {url}")

    print(f"\n{len(images)} images checked, {failures} problem(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
