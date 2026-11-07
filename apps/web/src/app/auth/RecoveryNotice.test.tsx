// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { RecoveryNotice } from "./RecoveryNotice";
import type { AuthSnapshot } from "./lifecycle";

function Destination() {
  const location = useLocation();
  return <output data-destination>{`${location.pathname}${location.search}${location.hash}`}</output>;
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(snapshot: AuthSnapshot, retry = vi.fn(async () => {}), signOut = vi.fn(async () => {})) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<MemoryRouter initialEntries={["/projects/p-8/chat/s-5?tab=history#latest"]}>
      <Routes>
        <Route path="*" element={<><RecoveryNotice snapshot={snapshot} retry={retry} signOut={signOut} /><Destination /></>} />
      </Routes>
    </MemoryRouter>);
  });
  return { retry, signOut };
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
});

describe("RecoveryNotice", () => {
  it("offers a retry after a transient outage without forcing a login redirect", async () => {
    const { retry } = await mount({ status: "error", tokens: { access_token: "access", refresh_token: "refresh" }, cabinets: [], contours: [], error: new Error("Network unavailable") });

    expect(document.body.textContent).toContain("Your saved sign-in is still on this device.");
    const retryButton = [...document.querySelectorAll("button")].find((button) => button.textContent === "Retry session");
    expect(retryButton).toBeTruthy();
    await act(async () => retryButton?.click());
    expect(retry).toHaveBeenCalledTimes(1);
    expect(document.body.querySelector("[data-destination]")?.textContent).toBe("/projects/p-8/chat/s-5?tab=history#latest");
  });

  it("names revoked and expired sessions and offers account sign-in", async () => {
    await mount({ status: "unauthenticated", cabinets: [], contours: [], endReason: "revoked" });
    expect(document.body.textContent).toContain("This account is no longer active");
    const switchButton = [...document.querySelectorAll("button")].find((button) => button.textContent === "Sign in / switch account");
    expect(switchButton).toBeTruthy();
    await act(async () => switchButton?.click());
    expect(document.body.querySelector("[data-destination]")?.textContent).toBe("/login?next=%2Fprojects%2Fp-8%2Fchat%2Fs-5%3Ftab%3Dhistory%23latest");

    await act(async () => root?.unmount());
    root = undefined;
    host?.remove();
    host = undefined;
    await mount({ status: "unauthenticated", cabinets: [], contours: [], endReason: "expired" });
    expect(document.body.textContent).toContain("Your session has expired");
  });
});