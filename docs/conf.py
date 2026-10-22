"""Sphinx configuration for GestaltDB documentation."""

from __future__ import annotations

from importlib.metadata import version as package_version
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

project = "GestaltDB"
author = "Mylonas Charilaos"
copyright = "2026, Mylonas Charilaos"
release = package_version("gestaltdb")
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.doctest",
    "sphinx.ext.intersphinx",
    "sphinx.ext.mathjax",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
]

templates_path = ["_templates"]
myst_enable_extensions = ["dollarmath"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_title = "GestaltDB"
html_static_path = ["_static"]

autodoc_typehints = "description"
autosummary_generate = True
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# Only the tutorial tree is assembled into this PDF; HTML still builds all docs.
if tags.has("tutorial-pdf"):
    sys.path.insert(0, str(ROOT / "docs" / "_ext"))
    extensions.append("tutorial_pdf")
    latex_engine = "xelatex"
    latex_documents = [
        ("onto-tutorial/index", "onto-tutorial.tex", "Graph Databases, Knowledge Graphs, and Reasoning with GestaltDB",
         author, "manual"),
    ]
    latex_elements = {
        "papersize": "a4paper",
        "pointsize": "11pt",
        "classoptions": ",oneside",
        "preamble": r"\setlength{\headheight}{30pt}\addtolength{\topmargin}{-14pt}\fvset{fontsize=\small}",
        "fontpkg": r"\setmainfont{DejaVu Serif}\setsansfont{DejaVu Sans}\setmonofont{DejaVu Sans Mono}",
        "sphinxsetup": "verbatimwithframe=false",
    }
