// Display stored instants with an explicit timezone. Naive dates stay unknown.
export function formatTimestamp(value) {
  if (!value || !/(Z|[+-]\d\d:\d\d)$/.test(value)) return "—";
  const instant = new Date(value);
  return Number.isNaN(instant.getTime()) ? "—" : instant.toISOString().slice(0, 19).replace("T", " ") + " UTC";
}

// Stored links are optional user content, never a navigation command.
export function externalUrl(value) {
  try {
    if (typeof value !== "string" || /[\u0000-\u0020\u007f]/.test(value)) return null;
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
