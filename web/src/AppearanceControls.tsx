import type { ViewerSettings, VizPayload } from "./viz-types";
import { propertyNames } from "./appearance";

interface Props { payload: VizPayload; settings: ViewerSettings; onChange: (settings: ViewerSettings) => void }

export function AppearanceControls({ payload, settings, onChange }: Props) {
  const nodes = propertyNames(payload.nodes), edges = propertyNames(payload.edges);
  const selectors = [
    ["Node label property", "nodeLabelProperty", nodes, "ID"],
    ["Edge label property", "edgeLabelProperty", edges, "Type / ID"],
    ["Node size property", "nodeSizeProperty", nodes, "Fixed"],
    ["Node color property", "nodeColorProperty", nodes, "Node group"],
    ["Edge width property", "edgeWidthProperty", edges, "Fixed"],
    ["Edge color property", "edgeColorProperty", edges, "Relationship type"],
  ] as const;
  return <details className="gdviz-controls">
    <summary>Labels and attribute styling</summary>
    <div className="gdviz-toolbar" role="group" aria-label="Appearance options">
      {selectors.map(([label, key, properties, fallback]) => <label key={key}>{label}
        <select aria-label={label} value={settings[key] ?? ""} onChange={event => onChange({ ...settings, [key]: event.target.value || null })}>
          <option value="">{fallback}</option>
          {[...new Set([...properties, ...(settings[key] ? [settings[key]!] : [])])].map(property => <option key={property} value={property}>{property}</option>)}
        </select>
      </label>)}
      <label>Label length<input type="number" min={1} max={1000} value={settings.labelMaxLength} onChange={event => {
        const length = event.target.valueAsNumber;
        if (Number.isInteger(length) && length >= 1 && length <= 1000) onChange({ ...settings, labelMaxLength: length });
      }} /></label>
    </div>
    <p className="gdviz-muted">Numeric sizes use the full graph's range: node radius 4–24, edge width 0.5–6. Missing or nonnumeric values use fixed defaults. Colors use categorical property values.</p>
  </details>;
}
