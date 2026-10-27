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
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_title = "GestaltDB"
html_static_path = ["_static"]

autodoc_typehints = "description"
# Keep dtype aliases readable; expanding numpy.str_ into prose creates an
# unintended reStructuredText reference to "numpy.str" under strict builds.
autodoc_type_aliases = {
    name: f"gestaltdb.sampling._arrays.{name}"
    for name in ("Array", "IntArray", "FloatArray", "BoolArray", "StringArray", "TypedArray")
}
autosummary_generate = True
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
