import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ResourceWorkspace } from "./ResourceWorkspace";
import { resourceWorkspaceFixture } from "./fixtures";
import type { ContentOpsService } from "./content-ops/contracts";

const asset = { id: "asset-1", owner_scope: "company", owner_company_id: "company-1", visibility: "company", mime: "text/plain", title: "notes.txt", tags: [], created_by: "employee-1", latest_version: 1, created_at: "2026-10-01T12:00:00Z", updated_at: "2026-10-01T12:00:00Z" };

const contentOpsService: ContentOpsService = {
  listAssets: async () => [asset],
  createAsset: async () => ({ id: "asset-1", owner_scope: "company", owner_company_id: "company-1", visibility: "company", mime: null, title: "asset", tags: [], created_by: null, latest_version: null, created_at: null, updated_at: null }),
  beginVersion: async () => ({ id: "version-1", asset_id: "asset-1", version: 1, size: 0, sha256: null, etag: null, object_metadata: {}, created_at: null }),
  uploadVersion: async () => ({ id: "version-1", asset_id: "asset-1", version: 1, size: 0, sha256: null, etag: null, object_metadata: {}, created_at: null }),
  downloadVersion: async () => ({ blob: new Blob(), filename: "asset", contentType: "application/octet-stream" }),
  listAcl: async () => [],
  replaceAcl: async () => [],
};


describe("ResourceWorkspace", () => {
  it("renders the profile overview, lifecycle navigation, and generation evidence from a typed snapshot", () => {
    const markup = renderToStaticMarkup(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} />);

    expect(markup).toContain("Operations reviewer");
    expect(markup).toContain("Prompts · files · MCP");
    expect(markup).toContain("Materialization");
    expect(markup).toContain("g4");
    expect(markup).toContain("Contract ready");
  });

  it("renders blockers and no fabricated data when the service cannot load an authoritative snapshot", () => {
    const markup = renderToStaticMarkup(<ResourceWorkspace profileId="profile-ops" />);

    expect(markup).toContain("Contract blockers");
    expect(markup).toContain("No data was invented");
    expect(markup).toContain("resource_web_facade_missing");
    expect(markup).not.toContain("Review policy");
  });

  it("keeps assets reachable without a resource snapshot and blocks them without company service context", () => {
    const markup = renderToStaticMarkup(<ResourceWorkspace profileId="profile-ops" initialTab="assets" />);

    expect(markup).toContain("Assets");
    expect(markup).toContain("Asset operations require authenticated company scope");
    expect(markup).toContain("no asset request was made");
    expect(markup).not.toContain("Upload version");
  });

  it("integrates ContentOpsPanel for explicit company scope and shows resource mutations as blocked", () => {
    const markup = renderToStaticMarkup(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} initialTab="assets" companyId="company-1" contentOpsService={contentOpsService} initialAssets={[asset]} />);

    expect(markup).toContain("Company company-1");
    expect(markup).toContain("Upload");
    expect(markup).toContain("API reports the latest version number but has no version-list route");
  });

  it("shows immutable version inspection and explicit unsupported create, bind, plan, and sync reasons", () => {
    const markup = renderToStaticMarkup(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} initialTab="resources" />);

    expect(markup).toContain("Version to inspect");
    expect(markup).toContain("sha256:new");
    expect(markup).toContain("project-ops");
    expect(markup).toContain("Operations");
    expect(markup).toContain("Local · child can diverge");
    expect(markup).toContain("Version creation is disabled: the resource API and version CAS contract are not connected.");
    expect(markup).toContain("Binding is read-only: no confirmed project-binding endpoint or typed scope-admission contract is connected.");
    expect(markup).toContain("Create is disabled: no confirmed resource create endpoint is connected.");
    expect(markup).toContain("Version comparison is disabled: no exact-version diff API is connected.");
  });

  it("keeps exact scope, binding mode, generation, digest, and provenance visible", () => {
    const markup = renderToStaticMarkup(<ResourceWorkspace initialSnapshot={resourceWorkspaceFixture} initialTab="materialization" />);

    expect(markup).toContain("Desired generation");
    expect(markup).toContain("Applied generation");
    expect(markup).toContain("Source revision");
    expect(markup).toContain("Plan and sync are unavailable: no reviewed materialization-plan adapter or atomic generation-checked apply API is connected.");
  });
});
