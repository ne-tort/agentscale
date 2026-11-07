// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { SettingsProvider } from "../../preferences/SettingsProvider";
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
    // The workspace reads its copy through the i18n catalogue, so it is mounted the
    // way the shell mounts it: inside the settings provider, in the English locale
    // the assertions below read.
    const previous = window.localStorage.getItem("agentscale.locale");
    window.localStorage.setItem("agentscale.locale", "en");
    const tree = <SettingsProvider><MemoryRouter><ProjectFilesWorkspace projectId="project-1" service={service} /></MemoryRouter></SettingsProvider>;
    const markup = renderToStaticMarkup(tree);
    if (previous === null) window.localStorage.removeItem("agentscale.locale");
    else window.localStorage.setItem("agentscale.locale", previous);

    expect(markup).toContain("Live files from the project Pod");
    expect(markup).toContain('aria-label="Project folders"');
    expect(markup).toContain('aria-label="Project folder tree"');
    expect(markup).toContain("Loading the workspace");
    expect(markup).toContain("Create a folder");
    expect(markup.toLowerCase()).not.toContain("minio");
    expect(markup.toLowerCase()).not.toContain("object storage");
  });
});
