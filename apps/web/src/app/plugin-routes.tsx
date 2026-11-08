import { Fragment, type ReactElement } from "react";
import { Route } from "react-router-dom";
import { useApi } from "./context";
import { PluginPage } from "../features/plugins/PluginPage";
import { createPluginInfoPortFor } from "../features/plugins/pluginInfo";

/**
 * Маршруты страницы плагина в основном web-приложении (F11 A9, v4 §5.3/§7.1).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ИНСТРУКЦИЯ РОДИТЕЛЮ: КУДА ИМЕННО ВСТАВИТЬ ЭТИ `<Route>` В `app/router.tsx`
 * ════════════════════════════════════════════════════════════════════════════
 *
 * 1. Добавить импорт рядом с остальными (файл НЕ редактируется мной, задача A9):
 *      import { PLUGIN_ROUTE_ELEMENTS } from "./plugin-routes";
 *
 *    ВАЖНО (react-router 7.18.5, `createRoutesFromChildren`): детьми `<Routes>`
 *    и рекурсивно детьми `<Route>` могут быть ТОЛЬКО `<Route>` или
 *    `<React.Fragment>`. JSX-тег `<PluginRoutes />` — произвольный компонент —
 *    выбросит invariant `[PluginRoutes] is not a <Route> component`. Поэтому
 *    вставлять надо ВЫЗОВ `{PluginRoutes()}` (он возвращает `<React.Fragment>`
 *    с двумя `<Route>`) или готовый элемент `{PLUGIN_ROUTE_ELEMENTS}`, но НЕ
 *    `<PluginRoutes />` как тег. `PLUGIN_ROUTE_ELEMENTS` — предпочтительный
 *    вариант: это уже `<React.Fragment>`, ничего вызывать не нужно.
 *
 * 2. Вставить `{PLUGIN_ROUTE_ELEMENTS}` ВНУТРЬ общего
 *    `<Route element={<AppFrame />}>` (то есть внутри
 *    `<Route element={<RequireAuth />}><Route element={<AppFrame />}>…`),
 *    а НЕ снаружи и НЕ в отдельный `<Routes>`:
 *
 *      <Route element={<RequireAuth />}>
 *        <Route element={<AppFrame />}>
 *          …
 *          <Route path="dynamic-modules" element={<DynamicModulesPage />} />
 *          <Route path="dynamic-modules/:definitionId" element={<DynamicModulePage />} />
 *          {PLUGIN_ROUTE_ELEMENTS}   ← СЮДА: ПОСЛЕ dynamic-modules/...,
 *                                      ДО `<Route path="*" element={<NotFoundPage />} />`
 *          <Route path="*" element={<NotFoundPage />} />
 *        </Route>
 *      </Route>
 *
 *    Точное место: между последним `<Route path="dynamic-modules/:definitionId" …>`
 *    (router.tsx ~329) и SPA-fallback `<Route path="*" element={<NotFoundPage />} />`
 *    (~330). Порядок важен: конкретные маршруты должны идти ДО `*`, иначе
 *    `/plugins/...` перехватится fallback и откроется «Страница не найдена».
 *
 * 3. Путь `/plugins/...` НЕ должен перехватываться SPA-fallback: это отдельные
 *    `<Route>` с более высоким приоритетом совпадения, объявленные до `*`.
 *    Если в кластере есть ingress/rewrites, `/plugins/...` должен отдавать
 *    index.html ТОЛЬКО как обычный клиентский маршрут оболочки; НЕ подменять его
 *    на `/pluginpage/...` (это внешний Gateway, v4 §7.1) и не направлять на
 *    static-origin плагина (v4 §5.2/§5.3). Токен в URL недопустим нигде.
 *
 * 4. Монтировать роутер самостоятельно ЗАПРЕЩЕНО: `AppRouter` уже владеет
 *    единственным `<Routes>`. Этот модуль — только фрагмент с `<Route>`.
 *
 * 5. Родителю также нужно передать странице реализацию `PluginInfoPort`
 *    (grant/instance через доверенный API) и `VITE_PLUGIN_STATIC_ORIGIN`
 *    в env: без них `usePluginFrame` отказывает fail-closed, iframe не рендерится.
 *    Capability-токен во frontend не попадает (v4 §5.2/§4.2).
 */

/** Пути, которые родитель регистрирует в общем `<Routes>`. */
export const PLUGIN_ROUTE_PATHS = [
  "/plugins/:pluginId/:companyId",
  "/plugins/:pluginId/:companyId/*",
] as const;

/**
 * Два маршрута на одну страницу: прямой адрес и любой вложенный путь плагина
 * (`*` нужен, чтобы deep-link внутри iframe не уехал в SPA-fallback).
 *
 * Пути АБСОЛЮТНЫЕ (`/plugins/...`), поэтому не наследуют префикс родительского
 * layout-`<Route>` — он pathless, что и позволяет им матчиться от корня.
 *
 * Страница с привязанным `PluginInfoPort`.
 *
 * `useApi()` обязан вызываться ВНУТРИ `<Route>`, а не на уровне модуля:
 * клиент читает токены из auth-контекста, которого ещё нет при импорте.
 * Без порта `PluginPage` остаётся fail-closed («не подключён»), поэтому
 * связываем явно. `VITE_PLUGIN_STATIC_ORIGIN` проверяется в `pluginInfo.ts`
 * и `usePluginFrame.ts` — невалидная конфигурация = ошибка, а не iframe.
 */
function ConnectedPluginPage(): ReactElement {
  const api = useApi();
  return <PluginPage infoPort={createPluginInfoPortFor(api)} />;
}

function pluginRouteElements(): ReactElement {
  return <Fragment>
    {PLUGIN_ROUTE_PATHS.map((path) => <Route key={path} path={path} element={<ConnectedPluginPage />} />)}
  </Fragment>;
}

/**
 * ГОТОВЫЙ ФРАГМЕНТ для вставки в `router.tsx`: `{PLUGIN_ROUTE_ELEMENTS}`.
 * Создаётся один раз на модуль, чтобы не пересоздавать элементы при рендере.
 */
export const PLUGIN_ROUTE_ELEMENTS: ReactElement = pluginRouteElements();

/**
 * Компонент совместимости с «обычным» API ([A9] просил `PluginRoutes`).
 * НЕ вставляйте его JSX-тегом `<PluginRoutes />` (см. инструкцию выше) —
 * используйте `{PluginRoutes()}` или `{PLUGIN_ROUTE_ELEMENTS}`.
 */
export function PluginRoutes(): ReactElement {
  return PLUGIN_ROUTE_ELEMENTS;
}
