Gestalt Graph Onto-Tutorial
===========================

An ontology-reasoning tutorial for advanced coders, grounded in the
``gestalt-core-v1`` fragment on ``feature/onto-dev-epic-2``.

This tutorial is (1) a basic ontology-reasoning walkthrough with gestaltdb,
(2) an introduction to the intermediate representations and foundational
capabilities underneath — and how gestaltdb supports them efficiently — and
(3) an explicit map of gaps and pain points, linked to open issues.

Start with chapters 1–2 and notebook 1 for a first inference. Chapters 3–6
explain the execution and storage boundaries; chapters 7–8 are the performance
and limitations reference. The :doc:`toolkit <toolkit/index>` regenerates the
illustrations, and the :doc:`notebooks <notebooks/index>` provide runnable labs.

.. rubric:: Setup and version boundary

Examples target this source checkout, based on ontology integration commit
``0dba88f``, not the published package on PyPI. On 2026-10-22, remote ``main``
was ``25daaf9`` and remote ``feature/onto-dev-epic-2`` was ``9400930``; the
latter contains additional work not imported into this tutorial branch.
An open roadmap issue is not evidence that its code is absent: chapter 8
separates available capabilities from outstanding acceptance work.

From the repository root, use a supported Python (3.10–3.13) and ``uv``::

   uv sync --extra lmdb
   uv run --with jupyterlab --with ipykernel jupyter lab docs/onto-tutorial/notebooks
   ./scripts/build_docs.sh

The standalone reasoning examples need no embedded backend. The property-graph
labs use the explicit ``lmdb`` extra, temporary databases, and deterministic
assertions. Choose the notebook kernel belonging to this environment; installing
Jupyter elsewhere does not install GestaltDB into that kernel.

Build output starts at ``docs/_build/html/onto-tutorial/index.html``. Building
Sphinx renders prose and figures; it does not execute notebook cells. Run the
tutorial's executable checks with::

   uv run pytest tests/test_ontology_tutorial.py -q

.. rubric:: PDF export

Build HTML plus a standalone tutorial PDF (not the whole API reference)::

   ./scripts/build_docs.sh --pdf

The PDF is written to ``docs/_build/html/onto-tutorial/onto-tutorial.pdf``.
With ``--output DIR``, find it at ``DIR/onto-tutorial/onto-tutorial.pdf``;
LaTeX intermediates and diagnostic logs are alongside it in ``latex/``.
Math is typeset by XeLaTeX, diagrams remain vector artwork, and the document
includes a table of contents and clickable external links. Notebook downloads
remain separate executable labs; notebook outputs are not executed into the PDF.

PDF export needs XeLaTeX and Sphinx's standard LaTeX packages. For example,
on Debian/Ubuntu install ``texlive-xetex``, ``texlive-latex-extra``,
``texlive-fonts-recommended``, and ``fonts-dejavu-core``. The build command
provisions its Python dependencies via ``uv``. HTML-only builds do not require
TeX, CairoSVG, or Graphviz. PDF conversion uses CairoSVG and its system Cairo
library; regenerating graph layouts separately requires Graphviz's ``dot``.

.. toctree::
   :maxdepth: 2
   :caption: Tutorial

   ch01-property-graph-vs-semantic
   ch02-ontology-fragment
   ch03-datalog-irs
   ch04-storage-grounding
   ch05-claims-temporal
   ch06-query-sampling
   ch07-performance
   ch08-gaps
   toolkit/index
   notebooks/index
   OUTLINE_COVERAGE
