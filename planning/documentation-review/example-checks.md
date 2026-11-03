# Documentation example checks

This is contributor guidance, not an application API or a claim that reference
fragments are independently runnable.

## Scope and classifications

`docs/_ext/example_inventory.json` covers Python/Cypher code directives in all
top-level Sphinx RST pages, fenced examples in README/EXAMPLES/AGENTS, and the
packaged agent skill/topics. Python API docstring doctests remain under their
existing tests; shell commands, notebooks and external files are not silently
treated as Python programs. RST options and nested code directives are supported.

Each page declares a digest of its ordered code blocks and explicit groups:
`[runner, [zero-based source-block indices], [required extras]]`. The supported
runners are:

- `execute`: standalone code, fresh namespace and disposable working directory;
  every input/import is local and cleanup is checked.
- `variants`: standalone code owned by the existing backend/serializer matrix,
  without duplicating that matrix or weakening its capability restrictions.
- `compile`: illustrative fragment;
  no execution and no claim of complete setup.
- `continuation`: syntax-only shared-state cells with an explicit page-level
  `setup` description; not executed as independent programs.
- `parse`: direct Cypher fragment; grammar acceptance only, not execution against
  a fabricated application world.
- `pseudocode`: explicitly non-executable material; currently no source blocks
  require this exception.

`requires` explains shared state for fragments. Notebook continuations name their
shared setup. Import maps are illustrative, not minimal applications. Each
canonical RST block gains its reviewed label before tab generation. Markdown
reference sections identify shared graph state in their prose; packaged
standalone recipes execute through the same inventory test.

Inspect current identities with `python docs/_ext/example_inventory.py --list`.
Review changed code and update the declaration deliberately; there is no
automatic status assignment or update command. New, changed, removed or doubly
classified examples fail validation. Digests bind the review, not correctness,
source authentication or a security signature.

## CI matrix

The Documentation and roadmap checks workflow runs for documentation, extension,
inventory, example-test, packaged-resource and runtime changes:

| Lane | Coverage |
| --- | --- |
| Python 3.10 + docs | Base standalone examples; syntax/parser/resource/navigation checks; strict Sphinx using the compatible Sphinx 8 line. Unavailable optional examples skip explicitly. |
| Python 3.13 + docs, backends, serializers, Arrow, Polars, NetworkX | All declared optional examples and backend/codec alternatives; existing agent examples. No unrelated external database/solver clients. |
| Existing Python 3.12 Documentation workflow | Strict published HTML build/deployment. |

Both example lanes run `tests/test_documentation*.py`,
`tests/test_reasoning_learning_paths.py` and `tests/test_agent_docs.py`. Existing
installed-artifact checks retain wheel/sdist resource verification; the new lane
does not substitute an editable checkout for installed-artifact acceptance.

## What is and is not checked

- Every Python source block except explicitly non-executable pseudocode compiles.
- Standalone source programs run independently; backend/codec variants keep the
  existing capability declarations, including native-only and snapshot-only limits.
- Constant Python `.query(...)` strings and direct Cypher blocks parse. The full
  Cypher tutorial also executes its reads/writes/temporal queries through variants.
  Parser success alone is not semantic or result validation for a reference fragment.
- Local Markdown link destinations must exist; source paths and packaged topics
  retain their inventory/resource tests. Strict Sphinx checks RST navigation and
  references. External HTTP links and GitHub fragment slugs are not network-tested.
- Cleanup checks and explicit temporary database lifetimes are useful regression
  checks, not a filesystem sandbox. Only trusted repository code is executed.
- Source identity tests run against a fixed checkout. Finish edits/commits before
  starting the full suite; do not change the checkout during source-attested tests.

These checks do not replace the editorial contract, browser layout/clipboard
checks, or final cross-audience/package acceptance in DOC-10.
