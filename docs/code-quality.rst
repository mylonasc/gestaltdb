Code quality and module boundaries
==================================

Run the lightweight gates before submitting library changes::

    uv run --extra lint ruff check .
    uv run python scripts/check_code_quality.py
    uv run pytest tests/test_code_quality.py -q

The ``Lint`` workflow runs these checks for library/configuration/gate changes
on pull requests and pushes to main. It does not install database backends or
run a Python-version matrix. Prose-only edits do not select it.

Ruff scope and rollout
----------------------

The pinned Ruff version and rule selection live in ``pyproject.toml``. The
initial scope is all Python under ``src/``, the module-size checker, and its
regression tests. Other tests, benchmark scripts, and developer scripts are not
yet covered by Ruff. The initial rules target invalid statements, undefined
names, invalid exports, and related correctness errors (``E4``, ``E7``, ``E9``,
``F63``, ``F7``, ``F82``). This is not a formatter, type checker, security audit,
or complete complexity analysis. Import sorting and unused-import cleanup need
a separate pass that preserves compatibility re-exports.

Module-size budget
------------------

New Python library modules may contain at most **800 physical lines**, including
blank lines, comments, and docstrings. This is a coarse maintainability ceiling,
not a target: prefer smaller cohesive modules and keep executable examples.
Tests, benchmarks, and non-Python assets are outside this particular size gate.

Eight existing oversized modules are recorded with exact budgets in
``planning/code-quality/module-size-baseline.json``. They cannot grow without
failing the gate. When an extraction makes one smaller, the gate requires lowering
its budget in the same change; once it is at or below 800 lines, remove the
exception. Deleted/renamed modules must remove stale entries, and a newly created
module is subject to the default limit.

Do not raise a budget, add an exception, delete examples, or compress code just
to silence the check. Baseline edits are reviewable policy changes, not an
automatic authorization to waive the limit. This checked-in ratchet does not
make exceptions tamper-proof; reviewers must check policy changes.

Refactoring direction
---------------------

``graphdb.py`` currently has 7,113 lines. This gate prevents growth; it does not
claim the existing file has been refactored. The staged plan is recorded in
``planning/code-quality/graphdb-refactoring.md``. Extract cohesive implementation
units while retaining ``from gestaltdb.graphdb import GraphDB, Node, Edge`` and
the existing transaction, writer-lock, snapshot, persistence, and indexing
semantics. Keep the public class as a small integration facade rather than
moving the same monolith into a differently named file.

See :doc:`architecture` for current component boundaries and
:doc:`api-workflows` for executable workflows that refactors must preserve.
