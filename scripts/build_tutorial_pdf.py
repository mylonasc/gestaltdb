#!/usr/bin/env python3
"""Compile the tutorial's Sphinx tree and vector figures to a standalone PDF.

Run through build_docs.sh --pdf to provision Python build dependencies. Requires
XeLaTeX plus the LaTeX packages used by Sphinx, but not latexmk or Graphviz.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def validate_reader_document(path: Path) -> None:
    """Keep contributor bookkeeping out of the reader edition."""
    text = path.read_text(encoding="utf-8")
    for page in ("maintainer-notes", "OUTLINE_COVERAGE", "toolkit/index"):
        if f"onto-tutorial/{page}::doc" in text:
            raise RuntimeError(f"Reader PDF unexpectedly includes maintainer page: {page}")


def build(output: Path) -> Path:
    engine = shutil.which("xelatex")
    if engine is None:
        raise RuntimeError("PDF export needs XeLaTeX. Install texlive-xetex, "
                           "texlive-latex-extra, texlive-fonts-recommended and fonts-dejavu-core "
                           "(Debian/Ubuntu), or equivalent TeX packages.")
    from sphinx.cmd.build import build_main

    output = output.resolve()
    work = output / "latex"
    work.mkdir(parents=True, exist_ok=True)
    status = build_main(["-b", "latex", "-W", "-t", "tutorial-pdf",
                         str(ROOT / "docs"), str(work)])
    if status:
        raise RuntimeError(f"Sphinx LaTeX build failed with exit status {status}")
    validate_reader_document(work / "onto-tutorial.tex")
    # Three passes resolve TOC, references, and page numbers without latexmk.
    for attempt in range(3):
        process = subprocess.run([engine, "-interaction=nonstopmode", "-halt-on-error",
                                  "onto-tutorial.tex"], cwd=work, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 timeout=180)
        (work / f"xelatex-pass-{attempt + 1}.log").write_text(process.stdout, encoding="utf-8")
        if process.returncode:
            raise RuntimeError("XeLaTeX failed. Check installed TeX packages and "
                               f"{work / f'xelatex-pass-{attempt + 1}.log'}\n"
                               + process.stdout[-6000:])
    if "Missing character:" in process.stdout:
        raise RuntimeError("The PDF font configuration is missing a character. "
                           f"See {work / 'xelatex-pass-3.log'} before publishing.")
    result = output / "onto-tutorial.pdf"
    compiled = work / "onto-tutorial.pdf"
    if not compiled.is_file() or not compiled.read_bytes().startswith(b"%PDF-"):
        raise RuntimeError(f"XeLaTeX did not produce a valid PDF at {compiled}")
    shutil.copyfile(compiled, result)
    print(f"OK: tutorial PDF built at {result}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="directory for PDF and LaTeX intermediates")
    arguments = parser.parse_args()
    try:
        build(arguments.output)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"{error}\n")
