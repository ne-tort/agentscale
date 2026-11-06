import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  Breadcrumbs,
  Button,
  CheckboxField,
  ConfirmationDialog,
  EmptyState,
  IconButton,
  InlineError,
  LoadingState,
  NavigationTabs,
  OperationResult,
  SearchableList,
  SearchableTable,
  SelectField,
  StatusBadge,
  TextAreaField,
  TextField,
  Toast,
  ToastRegion,
} from "./index";

describe("shared UI foundation acceptance", () => {
  it("renders labeled controls, validation descriptions, and keyboard-ready actions", () => {
    const markup = renderToStaticMarkup(<>
      <Button variant="primary" type="submit">Save changes</Button>
      <IconButton icon="x" label="Close panel" />
      <TextField id="workspace-name" label="Workspace name" hint="Visible to your team" error="Enter a name." />
      <SelectField id="workspace-state" label="Workspace state" options={[{ value: "active", label: "Active" }]} />
      <TextAreaField id="summary" label="Summary" />
      <CheckboxField id="notify" label="Send notifications" />
    </>);

    expect(markup).toContain('type="submit"');
    expect(markup).toContain('aria-label="Close panel"');
    expect(markup).toContain('for="workspace-name"');
    expect(markup).toContain('aria-describedby="workspace-name-hint workspace-name-error"');
    expect(markup).toContain('aria-invalid="true"');
    expect(markup).toContain('for="workspace-state"');
    expect(markup).toContain('type="checkbox"');
  });

  it("exposes distinct status, error, empty, loading, operation, and toast semantics", () => {
    const markup = renderToStaticMarkup(<>
      <StatusBadge variant="warning">Needs review</StatusBadge>
      <InlineError message="Could not load records." />
      <EmptyState title="No projects" description="Create a project to continue." />
      <LoadingState label="Loading projects" />
      <OperationResult title="Save failed" message="Try again." variant="error" />
      <ToastRegion><Toast title="Saved" variant="success" onDismiss={() => undefined} /></ToastRegion>
    </>);

    expect(markup).toContain('role="alert"');
    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('aria-label="Notifications"');
    expect(markup).toContain('aria-label="Dismiss Saved"');
    expect(markup).toContain("No projects");
  });

  it("renders accessible, horizontally scrollable searchable table and list primitives", () => {
    const records = [{ id: "p1", name: "Signal Lab", status: "Running" }];
    const markup = renderToStaticMarkup(<>
      <SearchableTable
        caption="Projects"
        columns={[
          { id: "name", header: "Project", getValue: (row) => row.name },
          { id: "status", header: "Status", getValue: (row) => row.status },
        ]}
        getRowKey={(row) => row.id}
        rows={records}
      />
      <SearchableList
        getKey={(row) => row.id}
        getSearchText={(row) => `${row.name} ${row.status}`}
        items={records}
        label="Projects"
        renderItem={(row) => <a href={`/projects/${row.id}`}>{row.name}</a>}
      />
    </>);

    expect(markup).toContain("Search Projects");
    expect(markup).toContain('<caption>Projects</caption>');
    expect(markup).toContain('scope="col"');
    expect(markup).toContain('aria-label="Projects table"');
    expect(markup).toContain('role="region"');
    expect(markup).toContain('aria-label="Projects"');
    expect(markup).toContain("Signal Lab");
  });

  it("marks current navigation and breadcrumb destinations and describes confirmation dialogs", () => {
    const markup = renderToStaticMarkup(<>
      <NavigationTabs
        currentId="overview"
        label="Project sections"
        tabs={[
          { id: "overview", label: "Overview", href: "/projects/1" },
          { id: "runs", label: "Runs", href: "/projects/1/runs" },
        ]}
      />
      <Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: "Signal Lab" }]} />
      <ConfirmationDialog
        open
        title="Delete project?"
        description="This action cannot be undone."
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />
    </>);

    expect(markup).toContain('aria-label="Project sections"');
    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain('aria-label="Breadcrumb"');
    expect(markup).toContain('role="alertdialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain("This action cannot be undone.");
    expect(markup).toContain("Cancel");
  });
});