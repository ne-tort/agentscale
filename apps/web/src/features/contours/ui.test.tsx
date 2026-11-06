import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ConfirmAction, DataTable } from "./ui";
import type { ContourRow } from "./service";

const row = { id: "company-1", name: "Acme", status: "active" } satisfies ContourRow;

function tableMarkup(rows: ContourRow[], loading = false, error?: string) {
  return renderToStaticMarkup(<DataTable rows={rows} columns={[{ key: "name", label: "Name" }, { key: "status", label: "Status" }]} loading={loading} error={error} onRetry={() => undefined} query="" onQuery={() => undefined} />);
}

describe("contour table acceptance surface", () => {
  it("exposes loading, error and empty states", () => {
    expect(tableMarkup([], true)).toContain("Loading…");
    expect(tableMarkup([], false, "network down")).toContain("network down");
    expect(tableMarkup([])).toContain("No records found.");
  });

  it("renders search and pagination controls for real rows", () => {
    const rows = Array.from({ length: 11 }, (_, index) => ({ ...row, id: `company-${index}`, name: `Acme ${index}` }));
    const markup = tableMarkup(rows);
    expect(markup).toContain('type="search"');
    expect(markup).toContain("Page 1 of 2");
    expect(markup).toContain("Previous");
    expect(markup).toContain("Next");
  });

  it("does not expose a destructive confirmation until the action is requested", () => {
    const markup = renderToStaticMarkup(<ConfirmAction label="Delete company" onConfirm={() => undefined} />);
    expect(markup).toContain("Delete company");
    expect(markup).not.toContain("Confirm");
  });
});
