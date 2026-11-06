/** Returns a safe same-origin target from the router's `?next=` query. */
export function getSafeNextPath(search: string): string | undefined {
  const candidate = new URLSearchParams(search).get("next");
  if (!candidate || !candidate.startsWith("/") || candidate.startsWith("//") || candidate.includes("\\")) return undefined;
  try {
    const target = new URL(candidate, "https://agentscale.invalid");
    if (target.origin !== "https://agentscale.invalid" || target.pathname === "/login") return undefined;
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return undefined;
  }
}
