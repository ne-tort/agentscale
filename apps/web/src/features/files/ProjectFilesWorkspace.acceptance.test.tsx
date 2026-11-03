import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { ProjectFilesWorkspace } from "./ProjectFilesWorkspace";
import type { WorkspaceService } from "./contracts";

const service: WorkspaceService = {
  listEntries: async () => ({ path: "", entries: [] }),
  previewText: async () => ({ path: "readme.md", name: "readme.md", size: 0, truncated: false, content: "" }),
  download: async () => ({ blob: new Blob(), filename: "readme.md", path: "readme.md", size: 0 }),
  upload: async () => ({ written: true, path: "readme.md", size: 0 }),
  createDirectory: async (_projectId, path) => ({ created: true, path }),
  deleteEntry: async () => ({ deleted: true }),
  moveEntry: async () => ({ moved: true }),
  copyEntry: async () => ({ copied: true }),
};

describe("Project Pod files workspace acceptance surface", () => {
  it("renders an accessible Pod-only loading shell without a storage fallback", () => {
    const markup = renderToStaticMarkup(<MemoryRouter><ProjectFilesWorkspace projectId="project-1" service={service} /></MemoryRouter>);

    expect(markup).toContain("Live files from the project Pod");
    expect(markup).toContain('aria-label="Project folders"');
    expect(markup).toContain('aria-label="Workspace folders"');
    expect(markup).toContain("Loading workspace");
    expect(markup).toContain("Create a folder");
    expect(markup.toLowerCase()).not.toContain("minio");
    expect(markup.toLowerCase()).not.toContain("object storage");
  });
});
