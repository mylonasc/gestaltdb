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

   ./scripts/build_docs.sh
   ./scripts/build_docs.sh --pdf --examples
   uv run --extra lmdb pytest tests/test_ontology_tutorial.py -q

HTML starts at ``docs/_build/html/onto-tutorial/index.html``. The optional PDF is
``docs/_build/html/onto-tutorial/onto-tutorial.pdf``; ``--output DIR`` changes that
prefix. LaTeX intermediates and diagnostic logs live under ``onto-tutorial/latex``
in the output directory. A Sphinx build renders text but does not execute cells;
``--examples`` also runs the tutorial regressions and modular documentation labs.

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

   cp docs/_build/html/onto-tutorial/onto-tutorial.pdf docs/onto-tutorial/onto-tutorial.pdf

Do not add the LaTeX intermediates or the full HTML build to Git.
