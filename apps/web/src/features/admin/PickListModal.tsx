import { useState } from "react";
import { useI18n } from "../../i18n/useT";
import { Picker, type PickItem } from "./picker";
import { Modal } from "./ui";

/** A modal that shows the current selection and writes the whole list on apply. */
export function PickListModal({
  title,
  items,
  initial,
  onApply,
  onClose,
}: {
  title: string;
  items: PickItem[];
  initial: string[];
  onApply: (next: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<string[]>(initial);
  const [busy, setBusy] = useState(false);

  async function apply() {
    setBusy(true);
    try {
      await onApply(selected);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      footer={
        <>
          <button className="admin-button" disabled={busy} onClick={onClose} type="button">
            {t("common.cancel")}
          </button>
          <button className="admin-button admin-button--primary" disabled={busy} onClick={() => void apply()} type="button">
            {busy ? t("common.saving") : t("admin.pickerApply")}
          </button>
        </>
      }
      onClose={onClose}
      title={title}
    >
      <Picker
        emptyLabel={t("admin.pickerEmpty")}
        items={items}
        onChange={setSelected}
        searchLabel={t("admin.pickerSearch")}
        selected={selected}
      />
    </Modal>
  );
}
