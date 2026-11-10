import { useMemo } from "react";
import { NavLink, Outlet, useLocation, useOutletContext } from "react-router-dom";
import { useAuth } from "../../app/context";
import { useI18n } from "../../i18n/useT";
import { createAdminService, type AdminService } from "./service";
import "./admin.css";

/** The service every admin screen shares; screens reach it through the outlet. */
export function useAdminService(): AdminService {
  return useOutletContext<AdminService>();
}

/** The tab paths, in bar order, and which one a path belongs to. */
export const ADMIN_TAB_PATHS = ["/admin", "/admin/keys", "/admin/modules", "/admin/plugin-modules", "/admin/deleted"] as const;

/**
 * Which tab owns a path.
 *
 * "Компании" owns the list *and* everything opened from it, but its own path is
 * the bare `/admin` — a prefix match there would swallow every other tab. So the
 * company branch is named explicitly. Without this a company's own page belonged
 * to no tab at all and the bar showed nothing selected.
 */
export function adminTabFor(pathname: string): string {
  if (pathname === "/admin" || pathname.startsWith("/admin/companies")) return "/admin";
  const match = ADMIN_TAB_PATHS.find(
    (path) => path !== "/admin" && (pathname === path || pathname.startsWith(`${path}/`)),
  );
  return match ?? "/admin";
}

export function AdminArea() {
  const auth = useAuth();
  const { t } = useI18n();
  const { pathname } = useLocation();
  const service = useMemo(
    () => createAdminService({ getAccessToken: () => auth.tokens?.access_token }),
    [auth.tokens?.access_token],
  );

  const current = adminTabFor(pathname);
  const tabs = [
    { to: "/admin", end: true, label: t("admin.tabCompanies") },
    { to: "/admin/keys", end: false, label: t("admin.tabKeys") },
    { to: "/admin/modules", end: false, label: t("admin.tabModules") },
    { to: "/admin/plugin-modules", end: false, label: t("admin.tabPluginModules") },
    { to: "/admin/deleted", end: false, label: t("admin.tabDeleted") },
  ];

  return (
    <main className="admin">
      {/* No heading, no subtitle. The tabs say where you are and what the page
          is for; a title above them repeated the same thing in different words
          and pushed the actual work a hundred pixels down. */}
      <nav aria-label={t("admin.tabsAria")} className="admin-tabs">
        {tabs.map((tab) => (
          <NavLink
            className={tab.to === current ? "admin-tab admin-tab--active" : "admin-tab"}
            end={tab.end}
            key={tab.to}
            to={tab.to}
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      {/* Companies are the subject of the work; keys, modules and the bin are
          reached from the bar, and a company's detail page is reached by
          opening it — that is the whole hierarchy, and it is three clicks deep
          at worst. */}
      <Outlet context={service} />
    </main>
  );
}
