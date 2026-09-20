import { useEffect, useMemo, useState } from "react";
import { GraphCanvas } from "./GraphCanvas";
import { Inspector, type Selection } from "./Inspector";
import { legendGroups, legendTypes } from "./graph";
import { computeView } from "./view";
import { exportJson, exportPng, exportSvg } from "./export";
import { colorFor } from "./colors";
import type { VizPayload, VizViewOptions } from "./viz-types";

// VIZ-05 shell: search, label/type filters, 1-hop focus, highlight overlay,
// and the node/edge inspector on top of the VIZ-04 canvas.
export function App({ payload, initial = {} }: { payload: VizPayload; initial?: VizViewOptions }) {
  const [paused, setPaused] = useState(false);
  const [charge, setCharge] = useState(initial.charge ?? -300);
  const [linkDistance, setLinkDistance] = useState(initial.linkDistance ?? 60);
  const [showLabels, setShowLabels] = useState(initial.showLabels ?? true);
  const [theme, setTheme] = useState<"light" | "dark">(initial.theme ?? "light");
  const showProperties = initial.showProperties ?? true;
  const [fitSignal, setFitSignal] = useState(0);
  const [unpinSignal, setUnpinSignal] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [query, setQuery] = useState("");
  const [hiddenGroups, setHiddenGroups] = useState<ReadonlySet<string>>(new Set());
  const [hiddenTypes, setHiddenTypes] = useState<ReadonlySet<string>>(new Set());
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [highlightOn, setHighlightOn] = useState(
    () => payload.highlight.nodes.length + payload.highlight.edges.length > 0
  );

  const groups = useMemo(() => legendGroups(payload), [payload]);
  const types = useMemo(() => legendTypes(payload), [payload]);
  const view = useMemo(
    () =>
      computeView(payload, {
        query,
        hiddenGroups,
        hiddenTypes,
        focusNodeId,
        highlightOn,
      }),
    [payload, query, hiddenGroups, hiddenTypes, focusNodeId, highlightOn]
  );

  // Esc clears the selection from anywhere in the artifact.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelection(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const toggleIn = (set: ReadonlySet<string>, key: string): ReadonlySet<string> => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  const canvasElement = (): SVGSVGElement | null => document.querySelector(".gdviz-canvas");

  return (
    <main className="gdviz" data-theme={theme}>
      <header className="gdviz-header">
        <h1>GestaltDB graph</h1>
        <p className="gdviz-counts">
          Showing {view.visibleNodeCount} of {payload.nodes.length} nodes, {view.visibleEdgeCount} of{" "}
          {payload.edges.length} edges
          {focusNodeId !== null && <> (neighborhood of {focusNodeId})</>}
        </p>
        <div className="gdviz-toolbar" role="toolbar" aria-label="Layout controls">
          <button type="button" onClick={() => setPaused((value) => !value)}>
            {paused ? "Resume layout" : "Pause layout"}
          </button>
          <button type="button" onClick={() => setFitSignal((value) => value + 1)}>
            Refit view
          </button>
          <button type="button" onClick={() => setUnpinSignal((value) => value + 1)}>
            Unpin all
          </button>
          <label>
            Charge
            <input
              type="range"
              min={-1000}
              max={-10}
              step={10}
              value={charge}
              onChange={(event) => setCharge(Number(event.target.value))}
            />
          </label>
          <label>
            Link distance
            <input
              type="range"
              min={10}
              max={200}
              step={5}
              value={linkDistance}
              onChange={(event) => setLinkDistance(Number(event.target.value))}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={showLabels}
              onChange={(event) => setShowLabels(event.target.checked)}
            />
            Labels
          </label>
          <button type="button" onClick={() => setTheme((value) => (value === "light" ? "dark" : "light"))}>
            {theme === "light" ? "Dark theme" : "Light theme"}
          </button>
        </div>
        <div className="gdviz-toolbar" role="toolbar" aria-label="Export controls">
          <button
            type="button"
            onClick={() => {
              const svg = canvasElement();
              if (svg) exportSvg(svg);
            }}
          >
            Export SVG
          </button>
          <button
            type="button"
            onClick={() => {
              const svg = canvasElement();
              if (svg) void exportPng(svg);
            }}
          >
            Export PNG
          </button>
          <button type="button" onClick={() => exportJson(payload)}>
            Download JSON
          </button>
        </div>
        <div className="gdviz-toolbar" role="toolbar" aria-label="Search and highlight controls">
          <label>
            Search nodes
            <input
              type="search"
              placeholder="id or label…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Search nodes by id or label"
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={highlightOn}
              onChange={(event) => setHighlightOn(event.target.checked)}
            />
            Highlight query matches
          </label>
          {focusNodeId !== null && (
            <button type="button" onClick={() => setFocusNodeId(null)}>
              Show full graph
            </button>
          )}
        </div>
      </header>

      {payload.truncation.truncated && (
        <p className="gdviz-banner" role="alert">
          Showing a capped sample: {payload.truncation.nodes_dropped} nodes and{" "}
          {payload.truncation.edges_dropped} edges dropped by caps. Narrow the query or sample a
          subgraph for full detail.
        </p>
      )}

      <div className="gdviz-body">
        <GraphCanvas
          payload={payload}
          settings={{ paused, charge, linkDistance, showLabels }}
          fitSignal={fitSignal}
          unpinSignal={unpinSignal}
          selection={selection}
          onSelect={setSelection}
          hiddenNodes={view.hiddenNodes}
          hiddenEdges={view.hiddenEdges}
          dimNodes={view.dimNodes}
          dimEdges={view.dimEdges}
        />
        <aside className="gdviz-legend" aria-label="Legend and inspector">
          {showProperties && (
            <Inspector
              payload={payload}
              selection={selection}
              focusNodeId={focusNodeId}
              onFocus={setFocusNodeId}
              onClear={() => setSelection(null)}
            />
          )}
          <section>
            <h2>Node groups</h2>
            <ul>
              {groups.map(([group, count]) => (
                <li key={group}>
                  <label>
                    <input
                      type="checkbox"
                      checked={!hiddenGroups.has(group)}
                      onChange={() => setHiddenGroups((current) => toggleIn(current, group))}
                      aria-label={`Toggle node group ${group}`}
                    />
                    <span
                      className="gdviz-swatch"
                      style={{ backgroundColor: colorFor(group) }}
                      aria-hidden="true"
                    />
                    {group}: {count}
                  </label>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h2>Edge types</h2>
            <ul>
              {types.map(([etype, count]) => (
                <li key={etype}>
                  <label>
                    <input
                      type="checkbox"
                      checked={!hiddenTypes.has(etype)}
                      onChange={() => setHiddenTypes((current) => toggleIn(current, etype))}
                      aria-label={`Toggle edge type ${etype}`}
                    />
                    <span
                      className="gdviz-swatch"
                      style={{ backgroundColor: colorFor(etype) }}
                      aria-hidden="true"
                    />
                    {etype}: {count}
                  </label>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </main>
  );
}
