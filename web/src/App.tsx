import { useMemo } from "react";
import * as d3 from "d3";
import type { VizPayload } from "./viz-types";

// VIZ-02 scaffold renderer. Replaced by the full React-shell + D3-force
// canvas in VIZ-04; kept deliberately minimal so VIZ-03 can build and test
// the offline artifact pipeline against a real bundle.
export function ScaffoldApp({ payload }: { payload: VizPayload }) {
  const stats = useMemo(() => {
    const groups = d3.group(payload.nodes, (node) => node.group || "(unlabeled)");
    const types = d3.group(payload.edges, (edge) => edge.type || "(untyped)");
    return { groups, types };
  }, [payload]);

  return (
    <main className="gdviz-scaffold">
      <h1>GestaltDB graph</h1>
      <p>
        {payload.nodes.length} nodes, {payload.edges.length} edges.
      </p>
      {payload.truncation.truncated && (
        <p role="alert">
          Truncated: {payload.truncation.nodes_dropped} nodes and{" "}
          {payload.truncation.edges_dropped} edges dropped by caps.
        </p>
      )}
      <section>
        <h2>Node groups</h2>
        <ul>
          {[...stats.groups.entries()].map(([group, nodes]) => (
            <li key={group}>
              {group}: {nodes.length}
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2>Edge types</h2>
        <ul>
          {[...stats.types.entries()].map(([type, edges]) => (
            <li key={type}>
              {type}: {edges.length}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
