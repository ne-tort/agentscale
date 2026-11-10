import { matchPath } from "react-router-dom";
import { isCanonicalPluginRoutePath } from "./bootstrap";

export const PRIVATE_PLUGIN_ROUTE = "/plugins/:pluginId/:companyId/*";
export const PUBLIC_PLUGIN_ROUTE = "/public/plugins/:companyId/:pluginId/*";

/** Extract the nested plugin route from the shell location, never from plugin input. */
export function pluginRoutePathFromLocation(pathname: string, publicPage = false): string {
  const match = matchPath(publicPage ? PUBLIC_PLUGIN_ROUTE : PRIVATE_PLUGIN_ROUTE, pathname);
  if (!match) return "";
  const nested = match.params["*"];
  const routePath = nested ? `/${nested}` : "/";
  return isCanonicalPluginRoutePath(routePath) ? routePath : "";
}
