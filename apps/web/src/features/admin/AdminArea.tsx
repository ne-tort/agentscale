import { useMemo } from "react";
import { NavLink, Outlet, useOutletContext } from "react-router-dom";
import { useAuth } from "../../app/context";
import { useI18n } from "../../i18n/useT";
import { createAdminService, type AdminService } from "./service";
import "./admin.css";

/** The service every admin screen shares; screens reach it through the outlet. */
export function useAdminService(): AdminService {
  return useOutletContext<AdminService>();
}

export function AdminArea() {
  const auth = useAuth();
  const { t } = useI18n();
  const service = useMemo(
    () => createAdminService({ getAccessToken: () => auth.tokens?.access_token }),
    [auth.tokens?.access_token],
  );

  const tabs = [
    { to: "/admin", end: true, label: t("admin.tabCompanies") },
    { to: "/admin/keys", end: false, label: t("admin.tabKeys") },
    { to: "/admin/modules", end: false, label: t("admin.tabModules") },
    { to: "/admin/deleted", end: false, label: t("admin.tabDeleted") },
  ];

  return (
    <main className="admin">
      {/* No eyebrow, no "contour": the administrator knows what this page is. */}
      <header className="admin-heading">
        <h1>{t("admin.title")}</h1>
        <p className="admin-muted">{t("admin.subtitle")}</p>
      </header>
      <nav aria-label={t("admin.title")} className="admin-tabs">
        {tabs.map((tab) => (
          <NavLink
            className={({ isActive }) => (isActive ? "admin-tab admin-tab--active" : "admin-tab")}
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
