import { useEffect, useId, useState, type KeyboardEvent, type ReactNode } from "react";

export interface TabItem {
  readonly id: string;
  readonly label: ReactNode;
  readonly content: ReactNode;
  readonly disabled?: boolean;
}

export interface TabsProps {
  readonly label: string;
  readonly tabs: ReadonlyArray<TabItem>;
  readonly value?: string;
  readonly defaultValue?: string;

  readonly onValueChange?: (value: string) => void;
  readonly activationMode?: "automatic" | "manual";
  readonly className?: string;
}

export function Tabs({
  label,
  tabs,
  value,
  defaultValue,
  onValueChange,
  activationMode = "automatic",
  className,
}: TabsProps) {
  const generatedId = useId();
  const enabledTabs = tabs.filter((tab) => !tab.disabled);
  const initialId = enabledTabs.find((tab) => tab.id === defaultValue)?.id ?? enabledTabs[0]?.id ?? "";
  const [internalValue, setInternalValue] = useState(initialId);
  const initialFocusId = enabledTabs.find((tab) => tab.id === value)?.id ?? initialId;
  const [focusedId, setFocusedId] = useState(initialFocusId);
  const selectedId = value ?? internalValue;
  const selectedTab = enabledTabs.find((tab) => tab.id === selectedId) ?? enabledTabs[0];
  const activeTabId = selectedTab?.id ?? "";

  useEffect(() => {
    setFocusedId(activeTabId);
  }, [activeTabId]);

  const setSelected = (nextId: string) => {
    if (value === undefined) setInternalValue(nextId);
    onValueChange?.(nextId);
    setFocusedId(nextId);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, currentId: string) => {
    const currentIndex = enabledTabs.findIndex((tab) => tab.id === currentId);
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % enabledTabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + enabledTabs.length) % enabledTabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = enabledTabs.length - 1;
    else return;

    event.preventDefault();
    const next = enabledTabs[nextIndex];
    if (!next) return;
    setFocusedId(next.id);
    document.getElementById(`${generatedId}-tab-${next.id}`)?.focus();
    if (activationMode === "automatic") setSelected(next.id);
  };

  return <div className={["form-tabs", className].filter(Boolean).join(" ")}>
    <div aria-label={label} className="form-tabs__list" role="tablist">
      {tabs.map((tab) => {
        const selected = tab.id === activeTabId;
        return <button
          aria-controls={`${generatedId}-panel-${tab.id}`}
          aria-selected={selected}
          className="form-tabs__tab"
          disabled={tab.disabled}
          id={`${generatedId}-tab-${tab.id}`}
          key={tab.id}
          onClick={() => setSelected(tab.id)}
          onFocus={() => setFocusedId(tab.id)}
          onKeyDown={(event) => handleKeyDown(event, tab.id)}
          role="tab"
          tabIndex={tab.id === focusedId ? 0 : -1}
          type="button"
        >{tab.label}</button>;
      })}
    </div>
    {tabs.map((tab) => <section
      aria-labelledby={`${generatedId}-tab-${tab.id}`}
      className="form-tabs__panel"
      hidden={tab.id !== activeTabId}
      id={`${generatedId}-panel-${tab.id}`}
      key={tab.id}
      role="tabpanel"
      tabIndex={0}
    >{tab.content}</section>)}
  </div>;
}