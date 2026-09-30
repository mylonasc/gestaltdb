import type { ViewState } from "./session";
import type { VizPayload } from "./viz-types";

export function ExplorationControls({ payload, filters, onChange }: {
  payload: VizPayload; filters: ViewState["filters"]; onChange: (filters: ViewState["filters"]) => void;
}) {
  const labels = [...new Set(payload.nodes.flatMap(node => node.labels))].sort();
  return <details className="gdviz-controls">
    <summary>Search and neighborhood</summary>
    <div className="gdviz-toolbar" role="group" aria-label="Exploration controls">
      <label>Search nodes<input type="search" placeholder="ID, label, or property…" value={filters.query}
        onChange={event => onChange({ ...filters, query: event.target.value })} aria-label="Search nodes" /></label>
      <label><input type="checkbox" checked={filters.searchProperties} onChange={event => onChange({ ...filters, searchProperties: event.target.checked })} />Search properties</label>
      <label>Native label<select aria-label="Native label" value={filters.requiredLabel} onChange={event => onChange({ ...filters, requiredLabel: event.target.value })}>
        <option value="">All labels</option>{labels.map(label => <option key={label} value={label}>{label}</option>)}
      </select></label>
      <label>Focus direction<select aria-label="Focus direction" value={filters.focusDirection} onChange={event => onChange({ ...filters, focusDirection: event.target.value as ViewState["filters"]["focusDirection"] })}>
        <option value="both">Both</option><option value="out">Outgoing</option><option value="in">Incoming</option>
      </select></label>
      <label>Focus hops<select aria-label="Focus hops" value={filters.focusHops} onChange={event => onChange({ ...filters, focusHops: Number(event.target.value) })}>
        {[1, 2, 3, 4, 5].map(hops => <option key={hops} value={hops}>{hops}</option>)}
      </select></label>
      <label><input type="checkbox" checked={filters.highlightOn} onChange={event => onChange({ ...filters, highlightOn: event.target.checked })} />Highlight query matches</label>
      {filters.focusNodeId !== null && <button onClick={() => onChange({ ...filters, focusNodeId: null })}>Show full graph</button>}
    </div>
    <p className="gdviz-muted">Neighborhood traversal follows enabled relationship types and node filters.</p>
  </details>;
}
