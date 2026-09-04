/** One key per logical submit: the server resolves a repeated POST carrying it to the same row. */
export function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `ax-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
