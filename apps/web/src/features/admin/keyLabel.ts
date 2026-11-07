import type { AdminKey } from "./service";

/**
 * How a key is named on screen — one answer for both places that list keys.
 *
 * A key is "an address and a secret", so the address is what identifies it. A
 * key created before the address became a field has no endpoint at all; it still
 * knows which provider it was resolved through, and hiding that behind "not set"
 * would tell the administrator less than the server does.
 */
export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

type Translate = (key: string) => string;

export function keyEndpointLabel(
  key: Pick<AdminKey, "baseUrl" | "provider">,
  t: Translate,
): string {
  if (key.baseUrl) return hostOf(key.baseUrl);
  if (key.provider) return key.provider;
  return t("admin.keyEndpointUnknown");
}

export function keyStatusLabel(status: string, t: Translate): string {
  if (status === "revoked") return t("admin.keyStatusRevoked");
  if (status === "expired") return t("admin.keyStatusExpired");
  return t("admin.keyStatusActive");
}
