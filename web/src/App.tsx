import { useEffect, useMemo, useRef, useState } from "react";
import { GraphCanvas, type CanvasHandle } from "./GraphCanvas";
import { Inspector, type Selection } from "./Inspector";
import { legendGroups, legendTypes } from "./graph";
import { computeView } from "./view";
import { downloadBlob, exportJson, exportPng, exportSvg, type ExportOptions } from "./export";
import { ExportControls } from "./ExportControls";
import { AppearanceControls } from "./AppearanceControls";
import { ExplorationControls } from "./ExplorationControls";
import { colorFor } from "./colors";
import { graphIdentity, parseViewState, type LayoutState, type ViewState } from "./session";
import { DEFAULT_SETTINGS, type VizPayload, type VizViewOptions, type ViewerSettings } from "./viz-types";

export function App({ payload, initial = {} }: { payload: VizPayload; initial?: VizViewOptions }) {
  const [settings, setSettings] = useState<ViewerSettings>({ ...DEFAULT_SETTINGS, ...initial });
  const canvasRef = useRef<SVGSVGElement>(null);
  const controllerRef = useRef<CanvasHandle>(null);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [fitSignal, setFitSignal] = useState(0);
  const [unpinSignal, setUnpinSignal] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [restoration, setRestoration] = useState<LayoutState | null>(null);
  const [filters, setFilters] = useState<ViewState["filters"]>({
    query: "", hiddenGroups: [], hiddenTypes: [], requiredLabel: "", focusNodeId: null,
    focusDirection: "both", focusHops: 1, searchProperties: true,
    highlightOn: payload.highlight.nodes.length + payload.highlight.edges.length > 0,
  });
  const [exportOptions, setExportOptions] = useState<ExportOptions>({
    scope: "viewport", background: "theme", width: 1200, height: 800, scale: 2,
  });
  const groups = useMemo(() => legendGroups(payload), [payload]);
  const types = useMemo(() => legendTypes(payload), [payload]);
  const view = useMemo(() => computeView(payload, {
    ...filters, hiddenGroups: new Set(filters.hiddenGroups), hiddenTypes: new Set(filters.hiddenTypes),
  }), [payload, filters]);
  const hiddenNodes = new Set(view.hiddenNodes), hiddenEdges = new Set(view.hiddenEdges);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") setSelection(null); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
  useEffect(() => { if (restoration) controllerRef.current?.restore(restoration); }, [restoration]);

  async function downloadImage(format: "svg" | "png") {
    if (!canvasRef.current) return;
    setExporting(true); setError(""); setStatus("");
    try {
      if (format === "svg") exportSvg(canvasRef.current, exportOptions);
      else await exportPng(canvasRef.current, exportOptions);
      setStatus(`${format.toUpperCase()} export prepared`);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setExporting(false); }
  }

  function saveView() {
    if (!controllerRef.current) return;
    const state: ViewState = { version: 1, kind: "gestaltdb-view", graph: graphIdentity(payload),
      layout: controllerRef.current.snapshot(), settings, filters, selection };
    downloadBlob("gestaltdb-view.json", new Blob([JSON.stringify(state, null, 2)], { type: "application/json" }));
  }

  async function loadView(file: File) {
    setError(""); setStatus("");
    try {
      if (file.size > 16 * 1024 * 1024) throw new Error("View-state file exceeds 16 MB");
      // Validate every field before applying any part of the imported state.
      const state = parseViewState(JSON.parse(await file.text()), payload);
      setSettings(state.settings); setFilters(state.filters); setSelection(state.selection);
      setRestoration(state.layout);
      setStatus("View state restored");
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }

  function toggleFilter(key: "hiddenGroups" | "hiddenTypes", value: string) {
    setFilters(current => ({ ...current, [key]: current[key].includes(value)
      ? current[key].filter(item => item !== value) : [...current[key], value] }));
  }
  const title = document.title || "GestaltDB graph";
  const selectionKey = selection ? JSON.stringify([selection.kind, selection.id]) : "";
  return <main className="gdviz" data-theme={settings.theme}>
    <header className="gdviz-header">
      <h1>{title}</h1>
      <p className="gdviz-counts" aria-live="polite">Showing {view.visibleNodeCount} of {payload.nodes.length} nodes, {view.visibleEdgeCount} of {payload.edges.length} edges
        {filters.focusNodeId !== null && <> (neighborhood of {filters.focusNodeId})</>}</p>
      <div className="gdviz-toolbar" role="group" aria-label="Layout controls">
        <button onClick={() => setSettings(current => ({ ...current, paused: !current.paused }))}>{settings.paused ? "Resume layout" : "Pause layout"}</button>
        <button onClick={() => setFitSignal(value => value + 1)}>Fit visible graph</button>
        <button onClick={() => setUnpinSignal(value => value + 1)}>Unpin all</button>
        <button onClick={() => controllerRef.current?.relayout(false)}>Reset layout</button>
        <button onClick={() => controllerRef.current?.relayout(true)}>Relayout visible graph</button>
        <label>Charge<input type="range" min={-1000} max={-10} step={10} value={settings.charge} onChange={event => setSettings({ ...settings, charge: Number(event.target.value) })} /></label>
        <label>Link distance<input type="range" min={10} max={200} step={5} value={settings.linkDistance} onChange={event => setSettings({ ...settings, linkDistance: Number(event.target.value) })} /></label>
        <label><input type="checkbox" checked={settings.showLabels} onChange={event => setSettings({ ...settings, showLabels: event.target.checked })} />Node labels</label>
        <label>Edge labels<select aria-label="Edge labels" value={settings.edgeLabels} onChange={event => setSettings({ ...settings, edgeLabels: event.target.value as ViewerSettings["edgeLabels"] })}>
          <option value="off">Off</option><option value="selected">Selected / hovered</option><option value="all">All</option>
        </select></label>
        <button onClick={() => setSettings({ ...settings, theme: settings.theme === "light" ? "dark" : "light" })}>{settings.theme === "light" ? "Dark theme" : "Light theme"}</button>
        <button aria-expanded={sidebarOpen} aria-controls="gdviz-sidebar" onClick={() => setSidebarOpen(value => !value)}>{sidebarOpen ? "Hide inspector" : "Show inspector"}</button>
      </div>
      <ExportControls options={exportOptions} onChange={setExportOptions} exporting={exporting} onExport={format => void downloadImage(format)}
        onGraph={() => exportJson(payload)} onSaveView={saveView} onLoadView={file => void loadView(file)}
        hasNotices={Boolean(document.getElementById("gdviz-notices"))}
        onNotices={() => {
          const notices = JSON.parse(document.getElementById("gdviz-notices")!.textContent!).text;
          downloadBlob("gestaltdb-third-party-notices.txt", new Blob([notices], { type: "text/plain;charset=utf-8" }));
        }} />
      <AppearanceControls payload={payload} settings={settings} onChange={setSettings} />
      <ExplorationControls payload={payload} filters={filters} onChange={setFilters} />
      <p className="gdviz-status" role="status">{exporting ? "Preparing image…" : status}</p>
      {error && <p role="alert">{error}</p>}
    </header>
    {payload.truncation.truncated && <p className="gdviz-banner" role="alert">Showing a capped sample: {payload.truncation.nodes_dropped} nodes and {payload.truncation.edges_dropped} edges dropped by caps. Narrow the query or sample a subgraph for full detail.</p>}
    {payload.nodes.length === 0 && <p className="gdviz-banner">{String(payload.meta.note ?? "No graph entities to display")}</p>}
    <div className="gdviz-body">
      <GraphCanvas canvasRef={canvasRef} controllerRef={controllerRef} onPinsChange={setPinnedIds} payload={payload} settings={settings}
        fitSignal={fitSignal} unpinSignal={unpinSignal} selection={selection} onSelect={setSelection}
        hiddenNodes={view.hiddenNodes} hiddenEdges={view.hiddenEdges} dimNodes={view.dimNodes} dimEdges={view.dimEdges} />
      {sidebarOpen && <aside id="gdviz-sidebar" className="gdviz-legend" aria-label="Legend and inspector">
        <label>Select visible entity<select aria-label="Select visible entity" value={selectionKey} onChange={event => {
          if (!event.target.value) setSelection(null);
          else { const [kind, id] = JSON.parse(event.target.value); setSelection({ kind, id }); }
        }}>
          <option value="">Choose a node or edge</option>
          {selection && ((selection.kind === "node" ? hiddenNodes : hiddenEdges).has(selection.id)) && <option value={selectionKey}>Selected {selection.kind}: {selection.id} (hidden)</option>}
          <optgroup label="Nodes">{payload.nodes.filter(node => !hiddenNodes.has(node.id)).map(node => <option key={node.id} value={JSON.stringify(["node", node.id])}>Node {node.id}</option>)}</optgroup>
          <optgroup label="Edges">{payload.edges.filter(edge => !hiddenEdges.has(edge.id)).map(edge => <option key={edge.id} value={JSON.stringify(["edge", edge.id])}>Edge {edge.id} ({edge.type})</option>)}</optgroup>
        </select></label>
        {selection?.kind === "node" && <button onClick={() => controllerRef.current?.togglePin(selection.id)}>{pinnedIds.includes(selection.id) ? "Unpin selected node" : "Pin selected node"}</button>}
        <Inspector showProperties={settings.showProperties} payload={payload} selection={selection} focusNodeId={filters.focusNodeId}
          onFocus={focusNodeId => setFilters({ ...filters, focusNodeId })} onClear={() => setSelection(null)} />
        {([ ["Node groups", groups, "hiddenGroups"], ["Edge types", types, "hiddenTypes"] ] as const).map(([heading, entries, key]) => <section key={key}>
          <h2>{heading}</h2><ul>{entries.map(([value, count]) => <li key={value}><label>
            <input type="checkbox" checked={!filters[key].includes(value)} onChange={() => toggleFilter(key, value)} aria-label={`Toggle ${key === "hiddenGroups" ? "node group" : "edge type"} ${value}`} />
            <span className="gdviz-swatch" style={{ backgroundColor: colorFor(value) }} aria-hidden="true" />{value}: {count}
          </label></li>)}</ul>
        </section>)}
      </aside>}
    </div>
  </main>;
}
