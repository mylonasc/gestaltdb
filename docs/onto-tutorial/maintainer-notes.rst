Documentation maintenance notes
===============================

This page is for contributors maintaining the tutorial, not part of the reader
PDF. Learning goals and the recommended reading order are in :doc:`index`.

Source and review boundary
--------------------------

Tutorial examples are based on ontology integration commit ``0dba88f``. A branch
refresh on 2026-10-22 found remote ``main`` at ``25daaf9`` and remote
``feature/onto-dev-epic-2`` at ``79f6605``. Integration changes were not merged
into the tutorial branch. Keep examples bound to the code actually tested here;
an open issue is not evidence that an implementation is absent.

Review the integration branch, ``main``, active issues, and draft PRs before
updating capability claims. Current follow-through is tracked in
`#200 <https://github.com/mylonasc/gestaltdb/issues/200>`_ and tutorial coverage in
`#273 <https://github.com/mylonasc/gestaltdb/issues/273>`_.
Decoded native evidence helpers exist; issue #194 retains scale-measurement work.
Head-bound support discovery and immutable dependency lookups (#252/#253) are
implemented in this checkout. Other active integration proposals (#248–#251,
#254–#256) must not be described as accepted runtime APIs solely because a draft
PR exists. Keep this bookkeeping here rather than in reader explanations.

Build and validation
---------------------

From the repository root::

   ./scripts/build_tutorial.sh
   ./scripts/build_tutorial.sh --pdf --examples
   uv run --extra lmdb pytest tests/test_ontology_tutorial.py -q

HTML starts at ``docs/onto-tutorial/_build/html/index.html``. The optional PDF is
``docs/onto-tutorial/_build/html/onto-tutorial.pdf``; ``--output DIR`` changes that
prefix. LaTeX intermediates and diagnostic logs live under ``latex``
in the output directory. A Sphinx build renders text but does not execute cells;
``--examples`` runs only the tutorial chapter, notebook, and illustration tests.

The tutorial is an independent Sphinx project with its own ``conf.py`` and
local PDF extension. It can be rendered directly with
``sphinx-build -W -b html docs/onto-tutorial OUTPUT``; rendering needs no installed
GestaltDB and does not import the main documentation configuration. Library
references use external GitHub links, while chapter links stay inside this site.
The main library build (``scripts/build_docs.sh``) excludes the tutorial entirely.
The Pages workflow builds both projects separately and publishes the tutorial at
`/onto-tutorial/ <https://mylonasc.github.io/gestaltdb/onto-tutorial/>`_, alongside
the library site. It serves the committed PDF without requiring TeX in CI.
Use a fresh output directory when switching from the old combined build:
Sphinx does not remove stale HTML from previous builds.

PDF export requires XeLaTeX, Sphinx's standard LaTeX packages, DejaVu fonts, and
Cairo for vector SVG conversion. For example, Debian/Ubuntu packages include
``texlive-xetex``, ``texlive-latex-extra``, ``texlive-fonts-recommended``, and
``fonts-dejavu-core``. ``uv`` provisions Python build dependencies. HTML-only
builds need neither TeX nor Graphviz. Figure regeneration additionally needs
Graphviz's ``dot``; see :doc:`toolkit/index`.

The PDF build should contain chapters, glossary, and lab guidance, **not** this
page, build commands, the toolkit reference, or the outline checklist. Tests
check the assembled LaTeX reader document for that separation. After validating
the PDF and inspecting representative pages, refresh the committed reader copy::

   cp docs/onto-tutorial/_build/html/onto-tutorial.pdf docs/onto-tutorial/onto-tutorial.pdf

Do not add the LaTeX intermediates or the full HTML build to Git.
