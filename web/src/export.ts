// Client-side export (VIZ-08): SVG snapshot, PNG raster, and JSON payload
// download. Everything renders locally; no server round trip.
import type { VizPayload } from "./viz-types";

export function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface ExportOptions {
  scope: "viewport" | "visible";
  background: "theme" | "transparent";
  width: number;
  height: number;
  scale: number;
}

export function standaloneSvg(svg: SVGSVGElement, options?: ExportOptions): { text: string; width: number; height: number } {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  // Resolve inherited CSS and theme variables before detaching the snapshot.
  const originals = [svg, ...svg.querySelectorAll("*")];
  const copies = [clone, ...clone.querySelectorAll("*")];
  const properties = ["color", "fill", "stroke", "stroke-width", "stroke-opacity", "fill-opacity",
    "opacity", "font-family", "font-size", "font-weight", "paint-order", "display", "visibility",
    "text-anchor", "stroke-linecap", "stroke-linejoin"];
  originals.forEach((element, index) => {
    const style = getComputedStyle(element);
    const copy = copies[index] as SVGElement;
    for (const property of properties) copy.style.setProperty(property, style.getPropertyValue(property));
  });
  clone.querySelectorAll(".edge-hit").forEach((element) => element.remove());
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const sourceWidth = svg.clientWidth || 800;
  const sourceHeight = svg.clientHeight || 600;
  const width = options?.width ?? sourceWidth;
  const height = options?.height ?? sourceHeight;
  if (![width, height].every(value => Number.isInteger(value) && value >= 1 && value <= 8192)) {
    throw new Error("Export width and height must be integers between 1 and 8192");
  }
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  // Root sizing must not depend on the original flex container's styles.
  clone.style.removeProperty("display");
  clone.setAttribute("viewBox", `0 0 ${sourceWidth} ${sourceHeight}`);
  if (options?.scope === "visible") {
    const originalViewport = svg.querySelector<SVGGElement>(".viewport");
    const viewport = clone.querySelector(".viewport");
    const bounds = originalViewport?.getBBox();
    if (bounds && viewport && bounds.width > 0 && bounds.height > 0) {
      const padding = 24;
      clone.setAttribute("viewBox", `${bounds.x - padding} ${bounds.y - padding} ${bounds.width + 2 * padding} ${bounds.height + 2 * padding}`);
      viewport.removeAttribute("transform");
    }
  }
  const app = svg.closest(".gdviz");
  const background = app ? getComputedStyle(app).backgroundColor : "#ffffff";
  if (options?.background !== "transparent") {
    const [x, y, w, h] = clone.getAttribute("viewBox")!.split(" ").map(Number);
    const fitScale = Math.min(width / w, height / h);
    const backgroundWidth = width / fitScale, backgroundHeight = height / fitScale;
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    // Extend into aspect-ratio padding using SVG geometry, not only CSS backgrounds.
    rect.setAttribute("x", String(x + (w - backgroundWidth) / 2));
    rect.setAttribute("y", String(y + (h - backgroundHeight) / 2));
    rect.setAttribute("width", String(backgroundWidth)); rect.setAttribute("height", String(backgroundHeight));
    rect.setAttribute("fill", background);
    clone.insertBefore(rect, clone.firstChild);
    // SVG aspect-ratio letterboxing also needs the requested background.
    clone.style.backgroundColor = background;
  }
  return { text: new XMLSerializer().serializeToString(clone), width, height };
}

export function exportSvg(svg: SVGSVGElement, options?: ExportOptions, filename = "gestaltdb-graph.svg") {
  downloadBlob(filename, new Blob([standaloneSvg(svg, options).text], { type: "image/svg+xml;charset=utf-8" }));
}

export async function exportPng(svg: SVGSVGElement, options?: ExportOptions, filename = "gestaltdb-graph.png") {
  const { text, width, height } = standaloneSvg(svg, options);
  const scale = options?.scale ?? 2;
  if (!Number.isInteger(scale) || scale < 1 || scale > 4 || width * height * scale * scale > 32_000_000) {
    throw new Error("PNG scale must be 1–4 and the output must not exceed 32 megapixels");
  }
  const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("PNG export failed to rasterize the SVG snapshot"));
      image.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = width * scale;
    canvas.height = height * scale;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("PNG export found no 2d canvas context");
    if (options?.background !== "transparent") {
      context.fillStyle = getComputedStyle(svg.closest(".gdviz") ?? svg).backgroundColor;
      context.fillRect(0, 0, canvas.width, canvas.height);
    }
    context.scale(scale, scale);
    context.drawImage(image, 0, 0, width, height);
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
