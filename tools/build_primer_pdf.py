#!/usr/bin/env python3
"""Render docs/primer.md to docs/primer.pdf.

Markdown is the source of truth. The PDF is a build artifact, regenerated rather than
hand-edited, so the document mentors read is always the document the repo reviewed.

Usage:
    pip install -r tools/requirements.txt
    python3 tools/build_primer_pdf.py [--source docs/primer.md] [--out docs/primer.pdf]

Chromium comes from Playwright's browser directory (PLAYWRIGHT_BROWSERS_PATH, default
~/.cache/ms-playwright) or from PATH. Fails loudly if it cannot be found: a silently
skipped PDF build is worse than a broken one.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

REPO_ROOT = Path(__file__).resolve().parent.parent

CSS = """
@page { size: Letter; margin: 20mm 18mm 18mm 18mm; }
:root {
  --ink: #1a1a1a;
  --muted: #5a5a5a;
  --rule: #d8d8d8;
  --accent: #7a3e2f;
  --band: #f6f4f1;
}
* { box-sizing: border-box; }
body {
  font-family: "Charter", "Bitstream Charter", "Georgia", "Times New Roman", serif;
  font-size: 10.5pt;
  line-height: 1.52;
  color: var(--ink);
  margin: 0;
  -webkit-font-smoothing: antialiased;
}
h1 {
  font-size: 25pt;
  line-height: 1.12;
  margin: 0 0 2pt 0;
  letter-spacing: -0.4pt;
}
h2 {
  font-size: 13pt;
  font-weight: 400;
  font-style: italic;
  color: var(--muted);
  margin: 0 0 14pt 0;
}
h3 {
  font-size: 13pt;
  margin: 22pt 0 7pt 0;
  padding-bottom: 3pt;
  border-bottom: 1px solid var(--rule);
  page-break-after: avoid;
  break-after: avoid;
}
h3 + p, h3 + ul, h3 + table, h3 + blockquote { page-break-before: avoid; }
p { margin: 0 0 8pt 0; orphans: 3; widows: 3; }
strong { font-weight: 700; }
em { font-style: italic; }
a { color: var(--accent); text-decoration: none; word-break: break-word; }
ul, ol { margin: 0 0 9pt 0; padding-left: 17pt; }
li { margin-bottom: 3.5pt; }
hr {
  border: 0;
  border-top: 1px solid var(--rule);
  margin: 16pt 0;
}
blockquote {
  margin: 10pt 0;
  padding: 8pt 12pt;
  background: var(--band);
  border-left: 2.5pt solid var(--accent);
  font-style: italic;
}
blockquote p { margin: 0; }
code, pre {
  font-family: "DejaVu Sans Mono", "Menlo", "Consolas", monospace;
}
code { font-size: 0.86em; background: var(--band); padding: 0.5pt 2.5pt; border-radius: 2px; }
pre {
  font-size: 7.6pt;
  line-height: 1.35;
  background: var(--band);
  border: 1px solid var(--rule);
  border-radius: 3px;
  padding: 9pt 11pt;
  overflow: visible;
  white-space: pre;
  page-break-inside: avoid;
  break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: inherit; }
table {
  width: 100%;
  border-collapse: collapse;
  margin: 9pt 0 12pt 0;
  font-size: 9pt;
  page-break-inside: auto;
}
thead { display: table-header-group; }
tr { page-break-inside: avoid; break-inside: avoid; }
th {
  text-align: left;
  font-weight: 700;
  border-bottom: 1.2pt solid var(--ink);
  padding: 4.5pt 7pt 4.5pt 0;
  vertical-align: bottom;
}
td {
  border-bottom: 1px solid var(--rule);
  padding: 4.5pt 7pt 4.5pt 0;
  vertical-align: top;
}
th:last-child, td:last-child { padding-right: 0; }
table td:first-child { white-space: nowrap; }
table.wrapfirst td:first-child { white-space: normal; }
.masthead {
  border-bottom: 2.5pt solid var(--ink);
  padding-bottom: 9pt;
  margin-bottom: 4pt;
}
@media print { a { color: var(--accent); } }
"""

HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def find_chromium() -> str:
    """Locate a Chromium binary, or exit with an explicit error."""
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found

    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
        Path.home() / ".cache" / "ms-playwright"
    )
    patterns = (
        f"{browsers_path}/chromium-*/chrome-linux/chrome",
        f"{browsers_path}/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        f"{browsers_path}/chromium_headless_shell-*/chrome-linux/headless_shell",
        f"{browsers_path}/chromium",
    )
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]

    sys.exit(
        "build_primer_pdf: no Chromium found.\n"
        f"  Looked on PATH and under {browsers_path}\n"
        "  Install one, or set PLAYWRIGHT_BROWSERS_PATH to a directory containing it."
    )


def render_html(source: Path) -> str:
    text = source.read_text(encoding="utf-8")
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
        output_format="html5",
    )
    # The title block is the first h1 plus its subtitle; rule it off as a masthead.
    if "<h2>" in body:
        head, _, rest = body.partition("</h2>")
        body = f'<div class="masthead">{head}</h2></div>{rest}'
    title = "BehaviorChangeBench Primer"
    return HTML_SHELL.format(title=title, css=CSS, body=body)


def to_pdf(html: str, out_path: Path, chromium: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "primer.html"
        html_path.write_text(html, encoding="utf-8")
        cmd = [
            chromium,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--user-data-dir={tmp}/profile",
            f"--print-to-pdf={out_path}",
            html_path.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not out_path.exists():
            sys.exit(
                "build_primer_pdf: Chromium failed to produce a PDF.\n"
                f"  exit={result.returncode}\n"
                f"  stderr={result.stderr.strip()[:2000]}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(REPO_ROOT / "docs" / "primer.md"))
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "primer.pdf"))
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_file():
        sys.exit(f"build_primer_pdf: source not found: {source}")

    out_path = Path(args.out).resolve()
    chromium = find_chromium()
    to_pdf(render_html(source), out_path, chromium)
    size_kb = out_path.stat().st_size / 1024
    print(f"build_primer_pdf: wrote {out_path} ({size_kb:.0f} KB) using {chromium}")


if __name__ == "__main__":
    main()
