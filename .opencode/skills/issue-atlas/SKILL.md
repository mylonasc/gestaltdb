---
name: Issue Atlas
description: Build or refresh an interactive offline D3 visualization of any GitHub repository's open and recently closed issues, dependencies, parent context and optional roadmap. Use for issue maps, dependency dashboards, roadmap graphs and project planning visualizations.
compatibility: Python 3.10+, GitHub CLI authenticated for live refresh; no repository package, npm or Python runtime dependencies. Optional Playwright for browser tests.
---

# Issue Atlas

This directory is a **self-contained portable skill**. All paths below are
relative to this skill's base directory, not the user's repository. Do not
import project code or assume any repository's issue numbers, file layout,
roadmap format, or working directory.

## Workflow

1. Identify the target `owner/repo`, output location and recent-closed window
   (default 30 days). All open issues are included regardless of age. Use the
   current repository if implied; the script can infer it via read-only
   `gh repo view`. Ask only if the intended repository is ambiguous.
2. Read `references/configuration.md` for CLI/config options and roadmap schema.
   A roadmap overlay is **optional**. Without it, native GitHub blocked-by and
   sub-issue relationships still work. Never invent blockers from prose.
3. Use the **absolute skill-base path** when invoking its script. For example,
   replacing `<skill-base>` with the base directory supplied when this skill
   loads:

   ```bash
   python <skill-base>/scripts/build.py --refresh --repo owner/repo \
     --days 30 --output /chosen/directory/index.html
   ```

   Or use `--config /path/to/atlas.json`. CLI flags override config values.
   With no `--refresh`, render the existing JSON snapshot without querying GitHub.
   Snapshot/cache/output defaults are relative to the calling working directory;
   config-specified paths are relative to the config file. Scripts work even
   when the skill was copied to a global skills directory.
4. Refresh uses **GET-only** GitHub requests. Check `gh auth status` if needed;
   never ask the user to paste a token or write credentials into config. The
   initial D3 download is version-pinned and checksum-verified; later renders
   use the cached copy. Finished HTML needs no server or network.
5. Verify the output. Offline unit tests:

   ```bash
   python -m pytest <skill-base>/tests -q
   ```

   Optional real-browser tests:

   ```bash
   uv run --with playwright playwright install chromium
   uv run --with playwright python <skill-base>/scripts/smoke.py /path/to/index.html
   ```

6. Return the HTML path, snapshot timestamp, issue/edge counts, and refresh
   command. Explain the color key: teal open; translucent violet closed;
   gold roadmap/epic; amber prerequisite; blue dependent. Click to trace,
   use Focus chain to zoom, search/filter, or export SVG.

## Evidence and safety boundaries

- Solid arrows are **prerequisite → dependent** from native blocked-by links
  or the explicitly supplied roadmap overlay. Dashed arrows are parent → child.
  Dotted optional context links are issue-body/comment mentions, not blockers.
- GitHub status and separately supplied roadmap delivery are not interchangeable.
  A merged milestone may leave the broader issue open. Report both honestly.
- Issues closed within the window, pinned roadmap issues and native relationship
  endpoints outside it are included; external context is not expanded recursively.
  Pull requests are not issue nodes. This is a snapshot, not a live subscription.
- Exported HTML and JSON may contain private issue/comment information. Keep
  output private unless the user authorizes sharing. Do not commit generated
  artifacts, publish them, change GitHub data or alter global configuration just
  to produce a visualization.
- Preserve D3's ISC license (`scripts/D3-LICENSE.txt`) in distributed exports.

## Reuse / installation

Copy **this entire `issue-atlas` directory**, including scripts, template,
license, references and tests, into another repository's `.opencode/skills/`,
or into `~/.config/opencode/skills/` for global discovery. No external project
files need copying. Do not overwrite a pre-existing installation without
checking it for user changes. Restart/reload OpenCode if its advertised skill
list has not refreshed, then invoke `@issue-atlas`.
