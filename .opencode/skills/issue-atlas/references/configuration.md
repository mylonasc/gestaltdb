# Configuration and portability

The skill supports any GitHub `owner/repo` with no roadmap files required.
It imports only the Python standard library and invokes the authenticated `gh`
CLI. Neither scripts nor templates load host-repository code.

## CLI and JSON

```bash
python /absolute/skill-base/scripts/build.py --refresh --repo owner/repository
python /absolute/skill-base/scripts/build.py --config /path/to/atlas.json --refresh
python /absolute/skill-base/scripts/build.py --snapshot /path/to/snapshot.json --output /path/to/index.html
```

Supported config fields (unknown fields fail rather than being ignored):

| Field / CLI flag | Default | Meaning |
| --- | --- | --- |
| `repo` / `--repo` | infer on refresh | GitHub `owner/repo`; offline rendering uses snapshot identity |
| `title` / `--title` | `owner/repo · Issue atlas` | Dashboard/browser title |
| `days` / `--days` | 30 | Recently closed window; all open issues included |
| `workers` / `--workers` | 6 | Concurrent GET workers, integer 1–12 |
| `comments` / `--no-comments` | true | Fetch comments for optional context mentions; flag disables |
| `snapshot` / `--snapshot` | `artifacts/issue-atlas/snapshot.json` | Reusable source JSON |
| `output` / `--output` | `artifacts/issue-atlas/index.html` | Self-contained HTML |
| `d3` / `--d3` | `artifacts/issue-atlas/d3.v7.9.0.min.js` | Pinned/checksummed cache; downloaded if absent |
| `roadmap` / `--roadmap` | none | Optional generic overlay JSON (below) |

`--refresh` is an action flag, not a persisted config setting. Explicit CLI
options override config fields. Config-specified paths resolve relative to the
config file; defaults and CLI paths resolve relative to the calling directory.
Different output/snapshot/cache/roadmap files are required. Multiple repositories
should use separate configs/output directories. A conflicting explicit `--repo`
on offline rendering fails rather than relabeling another repository's data.

See `config.example.json`. Copy and replace the example repository; its issue
numbers and title are examples, not built-in defaults. Use `--help` for flags.

## Optional roadmap overlay

Use this small repository-neutral schema instead of importing a project's
private planning format. See `roadmap.example.json`:

- `nodes`: list of metadata keyed by positive integer GitHub issue `number`.
  Optional `kind` is `roadmap`, `epic`, `feature`, or `issue`; optional `phase`,
  `roadmap_id`, `description`, `delivery` are strings; `acceptance` is a list
  of strings. GitHub title/state/URL are authoritative and cannot be overridden.
- `links`: list of integer issue `source`/`target`, `kind` (`dependency`, `parent`,
  or `reference`) and optional text `origin`. A dependency always points from
  prerequisite **to dependent**. Parent links point parent **to child**.
- Overlay issue numbers/endpoints are pinned into refresh even if older than the
  recent-closed window. Use only issue numbers belonging to the selected repo.
  A host-repository wrapper may translate its own roadmap into this format,
  but that wrapper must stay outside this portable directory.
- On offline rendering a supplied overlay can replace the saved overlay. It
  cannot retrieve missing issue metadata without a refresh. Unknown fields,
  duplicate node numbers and malformed links fail validation.

## Operational notes

GitHub endpoint failures fail the refresh rather than silently omit dependency
data. Native relationships outside the date window are included as context,
not recursively crawled. Refresh costs about two requests per issue plus
comment requests and pinned metadata; use fewer workers if rate-limited.

D3 7.9.0 is obtained from jsDelivr once, checked against a pinned SHA-256, and
embedded with its ISC license. Copy the cache alongside a snapshot for a fully
offline re-render, providing `--d3 /path/to/cache.js`. The HTML itself never
fetches external resources. Descriptions are shortened markdown, not AI claims;
all issue-controlled text is escaped on embedding and rendered as text nodes.
