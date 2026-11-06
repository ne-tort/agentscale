import type { ReactNode } from "react";

export interface NavigationTab {
  readonly id: string;
  readonly label: ReactNode;
  readonly href: string;
  readonly disabled?: boolean;
}

export interface NavigationTabsProps {
  readonly label: string;
  readonly tabs: ReadonlyArray<NavigationTab>;
  readonly currentId: string;
  readonly className?: string;
}

export function NavigationTabs({ label, tabs, currentId, className }: NavigationTabsProps) {
  return <nav aria-label={label} className={["ui-navigation-tabs", className].filter(Boolean).join(" ")}>
    <ul className="ui-navigation-tabs__list">
      {tabs.map((tab) => <li key={tab.id}>
        {tab.disabled
          ? <span aria-disabled="true" className="ui-navigation-tabs__link ui-navigation-tabs__link--disabled">{tab.label}</span>
          : <a aria-current={tab.id === currentId ? "page" : undefined} className="ui-navigation-tabs__link" href={tab.href}>{tab.label}</a>}
      </li>)}
    </ul>
  </nav>;
}

export interface BreadcrumbItem {
  readonly label: ReactNode;
  readonly href?: string;
}

export interface BreadcrumbsProps {
  readonly items: ReadonlyArray<BreadcrumbItem>;
  readonly label?: string;
  readonly className?: string;
}

export function Breadcrumbs({ items, label = "Breadcrumb", className }: BreadcrumbsProps) {
  if (items.length === 0) return null;
  const currentIndex = items.length - 1;
  return <nav aria-label={label} className={["ui-breadcrumbs", className].filter(Boolean).join(" ")}>
    <ol className="ui-breadcrumbs__list">
      {items.map((item, index) => <li className="ui-breadcrumbs__item" key={`${index}`}>
        {index === currentIndex
          ? <span aria-current="page" className="ui-breadcrumbs__current">{item.label}</span>
          : item.href
            ? <a className="ui-breadcrumbs__link" href={item.href}>{item.label}</a>
            : <span>{item.label}</span>}
        {index < currentIndex && <span aria-hidden="true" className="ui-breadcrumbs__separator">/</span>}
      </li>)}
    </ol>
  </nav>;
}