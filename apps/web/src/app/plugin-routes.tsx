import { Fragment, useMemo, type ReactElement } from "react";
import { Route, useParams } from "react-router-dom";
import { useApi, useAuth } from "./context";
import { PluginPage } from "../features/plugins/PluginPage";
import { createPluginInfoPortFor } from "../features/plugins/pluginInfo";
import { createPluginGatewayTransport } from "../features/plugins/operationBroker";

/** Private plugin deep links. The parent keeps these below RequireAuth. */
export const PLUGIN_ROUTE_PATHS = [
  "/plugins/:pluginId/:companyId",
  "/plugins/:pluginId/:companyId/*",
] as const;

/** Anonymous surface: no AppFrame/RequireAuth and no admin bootstrap fallback. */
export const PUBLIC_PLUGIN_ROUTE_PATHS = [
  "/public/plugins/:companyId/:pluginId",
  "/public/plugins/:companyId/:pluginId/*",
] as const;

function ConnectedPluginPage({ publicPage = false }: { readonly publicPage?: boolean }): ReactElement {
  const api = useApi();
  const auth = useAuth();
  const { pluginId = "", companyId = "" } = useParams();
  const infoPort = useMemo(() => createPluginInfoPortFor(api), [api]);
  const accessToken = auth.tokens?.access_token;
  const operationTransport = useMemo(() => createPluginGatewayTransport({
    companyId,
    pluginId,
    publicPage,
    getAccessToken: () => publicPage ? undefined : accessToken,
  }), [companyId, pluginId, publicPage, accessToken]);
  return <PluginPage
    infoPort={infoPort}
    operationTransport={operationTransport}
    publicPage={publicPage}
  />;
}

export const PLUGIN_ROUTE_ELEMENTS: ReactElement = <Fragment>
  {PLUGIN_ROUTE_PATHS.map((path) => <Route key={path} path={path} element={<ConnectedPluginPage />} />)}
</Fragment>;

/** Insert at Routes root, before/outside the RequireAuth subtree. */
export const PUBLIC_PLUGIN_ROUTE_ELEMENTS: ReactElement = <Fragment>
  {PUBLIC_PLUGIN_ROUTE_PATHS.map((path) => <Route key={path} path={path} element={<ConnectedPluginPage publicPage />} />)}
</Fragment>;

/** Compatibility API: this component is a fragment factory, not a Routes child tag. */
export function PluginRoutes(): ReactElement {
  return PLUGIN_ROUTE_ELEMENTS;
}
