// Client-side export (VIZ-08): SVG snapshot, PNG raster, and JSON payload
// download. Everything renders locally; no server round trip.
import type { VizPayload } from "./viz-types";

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function standaloneSvg(svg: SVGSVGElement): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const width = svg.clientWidth || 800;
  const height = svg.clientHeight || 600;
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  const app = document.querySelector(".gdviz");
  const background = app ? getComputedStyle(app).backgroundColor : "#ffffff";
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("width", "100%");
  rect.setAttribute("height", "100%");
  rect.setAttribute("fill", background);
  clone.insertBefore(rect, clone.firstChild);
  return new XMLSerializer().serializeToString(clone);
}

export function exportSvg(svg: SVGSVGElement, filename = "gestaltdb-graph.svg") {
  downloadBlob(filename, new Blob([standaloneSvg(svg)], { type: "image/svg+xml;charset=utf-8" }));
}

export async function exportPng(svg: SVGSVGElement, filename = "gestaltdb-graph.png") {
  const text = standaloneSvg(svg);
  const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("PNG export failed to rasterize the SVG snapshot"));
      image.src = url;
    });
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = (svg.clientWidth || 800) * scale;
    canvas.height = (svg.clientHeight || 600) * scale;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("PNG export found no 2d canvas context");
    context.scale(scale, scale);
    context.drawImage(image, 0, 0, svg.clientWidth || 800, svg.clientHeight || 600);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("PNG export produced an empty image");
    downloadBlob(filename, blob);
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function exportJson(payload: VizPayload, filename = "gestaltdb-graph.json") {
  downloadBlob(filename, new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
}
