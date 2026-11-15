# Security Policy

<!-- Temporary docs-only required-CI probe; this verification PR is never merged. -->

## Supported Versions

Security fixes are provided for the latest minor release line. Older
versions may receive fixes at the maintainer's discretion.

| Version   | Supported          |
| --------- | ------------------ |
| latest    | :white_check_mark: |
| < latest  | :x:                |

The current release version is declared as `version` in `pyproject.toml`
and published to PyPI as `gestaltdb`.

## Reporting a Vulnerability

**Do not open a public issue for a suspected vulnerability.**

Use [GitHub private vulnerability reporting][adv] for this repository
(Security tab → Report a vulnerability). If private reporting is
unavailable to you, contact the maintainer through the email address
listed on their GitHub profile and include "gestaltdb security" in the
subject line.

[adv]: https://github.com/mylonasc/gestaltdb/security/advisories/new

Please include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce (code, configuration, backend/serializer in use).
- The `gestaltdb` version, Python version, OS, and storage backend.
- Any suggested remediation, if known.

## Response Process

1. **Acknowledgement** — within 7 days of the report.
2. **Assessment** — the maintainer reproduces the issue, determines
   affected versions, and assigns a severity.
3. **Fix and disclosure** — a fix is prepared, released, and documented
   in `CHANGELOG.md`. Reporters are credited unless they ask not to be.
   Where applicable a CVE is requested through GitHub's advisory process.

## Scope

In scope: the `gestaltdb` Python package (`src/gestaltdb/`), its build and
release workflows, and the packaged visualization bundle. Out of scope:
third-party storage engines (LMDB, LevelDB, RocksDB), third-party
serializers' libraries, and the Node.js visualization sources under `web/`
except as they affect the shipped bundle.

## Hardening Notes for Deployments

- Prefer `JSONSerializer` or `MessagePackSerializer` for data exchanged
  across trust boundaries. `PickleSerializer` executes arbitrary code on
  deserialization and must only be used with trusted data.
- Embedded backends (`LMDBStore`, `LevelDBStore`, `PyRexStore`) offer no
  authentication or encryption; restrict filesystem access to the database
  directory.

## Required Pull-Request CI

`Required CI / CI gate` runs on every pull request targeting `main`, without
path filters. It reads the existing workflows' `pull_request` branch/path
filters and waits for all applicable workflows for that PR's current head.
Unrelated path-filtered workflows are not required to start. A selected
workflow must finish successfully; failures, cancellations, unexpected skips,
API errors, and missing runs fail closed. The gate itself has read-only
permissions and uses neither `pull_request_target` nor privileged
`workflow_run` execution.

The `basic protection` ruleset requires the exact check context `CI gate`,
issued by GitHub Actions, with the branch up to date before merging. Human
review approvals are not required for this single-maintainer workflow.

When a failed workflow is rerun successfully, rerun the gate too if it already
failed. Unsupported new glob/event-filter syntax must be implemented and tested
in `scripts/ci_gate.py` before use. GitHub can truncate native path-filter
evaluation for very large PRs; if a selected workflow is consequently absent,
split the PR rather than bypassing the gate. Gate policy tests run on every PR:

```sh
python -m pip install 'PyYAML==6.0.3'
python -m unittest discover -s tests -p test_ci_gate.py -v
```

Repository rules are GitHub settings, not implied by committing a workflow.
After changing the required check, verify both the active ruleset and a live
passing/failing PR. Keep the existing deletion/force-push protections and do not
add an administrator bypass to work around ordinary test failures.
