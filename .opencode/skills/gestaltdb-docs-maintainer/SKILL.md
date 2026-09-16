---
name: gestaltdb-docs-maintainer
description: Use when creating or maintaining GestaltDB agent-facing documentation, especially AGENTS.md, EXAMPLES.md, modular reference guides, and src/gestaltdb/__init__.py package docs.
---

# GestaltDB Docs Maintainer

Use this skill when creating or maintaining documentation for GestaltDB developers and AI agents. It governs `AGENTS.md`, `EXAMPLES.md`, modular topic guides in `references/`, the shipped library-user skill under `src/gestaltdb/agent_skill/`, the packaged retrieval CLI `src/gestaltdb/agent_docs.py`, the package docstring in `src/gestaltdb/__init__.py`, and consistency with user-facing Sphinx docs in `docs/`.

---

## 1. Fast-Path: Documentation Tools & Verification

This skill is equipped with automated Python tools to eliminate context bloat, manual verification, and documentation drift.

### Available Scripts
All scripts are located in `.opencode/skills/gestaltdb-docs-maintainer/scripts/`:

1. **`doc_tool.py` (CLI for Retrieval & Testing):**
   - List topics & triggers:
     ```bash
     uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py list
     ```
   - Retrieve a targeted documentation module (avoids loading the entire codebase):
     ```bash
     uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py get <topic>
     ```
     Use `--examples` to extract only runnable code blocks, or `--rules` for conceptual rules only.
    - Search across docs without dumping files:
      ```bash
      uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py search "<keyword>"
      ```
    - Query shipped library-user docs exactly as installed users and agents can:
      ```bash
      uv run python -m gestaltdb.agent_docs list
      uv run python -m gestaltdb.agent_docs get cypher --examples
      uv run python -m gestaltdb.agent_docs search "rebuild_deferred_indexes"
      ```
   - Execute all documentation examples in isolated tests:
     ```bash
     uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py test-examples
     ```

2. **`check_docs.py` (Automated Drift & API Checker):**
   - Run complete automated consistency check:
     ```bash
     uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/check_docs.py
     ```
     Automatically verifies:
     - Root exports in `gestaltdb.__all__` vs imported symbols.
     - Backend registry (`lmdb`, `leveldb`, `pyrex`) and serializer registry (`pickle`, `json`, `messagepack`, `protobuf`).
     - AST syntax and import validity of all Python code blocks across all markdown docs.
     - Public method signatures on `GraphDB`, `Node`, `Edge`, `SamplerEngine`, and `SamplerSnapshot`.
     - Existence of referenced Sphinx `.rst` documentation files.

---

## 2. Lazy-Loading Protocol (Prevent Context Bloat)

**Do NOT dump the entire repository and all Sphinx docs into context at once.**

Follow this lazy-loading workflow:
1. Identify the topic you need to read or update.
2. Read **only** the corresponding modular sub-file under `.opencode/skills/gestaltdb-docs-maintainer/references/`:
   - `references/backends.md`: `LevelDBStore`, `LMDBStore`, `PyRexStore`, transactional vs non-transactional mode, manifests (`create`/`open`).
   - `references/indexing.md`: Native labels, relationship types, property indexes, exact/range lookups, index maintenance.
   - `references/ingestion.md`: Arrow & Polars columnar ingest, `ColumnarIngestionMode`, `IndexMaintenanceMode`.
   - `references/cypher.md`: Supported Cypher read/write subset, syntax rules, limitations, and `QueryResult`.
    - `references/sampling.md`: Graph traversal sampling vs array-native `SamplerSnapshot`/`SamplerEngine` for GNNs.
    - `src/gestaltdb/agent_skill/references/*.md`: Short shipped, user-focused retrieval docs for installed-library coding agents.
3. Inspect relevant source files only when deep implementation details or test cases are needed:
   - Backends: `src/gestaltdb/kvstores.py`
   - Cypher: `src/gestaltdb/query_engine/cypher/` (legacy import shims remain in `src/gestaltdb/cypher*.py`)
   - Ingestion: `src/gestaltdb/ingestion.py`
   - Sampling: `src/gestaltdb/sampling/`
   - Core API: `src/gestaltdb/graphdb.py`

---

## 3. Documentation Roles & Structure

### Root `AGENTS.md`
- **Role:** High-level concise index and architecture map for coding agents.
- Keep it factual and brief (~120 lines).
- Distinguish submodule imports (e.g. `from gestaltdb.graphdb import GraphDB`) from root exports (`from gestaltdb import ...`).
- Refer agents to `references/*.md` for in-depth guidance on specific subsystems.

### Root `EXAMPLES.md`
- **Role:** Curated, copy-pasteable, end-to-end usage patterns.
- Prefer self-contained snippets using `TemporaryDirectory`.
- Always close graph handles in `try/finally` blocks.
- Explicitly import classes from their submodules.

### Packaged `src/gestaltdb/agent_skill/SKILL.md`
- **Role:** Shipped skill for coding assistants that consume GestaltDB as an installed library.
- Keep it user-focused: public imports, graph lifecycle, supported Cypher, explicit indexes, ingestion modes, and sampling ID rules.
- Do not include repository-development instructions or internal parser/backend modification guidance.
- Point agents to `python -m gestaltdb.agent_docs` for targeted retrieval.

### Packaged `src/gestaltdb/agent_docs.py`
- **Role:** Zero-extra-dependency retrieval CLI available after install.
- Must support `list`, `get <topic>`, `get <topic> --examples`, `get <topic> --rules`, and `search <regex>`.
- Must read packaged resources via `importlib.resources`, not filesystem-relative repository paths.

### Modular `references/*.md`
- **Role:** Deep-dive subsystem reference guides.
- Contain detailed conceptual rules, caveats, edge cases, and dedicated runnable examples.
- When an API or subsystem changes, update its specific modular reference file first.

### Package Docstring (`src/gestaltdb/__init__.py`)
- **Role:** Guide developers at import time.
- Keep minimal: high-level summary, import guidance, one minimal create/query snippet, pointers to `python -m gestaltdb.agent_docs`, `EXAMPLES.md`, and `docs/`.
- Do not modify `__all__` unless intentionally changing the public API export surface.

---

## 4. Verification Workflow

Before completing any documentation or code change:
1. Run `uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/check_docs.py` to ensure zero drift.
2. Run `uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py test-examples` to verify all documentation code snippets execute cleanly.
3. Run focused pytest tests: `uv run pytest tests/test_graphdb_interfaces.py tests/test_cypher.py tests/test_sampling.py -q`.
4. If Sphinx docs were modified and Sphinx is installed: `uv run sphinx-build -b html docs docs/_build/html`.

After modifying this skill, remind the user to restart opencode so the running session loads the updated skill definition.
