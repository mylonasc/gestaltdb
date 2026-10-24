"""Independent Sphinx project for the graph and ontology tutorial.

No library documentation configuration or installed GestaltDB is needed to
render the tutorial. Running its examples requires the matching library checkout.
"""

from pathlib import Path
import sys

project = "Graphs, Ontology, and Reasoning with GestaltDB"
author = "Mylonas Charilaos"
copyright = "2026, Mylonas Charilaos"
root_doc = "index"
extensions = ["sphinx.ext.mathjax", "myst_parser"]
myst_enable_extensions = ["dollarmath"]
exclude_patterns = ["_build", "README.md", "Thumbs.db", ".DS_Store"]
html_theme = "sphinx_rtd_theme"
html_title = project
html_theme_options = {"navigation_depth": 3}

if tags.has("tutorial-pdf"):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_ext"))
    extensions.append("tutorial_pdf")
    latex_engine = "xelatex"
    latex_documents = [
        ("index", "onto-tutorial.tex", project, author, "manual"),
    ]
    latex_elements = {
        "papersize": "a4paper",
        "pointsize": "11pt",
        "classoptions": ",oneside",
        "preamble": r"\setlength{\headheight}{30pt}\addtolength{\topmargin}{-14pt}\fvset{fontsize=\small}",
        "fontpkg": r"\setmainfont{DejaVu Serif}\setsansfont{DejaVu Sans}\setmonofont{DejaVu Sans Mono}",
        "sphinxsetup": "verbatimwithframe=false",
    }
