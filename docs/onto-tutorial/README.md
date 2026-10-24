# Standalone graph and ontology tutorial

Start with [the introduction](index.rst). The chapters, glossary, diagrams, and
notebooks form an independent tutorial; API references link out to the
[GestaltDB library](https://github.com/mylonasc/gestaltdb).

From the repository root:

```sh
./scripts/build_tutorial.sh                 # tutorial HTML only
./scripts/build_tutorial.sh --pdf --examples
```

Open `docs/onto-tutorial/_build/html/index.html`. The PDF is written beside it
as `onto-tutorial.pdf`. Use `--output DIR` to choose a different destination.
The committed reader PDF is [onto-tutorial.pdf](onto-tutorial.pdf).

This directory is its own Sphinx source root and can be built independently
with `sphinx-build -W -b html docs/onto-tutorial OUTPUT`. Rendering does not
require an installed library; executing examples requires the matching checkout
and LMDB for the stored-graph labs. See [maintenance notes](maintainer-notes.rst)
for PDF dependencies and verification.

The main library documentation uses `scripts/build_docs.sh` and does not include
this tutorial.
