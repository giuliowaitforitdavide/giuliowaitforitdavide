from pathlib import Path

import pytest

from check_images import Location, find_images, inspect, main

SVG = "image/svg+xml; charset=utf-8"


def svg_with_text(text: str) -> bytes:
    return f'<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="0">{text}</text></svg>'.encode()


def write(tmp_path: Path, content: str) -> Path:
    file = tmp_path / "README.md"
    file.write_text(content, encoding="utf-8")
    return file


# --- find_images -------------------------------------------------------------------------------


def test_finds_every_supported_syntax(tmp_path):
    file = write(
        tmp_path,
        "![md](https://a.test/inline.svg)\n"
        '<img alt="x" src="https://a.test/double.svg" />\n'
        "<img src='https://a.test/single.svg'>\n"
        "<picture>\n"
        '  <source media="(prefers-color-scheme: dark)"\n'
        '    srcset="https://a.test/dark.svg">\n'
        "</picture>\n"
        "![ref][logo]\n"
        "![collapsed][]\n"
        "\n"
        "[logo]: https://a.test/reference.svg\n"
        "[Collapsed]: https://a.test/collapsed.svg\n",
    )

    assert set(find_images([file])) == {
        "https://a.test/inline.svg",
        "https://a.test/double.svg",
        "https://a.test/single.svg",
        "https://a.test/dark.svg",
        "https://a.test/reference.svg",
        "https://a.test/collapsed.svg",
    }


def test_records_file_and_line_of_each_reference(tmp_path):
    file = write(tmp_path, "intro\n\n<img\n  src=\"https://a.test/x.svg\">\n![again](https://a.test/x.svg)\n")

    assert find_images([file])["https://a.test/x.svg"] == [Location(str(file), 3), Location(str(file), 5)]


def test_unescapes_html_entities(tmp_path):
    file = write(tmp_path, '<img src="https://a.test/x?a=1&amp;b=2">')

    assert list(find_images([file])) == ["https://a.test/x?a=1&b=2"]


def test_ignores_links_local_images_and_undefined_references(tmp_path):
    file = write(tmp_path, "[link](https://a.test/page)\n![local](docs/logo.png)\n![missing][nope]\n")

    assert find_images([file]) == {}


# --- inspect -----------------------------------------------------------------------------------


def test_healthy_svg_passes():
    assert inspect("https://img.shields.io/badge/ok-green", 200, SVG, svg_with_text("Python")) == []


def test_raster_images_are_not_parsed():
    assert inspect("https://a.test/x.png", 200, "image/png", b"\x89PNG not found") == []


@pytest.mark.parametrize(
    ("status", "content_type", "expected"),
    [
        (404, SVG, "HTTP 404"),
        (200, "text/html", "not an image (Content-Type: text/html)"),
        (200, "", "not an image (Content-Type: missing)"),
    ],
)
def test_reports_http_and_content_type_problems(status, content_type, expected):
    assert inspect("https://a.test/x.svg", status, content_type, b"") == [expected]


def test_global_markers_apply_to_any_host():
    body = svg_with_text("Something went wrong! file an issue")

    assert inspect("https://my-stats.vercel.app/api", 200, SVG, body) == ["shows 'something went wrong'"]


def test_generic_markers_only_apply_to_shields_hosts():
    body = svg_with_text("404 page not found")

    assert inspect("https://img.shields.io/badge/x-y", 200, SVG, body) == ["shows 'not found'"]
    assert inspect("https://my-stats.vercel.app/api", 200, SVG, body) == []


def test_markers_in_markup_are_ignored():
    body = b'<svg id="invalid"><title>ok</title></svg>'

    assert inspect("https://img.shields.io/badge/x-y", 200, SVG, body) == []


@pytest.mark.parametrize(
    ("icons", "inner_svgs", "expected"),
    [
        ("python,django", 2, []),
        ("python,notanicon", 1, ["renders 1 of 2 requested icons"]),
        ("python", -1, ["renders 0 of 1 requested icons"]),  # empty or malformed response
    ],
)
def test_skillicons_must_render_every_requested_icon(icons, inner_svgs, expected):
    body = ("<svg>" + "<svg></svg>" * max(inner_svgs, 0) + "</svg>").encode() if inner_svgs >= 0 else b""

    assert inspect(f"https://skillicons.dev/icons?i={icons}", 200, SVG, body) == expected


# --- main --------------------------------------------------------------------------------------


def test_main_fails_when_no_image_is_found(tmp_path, capsys):
    assert main([str(write(tmp_path, "no images here"))]) == 1
    assert "No remote images found" in capsys.readouterr().out


def test_main_annotates_failures_with_file_and_line(tmp_path, monkeypatch, capsys):
    file = write(tmp_path, "title\n![broken](https://a.test/x.svg)\n")
    monkeypatch.setattr("check_images.check", lambda url: ["HTTP 500"])

    assert main([str(file)]) == 1
    assert f"::error file={file},line=2::HTTP 500: https://a.test/x.svg" in capsys.readouterr().out
