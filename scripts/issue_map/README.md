# Issue atlas (D3)

Read-only GitHub export → reusable JSON snapshot → **self-contained interactive
HTML**. Requires Python 3.10+ and authenticated `gh` for refreshes. No npm/build
step or Python extras. The finished file opens directly in a browser offline.

```bash
# All open issues + issues actually closed in the last 30 days + roadmap.
python scripts/issue_map/build.py --refresh
# Open artifacts/issue-map/index.html in your browser.

# Re-render an existing snapshot without accessing GitHub.
python scripts/issue_map/build.py

# Wider recent-history window, custom export path.
python scripts/issue_map/build.py --refresh --days 90 --output /tmp/opencode/issue-atlas.html
```

On first rendering, the script downloads pinned **D3 7.9.0** from jsDelivr and
caches it in `artifacts/issue-map/d3.v7.9.0.min.js`. Subsequent rendering is fully
offline with the snapshot and cached script. You can provide a predownloaded
copy with `--d3 PATH`. The export prints its SHA-256 for reproducibility.
D3 is ISC licensed; its distribution retains the upstream copyright notice.

## Reading the graph

- Click nodes or issue-list entries for descriptions, GitHub links, status,
  roadmap delivery, prerequisites, dependents and parent/child context.
- **Solid arrows: prerequisite → dependent.** Data comes from native GitHub
  blocked-by relationships and the checked-in reasoning roadmap register.
- Dashed links are roadmap/native parent relationships. Optional dotted edges
  are body/comment issue mentions, **not dependencies**.
- Open nodes are teal; closed nodes are translucent violet. Roadmap/epics are
  larger gold nodes. Selecting an issue traces upstream amber and downstream
  blue, fading unrelated work. Toggle full-chain versus immediate neighbors.
- Search title/number/description/roadmap ID, filter phases and open-only
  dependency context, show/hide closed issues and context edges.
- Pan, zoom, drag, pause/resume, fit, clear selection, export SVG. Keyboard:
  tab through the issue list or nodes; Enter selects; Escape clears.

## Data boundaries

The snapshot records its fetch time and window. GitHub issue status is live at
fetch time; roadmap delivery comes from `planning/reasoning-engine/roadmap.json`
and can intentionally differ from issue status (integrated prototype vs broader
open issue). Native dependencies/sub-issues outside the selected window are
retained as context nodes, but not recursively expanded. Pull requests are not
issue nodes. Markdown descriptions are shortened, not AI-generated claims.
Comments supply context mentions only: ambiguous prose never becomes a blocker.

Refresh fails if a GitHub endpoint cannot be read rather than silently dropping
dependencies. It writes no GitHub data. Expect roughly two calls per issue plus
comments; `--workers` (default 6, max 12) controls concurrency. Keep generated
snapshots private if they contain private repository/comment data.

## Tests

```bash
uv run pytest tests/test_issue_map.py -q

# Optional real-browser checks (including offline interaction and SVG export).
uv run --with playwright playwright install chromium
uv run --with playwright python scripts/issue_map/smoke.py artifacts/issue-map/index.html
```

The generated HTML embeds all scripts/data; export the HTML to share a frozen
snapshot. Refreshing GitHub is a separate explicit step, never a browser action.
