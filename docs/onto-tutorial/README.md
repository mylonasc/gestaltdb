# Standalone graph and ontology tutorial

Start with [the introduction](index.rst). The chapters, glossary, diagrams, and
notebooks form an independent tutorial; API references link out to the
[GestaltDB library](https://github.com/mylonasc/gestaltdb).

The Pages workflow publishes this independent site at
<https://mylonasc.github.io/gestaltdb/onto-tutorial/> after merging to `main`.

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
require an installed library; executing examples requires the matching checkout.
Basic graph examples support LMDB, LevelDB, or RocksDB/PyRex; the pinned
current-graph labs require LMDB. See [backend choices](backends.rst) and
[maintenance notes](maintainer-notes.rst) for dependencies and verification.
The HTML build includes copy buttons on every code listing.

The main library documentation uses `scripts/build_docs.sh` and does not include
this tutorial.
