import type { ReactNode } from "react";

export interface BreadcrumbItem {
  readonly label: ReactNode;
  readonly href?: string;
}

export interface BreadcrumbsProps {
  readonly items: ReadonlyArray<BreadcrumbItem>;
  readonly label?: string;
  readonly separator?: ReactNode;
  readonly className?: string;
}

export function Breadcrumbs({ items, label = "Breadcrumb", separator = "/", className }: BreadcrumbsProps) {
  if (items.length === 0) return null;
  const currentIndex = items.length - 1;
  return <nav aria-label={label} className={["form-breadcrumbs", className].filter(Boolean).join(" ")}>
    <ol className="form-breadcrumbs__list">
      {items.map((item, index) => <li className="form-breadcrumbs__item" key={index}>
        {index === currentIndex
          ? <span aria-current="page" className="form-breadcrumbs__current">{item.label}</span>
          : item.href
            ? <a className="form-breadcrumbs__link" href={item.href}>{item.label}</a>
            : <span>{item.label}</span>}
        {index < currentIndex && <span aria-hidden="true" className="form-breadcrumbs__separator">{separator}</span>}
      </li>)}
    </ol>
  </nav>;
}