import type { ExportOptions } from "./export";

interface Props {
  options: ExportOptions;
  onChange: (options: ExportOptions) => void;
  exporting: boolean;
  onExport: (format: "svg" | "png") => void;
  onGraph: () => void;
  onNotices: () => void;
  hasNotices: boolean;
  onSaveView: () => void;
  onLoadView: (file: File) => void;
}

export function ExportControls({ options, onChange, exporting, onExport, onGraph, onNotices, hasNotices, onSaveView, onLoadView }: Props) {
  return <details className="gdviz-controls">
    <summary>Export and saved view</summary>
    <div className="gdviz-toolbar" role="group" aria-label="Export options">
      <label>Image scope
        <select aria-label="Image scope" value={options.scope} onChange={event => onChange({ ...options, scope: event.target.value as ExportOptions["scope"] })}>
          <option value="viewport">Current viewport</option><option value="visible">All visible content</option>
        </select>
      </label>
      <label>Image background
        <select aria-label="Image background" value={options.background} onChange={event => onChange({ ...options, background: event.target.value as ExportOptions["background"] })}>
          <option value="theme">Theme</option><option value="transparent">Transparent</option>
        </select>
      </label>
      <label>Image width<input type="number" min={1} max={8192} value={Number.isFinite(options.width) ? options.width : ""} onChange={event => onChange({ ...options, width: event.target.valueAsNumber })} /></label>
      <label>Image height<input type="number" min={1} max={8192} value={Number.isFinite(options.height) ? options.height : ""} onChange={event => onChange({ ...options, height: event.target.valueAsNumber })} /></label>
      <label>PNG scale<select aria-label="PNG scale" value={options.scale} onChange={event => onChange({ ...options, scale: Number(event.target.value) })}>
        {[1, 2, 3, 4].map(scale => <option key={scale} value={scale}>{scale}×</option>)}
      </select></label>
    </div>
    <div className="gdviz-toolbar" role="group" aria-label="Export controls">
      <button disabled={exporting} onClick={() => onExport("svg")}>Export SVG</button>
      <button disabled={exporting} onClick={() => onExport("png")}>Export PNG</button>
      <button onClick={onGraph}>Download graph JSON</button>
      <button disabled={!hasNotices} onClick={onNotices}>Download third-party notices</button>
      <button onClick={onSaveView}>Save view state</button>
      <label>Load view state<input type="file" accept="application/json,.json" onChange={event => {
        const file = event.target.files?.[0];
        if (file) onLoadView(file);
        event.target.value = "";
      }} /></label>
    </div>
  </details>;
}
