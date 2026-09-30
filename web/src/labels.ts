import { propertyValue } from "./appearance";

export function entityLabel(entity: { properties: Record<string, unknown>; extra: Record<string, unknown> },
  property: string | null | undefined, fallback: string, maxLength: number): string {
  const value = propertyValue(entity, property ?? null);
  const text = value === undefined || value === null ? fallback
    : typeof value === "object" ? JSON.stringify(value) : String(value);
  const chars = Array.from(text);
  return chars.length > maxLength ? chars.slice(0, Math.max(0, maxLength - 1)).join("") + "…" : text;
}
