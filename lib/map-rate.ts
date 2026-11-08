const hits = new Map<string, number[]>();

export function allowMapRequest(bucket: string, limit: number, windowMs: number): boolean {
  const now = Date.now();
  const kept = (hits.get(bucket) ?? []).filter((stamp) => now - stamp < windowMs);
  if (kept.length >= limit) {
    hits.set(bucket, kept);
    return false;
  }
  kept.push(now);
  hits.set(bucket, kept);
  return true;
}
