# Security Policy

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
