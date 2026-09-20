import { useMemo, useState } from "react";
import { GraphCanvas } from "./GraphCanvas";
import { legendGroups, legendTypes } from "./graph";
import { colorFor } from "./colors";
import type { VizPayload } from "./viz-types";

// VIZ-04 shell: toolbar (pause, forces, labels, theme, refit, unpin),
// truncation banner, legend, and the D3 force canvas. Selection, search,
// and filtering arrive in VIZ-05.
export function App({ payload }: { payload: VizPayload }) {
  const [paused, setPaused] = useState(false);
  const [charge, setCharge] = useState(-300);
  const [linkDistance, setLinkDistance] = useState(60);
  const [showLabels, setShowLabels] = useState(true);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [fitSignal, setFitSignal] = useState(0);
  const [unpinSignal, setUnpinSignal] = useState(0);

  const groups = useMemo(() => legendGroups(payload), [payload]);
  const types = useMemo(() => legendTypes(payload), [payload]);

  return (
    <main className="gdviz" data-theme={theme}>
      <header className="gdviz-header">
        <h1>GestaltDB graph</h1>
        <p className="gdviz-counts">
          {payload.nodes.length} nodes, {payload.edges.length} edges
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
        />
        <aside className="gdviz-legend" aria-label="Legend">
          <section>
            <h2>Node groups</h2>
            <ul>
              {groups.map(([group, count]) => (
                <li key={group}>
                  <span
                    className="gdviz-swatch"
                    style={{ backgroundColor: colorFor(group) }}
                    aria-hidden="true"
                  />
                  {group}: {count}
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h2>Edge types</h2>
            <ul>
              {types.map(([etype, count]) => (
                <li key={etype}>
                  <span
                    className="gdviz-swatch"
                    style={{ backgroundColor: colorFor(etype) }}
                    aria-hidden="true"
                  />
                  {etype}: {count}
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </main>
  );
}
