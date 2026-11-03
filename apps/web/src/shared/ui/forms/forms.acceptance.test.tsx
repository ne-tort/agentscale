import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  Breadcrumbs,
  CheckboxField,
  ConfirmationDialog,
  InlineError,
  SearchableCombobox,
  SelectField,
  TableToolbar,
  Tabs,
  TextAreaField,
  TextField,
} from "./index";

describe("reusable form UI acceptance", () => {
  it("renders typed field controls with labels, descriptions, and validation", () => {
    const markup = renderToStaticMarkup(<>
      <TextField id="email" label="Email" type="email" hint="Work address" error="Enter a valid address." required />
      <SelectField id="role" label="Role" options={[{ value: "admin", label: "Administrator" }]} placeholder="Choose a role" />
      <TextAreaField id="notes" label="Notes" rows={3} />
      <CheckboxField id="updates" label="Send updates" error="Choose whether updates are allowed." />
    </>);

    expect(markup).toContain('for="email"');
    expect(markup).toContain('type="email"');
    expect(markup).toContain('aria-describedby="email-hint email-error"');
    expect(markup).toContain('aria-invalid="true"');
    expect(markup).toContain('role="alert"');
    expect(markup).toContain('for="role"');
    expect(markup).toContain('type="checkbox"');
  });

  it("renders a searchable, labeled combobox and serializes its selected value", () => {
    const markup = renderToStaticMarkup(<SearchableCombobox
      defaultValue="active"
      hint="Choose a workspace status."
      label="Status"
      name="status"
      options={[
        { value: "active", label: "Active" },
        { value: "paused", label: "Paused", disabled: true },
      ]}
    />);

    expect(markup).toContain('role="combobox"');
    expect(markup).toContain('aria-controls=');
    expect(markup).toContain('role="listbox"');
    expect(markup).toContain('aria-disabled="true"');
    expect(markup).toContain('name="status"');
    expect(markup).toContain('value="active"');
  });

  it("renders alert, toolbar count, tab relationships, breadcrumb current item, and dialog semantics", () => {
    const markup = renderToStaticMarkup(<>
      <InlineError title="Save failed" message="Try again." />
      <TableToolbar label="Projects" resultCount={2}><select aria-label="Status filter"><option>All</option></select></TableToolbar>
      <Tabs label="Project sections" defaultValue="overview" tabs={[
        { id: "overview", label: "Overview", content: "Project summary" },
        { id: "runs", label: "Runs", content: "Recent runs" },
      ]} />
      <Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: "Signal Lab" }]} />
      <ConfirmationDialog
        open
        title="Delete project?"
        description="This action cannot be undone."
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />
    </>);

    expect(markup).toContain('role="alert"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('role="tablist"');
    expect(markup).toContain('aria-selected="true"');
    expect(markup).toContain('aria-controls=');
    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain('aria-label="Breadcrumb"');
    expect(markup).toContain('role="alertdialog"');
    expect(markup).toContain('aria-modal="true"');
  });
});