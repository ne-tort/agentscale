/**
 * Плагинный контур web-оболочки (v4, F9).
 *
 * Всё, что нужно родителю для интеграции: компонент iframe, хук состояния,
 * контракт `PluginInfoPort`, протокол `plugin-rpc/1` и коды ошибок.
 * Роутер, каталог i18n и админские экраны этим каталогом не трогаются.
 */

export {
  PluginFrame,
  PLUGIN_SANDBOX,
  assertSafeSrc,
  expectedPluginOrigin,
  UnsafePluginSrcError,
} from "./PluginFrame";
export type {
  PluginFrameProps,
  SafeSrcFailure,
} from "./PluginFrame";

export {
  usePluginFrame,
  resolveStaticOrigin,
  pluginFrameStatusKey,
} from "./usePluginFrame";
export type {
  PluginInfoPort,
  PluginInfoQuery,
  PluginInfo,
  ConnectedPluginInfo,
  NotConnectedPluginInfo,
  PluginFrameConfig,
  UsePluginFrameOptions,
  UsePluginFrameResult,
  PluginFrameStatus,
} from "./usePluginFrame";

export {
  createPluginChannel,
  PLUGIN_RPC_PROTOCOL,
  PLUGIN_RPC_PROTOCOL as RPC_PROTOCOL,
  OPAQUE_ORIGIN,
} from "./rpc";
export type {
  PluginChannel,
  PluginChannelFactory,
  PluginChannelOptions,
  PluginChannelMessage,
  PluginReadyMessage,
  HostReadyMessage,
  GrantReadyMessage,
  PluginRequestHandler,
  PluginRequestContext,
  PluginRpcError,
  PluginRpcErrorCode,
  PluginRpcMethod,
  PluginRpcRequestMessage,
  PluginRpcResponseMessage,
  PluginErrorMessage,
} from "./rpc";

export {
  PLUGIN_ERROR_CODES,
  PluginFrameError,
  isPluginFrameError,
  toPluginFrameError,
  pluginErrorFromCode,
  pluginErrorKey,
  pluginSessionRequired,
} from "./errors";
export { buildPluginSrc } from "./bootstrap";
export {
  createManifestOperationBroker,
  createPluginGatewayTransport,
  PluginOperationError,
} from "./operationBroker";
export type {
  ManifestOperationBrokerOptions,
  PluginGatewayTransportOptions,
  PluginOperationTransport,
  PluginOperationTransportRequest,
  PluginOperationDenial,
} from "./operationBroker";
export type { PluginErrorCode } from "./errors";
