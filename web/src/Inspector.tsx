import type { VizPayload } from "./viz-types";
import { groupKey } from "./graph";

export interface Selection {
  kind: "node" | "edge";
  id: string;
}

interface Props {
  payload: VizPayload;
  selection: Selection | null;
  focusNodeId: string | null;
  onFocus: (nodeId: string | null) => void;
  onClear: () => void;
}

function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  const json = JSON.stringify(value);
  return json === undefined ? String(value) : json;
}

export function Inspector({ payload, selection, focusNodeId, onFocus, onClear }: Props) {
  if (selection === null) {
    return (
      <section aria-label="Inspector">
        <h2>Inspector</h2>
        <p className="gdviz-muted">Select a node or edge to inspect its properties.</p>
      </section>
    );
  }

  if (selection.kind === "node") {
    const node = payload.nodes.find((candidate) => candidate.id === selection.id);
    if (!node) return null;
    const entries = Object.entries(node.properties);
    return (
      <section aria-label="Inspector">
        <h2>Node inspector</h2>
        <dl className="gdviz-props">
          <dt>id</dt>
          <dd>{node.id}</dd>
          <dt>labels</dt>
          <dd>{node.labels.length > 0 ? node.labels.join(", ") : "(none)"}</dd>
          <dt>group</dt>
          <dd>{groupKey(node)}</dd>
        </dl>
        {entries.length === 0 ? (
          <p className="gdviz-muted">No properties stored on this node.</p>
        ) : (
          <dl className="gdviz-props">
            {entries.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{formatValue(value)}</dd>
              </div>
            ))}
          </dl>
        )}
        <div className="gdviz-row">
          {focusNodeId === node.id ? (
            <button type="button" onClick={() => onFocus(null)}>
              Show full graph
            </button>
          ) : (
            <button type="button" onClick={() => onFocus(node.id)}>
              Focus neighborhood
            </button>
          )}
          <button type="button" onClick={onClear}>
            Clear selection
          </button>
        </div>
      </section>
    );
  }

  const edge = payload.edges.find((candidate) => candidate.id === selection.id);
  if (!edge) return null;
  const entries = Object.entries(edge.properties);
  return (
    <section aria-label="Inspector">
      <h2>Edge inspector</h2>
      <dl className="gdviz-props">
        <dt>id</dt>
        <dd>{edge.id}</dd>
        <dt>type</dt>
        <dd>{edge.type || "(untyped)"}</dd>
        <dt>source</dt>
        <dd>{edge.source}</dd>
        <dt>target</dt>
        <dd>{edge.target}</dd>
      </dl>
      {entries.length === 0 ? (
        <p className="gdviz-muted">No properties stored on this edge.</p>
      ) : (
        <dl className="gdviz-props">
          {entries.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="gdviz-row">
        <button type="button" onClick={onClear}>
          Clear selection
        </button>
      </div>
    </section>
  );
}
