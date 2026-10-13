// Display stored instants with an explicit timezone. Naive dates stay unknown.
export function formatTimestamp(value) {
  if (!value || !/(Z|[+-]\d\d:\d\d)$/.test(value)) return "—";
  const instant = new Date(value);
  return Number.isNaN(instant.getTime()) ? "—" : instant.toISOString().slice(0, 19).replace("T", " ") + " UTC";
}
