import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import type { VizPayload, VizViewOptions } from "./viz-types";
import "./styles.css";

const payload: VizPayload | undefined = window.__GESTALTDB_VIZ__;
const initialOptions: VizViewOptions = window.__GESTALTDB_VIZ_OPTIONS__ ?? {};

const root = document.getElementById("root");
if (!root) {
  throw new Error("GestaltDB viz: missing #root element");
}

if (!payload || payload.version !== 1) {
  root.textContent = "GestaltDB viz: missing or unsupported window.__GESTALTDB_VIZ__ payload (expected version 1).";
} else {
  createRoot(root).render(
    <React.StrictMode>
      <App payload={payload} initial={initialOptions} />
    </React.StrictMode>
  );
}
