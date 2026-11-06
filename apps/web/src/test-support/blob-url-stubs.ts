/**
 * Blob-URL stubs that survive the teardown of the test that installed them.
 *
 * Several download paths revoke their object URL on a timer:
 * `window.setTimeout(() => URL.revokeObjectURL(href), 1000)` in
 * `ModuleRuntime`, `ContentOpsPanel`; `0` in `ProjectFilesWorkspace`,
 * `ProjectRuntime` and `EquipmentProcurementPage`. The tree is unmounted in
 * `afterEach`, but the timer was already scheduled, so it fires *after* the
 * stub has been taken away -- and when the original property is restored by
 * deleting it (which is what happens on a runner whose jsdom/Node does not
 * define `revokeObjectURL` on the constructor at all), the callback throws
 * `TypeError: URL.revokeObjectURL is not a function` inside a timer nobody
 * awaits. Vitest reports that as an unhandled error and fails the job even
 * though all 596 tests passed.
 *
 * So teardown restores the original descriptor when there was one, and
 * otherwise installs a no-op rather than removing the property: a stray timer
 * must be able to call something. Nothing asserts that the global is absent.
 */
export interface BlobUrlState {
  createObjectURL: PropertyDescriptor | undefined;
  revokeObjectURL: PropertyDescriptor | undefined;
}

export function captureBlobUrlDescriptors(): BlobUrlState {
  return {
    createObjectURL: Object.getOwnPropertyDescriptor(URL, "createObjectURL"),
    revokeObjectURL: Object.getOwnPropertyDescriptor(URL, "revokeObjectURL"),
  };
}

function restore(target: typeof URL, name: "createObjectURL" | "revokeObjectURL", original: PropertyDescriptor | undefined): void {
  if (original) {
    Object.defineProperty(target, name, original);
    return;
  }
  // Not a delete: a timer scheduled by the unmounted tree may still fire. The
  // stand-in returns a stable id for create and ignores revoke.
  Object.defineProperty(target, name, {
    configurable: true,
    writable: true,
    value: name === "createObjectURL" ? () => "blob:test-object-url" : () => undefined,
  });
}

export function restoreBlobUrlDescriptors(state: BlobUrlState): void {
  restore(URL, "createObjectURL", state.createObjectURL);
  restore(URL, "revokeObjectURL", state.revokeObjectURL);
}
