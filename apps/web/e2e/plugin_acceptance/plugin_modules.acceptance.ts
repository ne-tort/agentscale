import type { APIRequestContext, Browser, Page } from "@playwright/test";
import { expect, request as playwrightRequest, test } from "@playwright/test";

import {
  allPluginReady,
  capturePluginSource,
  dispatchStoredPluginSource,
  dispatchToHost,
  dispatchToPlugin,
  dispatchWrongSource,
  hostRpcResponses,
  installBrowserAcceptance,
  latestHandshake,
  listenerState,
  openPrivatePlugin,
  openPublicPlugin,
  pluginIframe,
  privatePageUrl,
  rpcFromHandshake,
  signIn,
  waitPrivatePluginReady,
  type BrowserAcceptanceProbe,
  type HandshakeObservation,
} from "./support/browser";
import { PLUGIN_ACCEPTANCE as env } from "./support/environment";
import {
  assertAdminInstallation,
  authHeaders,
  expectBootstrap,
  expectDeniedStatus,
  expectGatewayDenial,
  expectStaticHeaders,
  gatewayUrl,
  loginAccessToken,
  pluginpagePath,
  privateBootstrapPath,
  publicBootstrapPath,
  readObject,
  staticEntryUrl,
} from "./support/http";

let network: BrowserAcceptanceProbe;

function memberToken(request: APIRequestContext): Promise<string> {
  return loginAccessToken(request, env.memberUsername, env.memberPassword);
}

function adminToken(request: APIRequestContext): Promise<string> {
  return loginAccessToken(request, env.adminUsername, env.adminPassword);
}

const revokeCalls: string[] = [];

async function revokeDedicatedGrant(
  request: APIRequestContext,
  companyId: string,
  expectedGrantId: string,
): Promise<void> {
  expect(companyId, "natural-expiry fixture must never be revoked").not.toBe(env.expiringCompanyId);
  revokeCalls.push(companyId);
  const admin = await adminToken(request);
  const revoked = await request.post(gatewayUrl("/api/v1/admin/plugins/revoke"), {
    headers: authHeaders(admin),
    data: { company_id: companyId, plugin_id: env.pluginId },
    timeout: env.httpTimeoutMs,
    maxRedirects: 0,
  });
  expect(revoked.status()).toBe(200);
  expect(await readObject(revoked, "dedicated grant revoke")).toMatchObject({
    company_id: companyId,
    plugin_id: env.pluginId,
    grant_id: expectedGrantId,
    grant_status: "revoked",
    token_revoked: true,
    replayed: false,
    database_preserved: true,
  });
}

function primaryScope(): Record<string, string> {
  return { company_id: env.companyId, cabinet_id: env.cabinetId, project_id: env.projectId };
}

function scopedBackendPath(path: string, scope: Record<string, string>, limit?: number): string {
  const query = new URLSearchParams(scope);
  if (limit !== undefined) query.set("limit", String(limit));
  return `${path}?${query.toString()}`;
}

function differentDigest(): string {
  const replacement = env.artifactDigest.endsWith("0") ? "1" : "0";
  return `${env.artifactDigest.slice(0, -1)}${replacement}`;
}

function differentToken(value: unknown): string {
  const token = String(value);
  return `${token.slice(0, -1)}${token.endsWith("a") ? "b" : "a"}`;
}

async function expectUniqueObservedChannels(page: Page): Promise<void> {
  const readyMessages = await allPluginReady(page);
  const channelKeys = readyMessages.map((entry) => `${entry.data.instance_id}:${entry.data.nonce}`);
  expect(new Set(channelKeys).size, "no old iframe listener may answer another host.ready")
    .toBe(channelKeys.length);
}

async function flushBrowser(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
}

async function expectFreshLoginDenied(browser: Browser, companyId: string): Promise<void> {
  const context = await browser.newContext({
    ignoreHTTPSErrors: false,
    storageState: { cookies: [], origins: [] },
  });
  const freshPage = await context.newPage();
  const freshNetwork = await installBrowserAcceptance(freshPage);
  try {
    expect(await context.cookies()).toEqual([]);
    await signIn(freshPage);
    await freshPage.goto(privatePageUrl(companyId));
    await expect(freshPage.locator('.plugin-page[data-plugin-phase="error"]')).toBeVisible();
    await expect(freshPage.locator('.plugin-frame[data-plugin-phase="ready"]')).toHaveCount(0);
    expect(freshNetwork.backendCalls()).toHaveLength(0);
    await freshNetwork.assertClean();
  } finally {
    await context.close();
  }
}

async function expectHelloHandshake(
  page: Page,
  scope: Record<string, string>,
  grantExpiresAt: Date,
): Promise<HandshakeObservation> {
  const handshake = await latestHandshake(page);
  const identityKeys = [
    "artifact_digest", "instance_id", "nonce", "plugin_id", "protocol", "type", "version",
  ];
  expect(Object.keys(handshake.hostReady.data).sort()).toEqual(identityKeys);
  expect(Object.keys(handshake.pluginReady.data).sort()).toEqual(identityKeys);
  expect(Object.keys(handshake.grantReady.data).sort()).toEqual([
    ...identityKeys, "capabilities", "expires_at", "scope",
  ].sort());

  for (const message of [handshake.hostReady.data, handshake.pluginReady.data, handshake.grantReady.data]) {
    expect(message.protocol).toBe("plugin-rpc/1");
    expect(message.plugin_id).toBe(env.pluginId);
    expect(message.version).toBe(env.pluginVersion);
    expect(message.artifact_digest).toBe(env.artifactDigest);
    expect(message.instance_id).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(message.nonce).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
  }
  expect(handshake.hostReady.data.type).toBe("host.ready");
  expect(handshake.pluginReady.data.type).toBe("plugin.ready");
  expect(handshake.grantReady.data.type).toBe("grant.ready");
  expect(handshake.hostReady.origin).toBe(env.shellOrigin);
  expect(handshake.pluginReady.origin).toBe("null");
  expect(handshake.grantReady.origin).toBe(env.shellOrigin);
  expect(handshake.hostReady.sourceMatches).toBe(true);
  expect(handshake.pluginReady.sourceMatches).toBe(true);
  expect(handshake.grantReady.sourceMatches).toBe(true);
  expect(handshake.hostReady.data.instance_id).toBe(handshake.pluginReady.data.instance_id);
  expect(handshake.hostReady.data.nonce).toBe(handshake.pluginReady.data.nonce);
  expect(handshake.grantReady.data.instance_id).toBe(handshake.pluginReady.data.instance_id);
  expect(handshake.grantReady.data.nonce).toBe(handshake.pluginReady.data.nonce);
  expect(handshake.grantReady.data.capabilities).toEqual([`${env.capabilityId}@${env.capabilityVersion}`]);
  expect(handshake.grantReady.data.scope).toEqual(scope);
  const effectiveExpiry = Date.parse(String(handshake.grantReady.data.expires_at));
  expect(effectiveExpiry).toBeGreaterThan(Date.now());
  expect(effectiveExpiry).toBeLessThanOrEqual(grantExpiresAt.getTime());
  return handshake;
}

async function expectPublicHandshake(page: Page): Promise<void> {
  const handshake = await latestHandshake(page);
  const identityKeys = [
    "artifact_digest", "instance_id", "nonce", "plugin_id", "protocol", "type", "version",
  ];
  expect(Object.keys(handshake.hostReady.data).sort()).toEqual(identityKeys);
  expect(Object.keys(handshake.pluginReady.data).sort()).toEqual(identityKeys);
  expect(Object.keys(handshake.grantReady.data).sort()).toEqual([
    ...identityKeys, "capabilities", "expires_at", "scope",
  ].sort());
  for (const message of [handshake.hostReady.data, handshake.pluginReady.data, handshake.grantReady.data]) {
    expect(message.protocol).toBe("plugin-rpc/1");
    expect(message.instance_id).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(message.nonce).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
  }
  expect(handshake.hostReady.data).toMatchObject({
    type: "host.ready",
    plugin_id: env.publicPluginId,
    version: env.publicPluginVersion,
    artifact_digest: env.publicArtifactDigest,
  });
  expect(handshake.hostReady.sourceMatches).toBe(true);
  expect(handshake.pluginReady.sourceMatches).toBe(true);
  expect(handshake.grantReady.sourceMatches).toBe(true);
  expect(handshake.pluginReady.data).toMatchObject({
    protocol: "plugin-rpc/1",
    type: "plugin.ready",
    plugin_id: env.publicPluginId,
    version: env.publicPluginVersion,
    artifact_digest: env.publicArtifactDigest,
  });
  expect(handshake.grantReady.data).toMatchObject({
    protocol: "plugin-rpc/1",
    type: "grant.ready",
    plugin_id: env.publicPluginId,
    version: env.publicPluginVersion,
    artifact_digest: env.publicArtifactDigest,
    capabilities: [`${env.publicCapabilityId}@${env.publicCapabilityVersion}`],
    scope: { company_id: env.publicCompanyId },
  });
  expect(handshake.pluginReady.origin).toBe("null");
  expect(handshake.hostReady.origin).toBe(env.shellOrigin);
  expect(handshake.grantReady.origin).toBe(env.shellOrigin);
  expect(Date.parse(String(handshake.grantReady.data.expires_at))).toBeLessThanOrEqual(
    env.publicGrantExpiresAt.getTime(),
  );
}

test.describe.serial("plugin modules real Chromium acceptance @live @plugin-acceptance", () => {
  test.beforeEach(async ({ page }) => {
    network = await installBrowserAcceptance(page);
  });

  test.afterEach(async () => {
    await network.assertClean();
  });

  test("01 explicit disposable fixture and bootstrap identity are exact", async ({ request }) => {
    const admin = await adminToken(request);
    const member = await memberToken(request);

    await assertAdminInstallation(request, admin, {
      companyId: env.companyId,
      pluginId: env.pluginId,
      pluginVersion: env.pluginVersion,
      artifactDigest: env.artifactDigest,
      pluginInstanceId: env.pluginInstanceId,
      grantId: env.grantId,
    });
    await assertAdminInstallation(request, admin, {
      companyId: env.expiringCompanyId,
      pluginId: env.pluginId,
      pluginVersion: env.pluginVersion,
      artifactDigest: env.artifactDigest,
      pluginInstanceId: env.expiringPluginInstanceId,
      grantId: env.expiringGrantId,
    });
    await assertAdminInstallation(request, admin, {
      companyId: env.revokeCompanyId,
      pluginId: env.pluginId,
      pluginVersion: env.pluginVersion,
      artifactDigest: env.artifactDigest,
      pluginInstanceId: env.revokePluginInstanceId,
      grantId: env.revokeGrantId,
    });
    await assertAdminInstallation(request, admin, {
      companyId: env.publicCompanyId,
      pluginId: env.publicPluginId,
      pluginVersion: env.publicPluginVersion,
      artifactDigest: env.publicArtifactDigest,
      pluginInstanceId: env.publicPluginInstanceId,
      grantId: env.publicGrantId,
    });

    const privateResponse = await request.get(gatewayUrl(
      privateBootstrapPath(env.pluginId, env.companyId, env.privateRoutePath),
    ), { headers: authHeaders(member), timeout: env.httpTimeoutMs, maxRedirects: 0 });
    expect(privateResponse.status()).toBe(200);
    expectBootstrap(await readObject(privateResponse, "private bootstrap"), {
      pluginId: env.pluginId,
      pluginVersion: env.pluginVersion,
      artifactDigest: env.artifactDigest,
      companyId: env.companyId,
      cabinetId: env.cabinetId,
      projectId: env.projectId,
      grantExpiresAt: env.grantExpiresAt,
      routePath: env.privateRoutePath,
      frontendEntry: env.frontendEntry,
      frontendBasePath: env.frontendBasePath,
      capability: `${env.capabilityId}@${env.capabilityVersion}`,
    });

    const publicResponse = await request.get(gatewayUrl(
      publicBootstrapPath(env.publicPluginId, env.publicCompanyId, env.publicRoutePath),
    ), { headers: { Accept: "application/json" }, timeout: env.httpTimeoutMs, maxRedirects: 0 });
    expect(publicResponse.status()).toBe(200);
    expect(publicResponse.headers()["x-plugin-capability-token"]).toBeUndefined();
    expect(publicResponse.headers()["set-cookie"]).toBeUndefined();
    expectBootstrap(await readObject(publicResponse, "public bootstrap"), {
      pluginId: env.publicPluginId,
      pluginVersion: env.publicPluginVersion,
      artifactDigest: env.publicArtifactDigest,
      companyId: env.publicCompanyId,
      grantExpiresAt: env.publicGrantExpiresAt,
      routePath: env.publicRoutePath,
      frontendEntry: env.publicFrontendEntry,
      frontendBasePath: env.publicFrontendBasePath,
      capability: `${env.publicCapabilityId}@${env.publicCapabilityVersion}`,
      publicPage: true,
    });
  });

  test("02 natural grant expiry without revoke denies stale RPC and fresh access", async ({ page, request, browser }) => {
    test.setTimeout(env.expiryMaxWaitMs + 90_000);
    await signIn(page);
    await openPrivatePlugin(page, env.expiringCompanyId);
    const scope = {
      company_id: env.expiringCompanyId,
      cabinet_id: env.expiringCabinetId,
      project_id: env.expiringProjectId,
    };
    const handshake = await expectHelloHandshake(page, scope, env.expiringGrantExpiresAt);
    expect(Date.parse(String(handshake.grantReady.data.expires_at))).toBe(env.expiringGrantExpiresAt.getTime());
    const staleSource = await capturePluginSource(page);
    const staleRpc = rpcFromHandshake(handshake, "natural-expiry-stale-001", scope);
    const baseline = network.backendCalls().length;

    // Do not revoke this fixture. First prove wall-clock natural expiry, then
    // attempt stale RPC. A stored source is only a stale-authority probe here,
    // never evidence of an open channel or a typed open-channel response.
    const remaining = Math.max(1, env.expiringGrantExpiresAt.getTime() - Date.now());
    await expect.poll(() => Date.now(), { timeout: remaining + 15_000 })
      .toBeGreaterThanOrEqual(env.expiringGrantExpiresAt.getTime());
    await dispatchStoredPluginSource(page, staleSource, staleRpc);
    await flushBrowser(page);
    expect(network.backendCalls(), "naturally expired stale RPC must not reach broker")
      .toHaveLength(baseline);

    await expect(page.locator('.plugin-page[data-plugin-phase="error"]')).toBeVisible();
    await expect(page.locator("iframe[data-plugin-id]")).toHaveCount(0);

    const member = await memberToken(request);
    const expiredCall = await request.get(gatewayUrl(pluginpagePath(
      env.pluginId,
      env.expiringCompanyId,
      scopedBackendPath(env.listBackendPath, scope),
    )), { headers: authHeaders(member), timeout: env.httpTimeoutMs, maxRedirects: 0 });
    await expectGatewayDenial(expiredCall, "naturally expired grant list", 403, "PLUGIN_GATEWAY_GRANT_INACTIVE");

    const bootstrap = await request.get(gatewayUrl(
      privateBootstrapPath(env.pluginId, env.expiringCompanyId, env.privateRoutePath),
    ), { headers: authHeaders(member), timeout: env.httpTimeoutMs, maxRedirects: 0 });
    expect(bootstrap.status()).toBe(404);

    await page.reload();
    await expect(page.locator('.plugin-page[data-plugin-phase="error"]')).toBeVisible();
    await expect(page.locator('.plugin-frame[data-plugin-phase="ready"]')).toHaveCount(0);
    await expectFreshLoginDenied(browser, env.expiringCompanyId);
    expect(revokeCalls, "no revoke call is allowed for the natural-expiry fixture")
      .not.toContain(env.expiringCompanyId);
  });

  test("03 private first load is ordered, scoped and executes real list/greet", async ({ page }) => {
    await signIn(page);
    await openPrivatePlugin(page);
    await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);

    const listPath = pluginpagePath(env.pluginId, env.companyId, env.listBackendPath);
    await expect.poll(() => network.backendCalls().some((call) => call.pathname === listPath)).toBe(true);
    const initialList = network.backendCalls().find((call) => call.pathname === listPath);
    expect(initialList?.scopeQuery).toEqual({
      company_id: env.companyId,
      cabinet_id: env.cabinetId,
      project_id: env.projectId,
      limit: null,
    });

    const frame = await pluginIframe(page);
    await expect(frame.locator("#state")).toHaveText("Готово.");
    const beforeGreet = network.backendCalls().length;
    await frame.locator("#greet").click();
    const greetPath = pluginpagePath(env.pluginId, env.companyId, env.greetBackendPath);
    await expect.poll(() => network.backendCalls().filter((call) => call.pathname === greetPath).length).toBe(1);
    expect(network.backendCalls().find((call) => call.pathname === greetPath)?.bodyScope).toEqual({
      company_id: env.companyId,
      cabinet_id: env.cabinetId,
      project_id: env.projectId,
    });
    await expect.poll(() => network.backendCalls().length).toBeGreaterThan(beforeGreet);
    await frame.locator("#refresh").click();
    await expect.poll(() => network.backendCalls().filter((call) => call.pathname === listPath).length)
      .toBeGreaterThan(1);
    await expect(frame.locator("#greetings")).toContainText("Привет от hello_plugin");
  });

  test("04 wrong origin/source/company never reaches the plugin backend", async ({ page }) => {
    await signIn(page);
    await openPrivatePlugin(page);
    const handshake = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);
    const frame = await pluginIframe(page);
    const baseline = network.backendCalls().length;

    await dispatchToHost(page, rpcFromHandshake(handshake, "negative-origin-001", primaryScope()), env.forbiddenOrigin);
    await flushBrowser(page);
    await expect(page.locator('.plugin-frame[data-plugin-phase="ready"]')).toBeVisible();

    await dispatchWrongSource(page, rpcFromHandshake(handshake, "negative-source-001", primaryScope()));
    await flushBrowser(page);
    await expect(page.locator('.plugin-frame[data-plugin-phase="ready"]')).toBeVisible();
    expect(network.backendCalls()).toHaveLength(baseline);

    await dispatchToHost(page, rpcFromHandshake(handshake, "negative-company-001", {
      ...primaryScope(),
      company_id: env.wrongCompanyId,
    }));
    await expect.poll(async () => (await hostRpcResponses(frame)).some((entry) => {
      const error = entry.data.error;
      return entry.data.request_id === "negative-company-001"
        && typeof error === "object" && error !== null
        && (error as Record<string, unknown>).code === "SCOPE_DENIED";
    })).toBe(true);
    expect(network.backendCalls(), "scope/origin/source denials must stop before HTTP broker transport")
      .toHaveLength(baseline);
  });

  for (const field of ["nonce", "instance_id", "plugin_id", "version", "artifact_digest"] as const) {
    test(`05 ${field} mismatch closes the channel before backend`, async ({ page }) => {
      await signIn(page);
      await openPrivatePlugin(page);
      const handshake = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);
      const baseline = network.backendCalls().length;
      const replacement = field === "artifact_digest" ? differentDigest()
        : field === "plugin_id" ? "not_the_expected_plugin"
          : field === "version" ? (env.pluginVersion === "0.0.1" ? "0.0.2" : "0.0.1")
            : differentToken(handshake.pluginReady.data[field]);
      await dispatchToHost(page, {
        ...handshake.pluginReady.data,
        type: "plugin.rpc.request",
        request_id: `negative-${field}-001`,
        method: "capability.call",
        capability: { id: env.capabilityId, version: env.capabilityVersion },
        context: primaryScope(),
        input: { method: env.listOperation, params: primaryScope() },
        idempotency_key: `negative-${field}-001`,
        [field]: replacement,
      });
      await expect(page.locator('.plugin-page[data-plugin-phase="error"]')).toBeVisible();
      expect(network.backendCalls()).toHaveLength(baseline);
    });
  }

  for (const messageType of ["host.ready", "grant.ready"] as const) {
    for (const failure of ["origin", "source", "nonce", "digest"] as const) {
      test(`05 plugin-side ${messageType} ${failure} forgery stops before broker HTTP`, async ({ page }) => {
        await signIn(page);
        await openPrivatePlugin(page);
        const handshake = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);
        const frame = await pluginIframe(page);
        const baseline = network.backendCalls().length;
        const sourceMessage = messageType === "host.ready" ? handshake.hostReady.data : handshake.grantReady.data;
        const forged = { ...sourceMessage };
        if (failure === "nonce") forged.nonce = differentToken(forged.nonce);
        if (failure === "digest") forged.artifact_digest = differentDigest();

        await dispatchToPlugin(frame, forged, {
          origin: failure === "origin" ? env.forbiddenOrigin : env.shellOrigin,
          source: failure === "source" ? "self" : "parent",
        });
        await flushBrowser(page);

        if (failure === "origin" || failure === "source") {
          await expect(frame.locator("#state")).toHaveText("Готово.");
          await expect(frame.locator("#refresh")).toBeEnabled();
        } else {
          await expect(frame.locator("#state")).toContainText("Канал закрыт:");
          await expect(frame.locator("#refresh")).toBeDisabled();
          await expect(frame.locator("#greet")).toBeDisabled();
        }
        expect(network.backendCalls(), "forged host/plugin handshake must not invoke broker transport")
          .toHaveLength(baseline);
      });
    }
  }

  test("06 reload/retry/dispose/second load use fresh channels without residual listeners", async ({ page }) => {
    await signIn(page);
    await openPrivatePlugin(page);
    const first = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);

    await page.reload();
    await waitPrivatePluginReady(page);
    const reloaded = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);
    expect(reloaded.pluginReady.data.instance_id).not.toBe(first.pluginReady.data.instance_id);
    expect(reloaded.pluginReady.data.nonce).not.toBe(first.pluginReady.data.nonce);
    expect(await allPluginReady(page)).toHaveLength(1);
    const listenersAtReady = await listenerState(page);

    // An exact duplicate grant is rejected by the real hello listener before RPC.
    const reloadedFrame = await pluginIframe(page);
    const duplicateBaseline = network.backendCalls().length;
    await dispatchToPlugin(reloadedFrame, reloaded.grantReady.data);
    await flushBrowser(page);
    await expect(reloadedFrame.locator("#state")).toContainText("некорректный grant.ready");
    await expect(reloadedFrame.locator("#refresh")).toBeDisabled();
    expect(network.backendCalls()).toHaveLength(duplicateBaseline);

    // Force host-side failure afterwards so Retry can be exercised. The closed
    // host channel must remove exactly its own message listener.
    await dispatchToHost(page, {
      ...reloaded.pluginReady.data,
      type: "plugin.ready",
      nonce: differentToken(reloaded.pluginReady.data.nonce),
    });
    await expect(page.locator('.plugin-page[data-plugin-phase="error"]')).toBeVisible();
    expect((await listenerState(page)).active).toBe(listenersAtReady.active - 1);
    expect((await listenerState(page)).removes).toBe(listenersAtReady.removes + 1);

    await page.getByRole("button", { name: /^(Retry|Повторить)$/ }).click();
    await waitPrivatePluginReady(page);
    const retried = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);
    expect(retried.pluginReady.data.instance_id).not.toBe(reloaded.pluginReady.data.instance_id);
    expect(retried.pluginReady.data.nonce).not.toBe(reloaded.pluginReady.data.nonce);
    expect((await listenerState(page)).active).toBe(listenersAtReady.active);
    await expectUniqueObservedChannels(page);

    // A stale pre-retry identity is denied before broker transport.
    const staleBaseline = network.backendCalls().length;
    const listenersBeforeStale = await listenerState(page);
    await dispatchToHost(page, rpcFromHandshake(reloaded, "stale-after-retry-001", primaryScope()));
    await expect(page.locator('.plugin-page[data-plugin-phase="error"]')).toBeVisible();
    expect(network.backendCalls()).toHaveLength(staleBaseline);
    expect((await listenerState(page)).active).toBe(listenersBeforeStale.active - 1);

    await page.getByRole("button", { name: /^(Retry|Повторить)$/ }).click();
    await waitPrivatePluginReady(page);
    const afterStale = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);
    expect(afterStale.pluginReady.data.instance_id).not.toBe(retried.pluginReady.data.instance_id);
    expect(afterStale.pluginReady.data.nonce).not.toBe(retried.pluginReady.data.nonce);
    expect((await listenerState(page)).active).toBe(listenersBeforeStale.active);

    const listenersBeforeClose = await listenerState(page);
    await page.getByRole("button", { name: /^(Close|Закрыть)$/ }).click();
    await waitPrivatePluginReady(page);
    const disposed = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);
    expect(disposed.pluginReady.data.instance_id).not.toBe(afterStale.pluginReady.data.instance_id);
    expect(disposed.pluginReady.data.nonce).not.toBe(afterStale.pluginReady.data.nonce);
    const listenersAfterClose = await listenerState(page);
    expect(listenersAfterClose.removes).toBe(listenersBeforeClose.removes + 1);
    expect(listenersAfterClose.adds).toBe(listenersBeforeClose.adds + 1);
    expect(listenersAfterClose.active).toBe(listenersBeforeClose.active);
    await expectUniqueObservedChannels(page);

    const detachedFrame = await pluginIframe(page);
    const detachedBefore = network.detachedPluginFrames();
    await page.goto(`${env.webOrigin}/projects`);
    await expect(page.locator("iframe[data-plugin-id]")).toHaveCount(0);
    expect(network.detachedPluginFrames()).toBeGreaterThan(detachedBefore);
    let detachedEvaluationFailed = false;
    try {
      await detachedFrame.evaluate(() => document.title);
    } catch {
      detachedEvaluationFailed = true;
    }
    expect(detachedEvaluationFailed, "detached plugin Window must be unusable").toBe(true);

    await openPrivatePlugin(page);
    const secondLoad = await expectHelloHandshake(page, primaryScope(), env.grantExpiresAt);
    expect(secondLoad.pluginReady.data.instance_id).not.toBe(disposed.pluginReady.data.instance_id);
    expect(secondLoad.pluginReady.data.nonce).not.toBe(disposed.pluginReady.data.nonce);
    await expectUniqueObservedChannels(page);

    // Exercise the new channel with the old second-load identity. No stale RPC
    // response or broker call is allowed, and the current channel must close.
    const secondLoadBaseline = network.backendCalls().length;
    await dispatchToHost(page, rpcFromHandshake(disposed, "stale-second-load-001", primaryScope()));
    await expect(page.locator('.plugin-page[data-plugin-phase="error"]')).toBeVisible();
    expect(network.backendCalls()).toHaveLength(secondLoadBaseline);
  });

  test("07 anonymous public browser/bootstrap and exact public GET/HEAD need no token", async ({ page }) => {
    const anonymousRequest = await playwrightRequest.newContext({
      credentials: "omit",
      ignoreHTTPSErrors: false,
    });
    try {
      expect(await anonymousRequest.storageState()).toMatchObject({ cookies: [], origins: [] });
      expect(await page.context().cookies()).toEqual([]);
      const publicBootstrap = await anonymousRequest.get(gatewayUrl(
        publicBootstrapPath(env.publicPluginId, env.publicCompanyId, env.publicRoutePath),
      ), { headers: { Accept: "application/json" }, timeout: env.httpTimeoutMs, maxRedirects: 0 });
      expect(publicBootstrap.status()).toBe(200);
      expect(publicBootstrap.headers()["x-plugin-capability-token"]).toBeUndefined();
      expect(publicBootstrap.headers()["set-cookie"]).toBeUndefined();

      await openPublicPlugin(page);
      await expectPublicHandshake(page);
      expect(await page.context().cookies()).toEqual([]);

      const path = pluginpagePath(env.publicPluginId, env.publicCompanyId, env.publicGetPath);
      const getResponse = await anonymousRequest.get(gatewayUrl(path), {
        headers: { Accept: "application/json" },
        timeout: env.httpTimeoutMs,
        maxRedirects: 0,
      });
      expect(getResponse.status()).toBe(200);
      expect(getResponse.headers()["x-plugin-capability-token"]).toBeUndefined();
      expect(getResponse.headers()["set-cookie"]).toBeUndefined();
      expect((await getResponse.text()).trim().length).toBeGreaterThan(0);
      const headResponse = await anonymousRequest.head(gatewayUrl(path), {
        headers: { Accept: "application/json" },
        timeout: env.httpTimeoutMs,
        maxRedirects: 0,
      });
      expect(headResponse.status()).toBe(200);
      expect(headResponse.headers()["x-plugin-capability-token"]).toBeUndefined();
      expect(headResponse.headers()["set-cookie"]).toBeUndefined();
      expect(await headResponse.text()).toBe("");

      const postResponse = await anonymousRequest.post(gatewayUrl(
        pluginpagePath(env.publicPluginId, env.publicCompanyId, env.publicPostPath),
      ), {
        data: {},
        headers: { Accept: "application/json" },
        timeout: env.httpTimeoutMs,
        maxRedirects: 0,
      });
      await expectGatewayDenial(
        postResponse,
        "anonymous public POST",
        403,
        "PLUGIN_GATEWAY_OPERATION_NOT_ALLOWED",
      );

      const nonPublic = await anonymousRequest.get(gatewayUrl(
        pluginpagePath(env.publicPluginId, env.publicCompanyId, env.publicPrivateGetPath),
      ), { headers: { Accept: "application/json" }, timeout: env.httpTimeoutMs, maxRedirects: 0 });
      await expectGatewayDenial(
        nonPublic,
        "anonymous non-public GET",
        401,
        "PLUGIN_GATEWAY_SESSION_REQUIRED",
      );

      const nonPublicBootstrap = await anonymousRequest.get(gatewayUrl(publicBootstrapPath(
        env.publicPluginId,
        env.publicCompanyId,
        env.publicPrivateGetPath,
      )), { headers: { Accept: "application/json" }, timeout: env.httpTimeoutMs, maxRedirects: 0 });
      expect(nonPublicBootstrap.status()).toBe(403);

      // Cookie is an explicit credential too, even without Authorization or a
      // capability header. Anonymous bootstrap and public GET must reject it.
      const credentialHeaders = { Cookie: "plugin_acceptance_foreign=1", Accept: "application/json" };
      const cookieBootstrap = await anonymousRequest.get(gatewayUrl(
        publicBootstrapPath(env.publicPluginId, env.publicCompanyId, env.publicRoutePath),
      ), { headers: credentialHeaders, timeout: env.httpTimeoutMs, maxRedirects: 0 });
      expectDeniedStatus(cookieBootstrap, "public bootstrap with Cookie");
      const cookieGet = await anonymousRequest.get(gatewayUrl(path), {
        headers: credentialHeaders,
        timeout: env.httpTimeoutMs,
        maxRedirects: 0,
      });
      expectDeniedStatus(cookieGet, "public GET with Cookie");
    } finally {
      await anonymousRequest.dispose();
    }
  });

  test("08 direct static GET/HEAD pin CSP, security headers and exact bundle digest", async ({ request }) => {
    for (const [entry, basePath, digest] of [
      [env.frontendEntry, env.frontendBasePath, env.artifactDigest],
      [env.publicFrontendEntry, env.publicFrontendBasePath, env.publicArtifactDigest],
    ] as const) {
      const url = staticEntryUrl(entry, basePath);
      expect(new URL(url).search).toBe("");
      expect(new URL(url).hash).toBe("");
      const getResponse = await request.get(url, { timeout: env.httpTimeoutMs, maxRedirects: 0 });
      expectStaticHeaders(getResponse, digest);
      expect((await getResponse.text()).length).toBeGreaterThan(0);
      const headResponse = await request.head(url, { timeout: env.httpTimeoutMs, maxRedirects: 0 });
      expectStaticHeaders(headResponse, digest);
      expect(await headResponse.text()).toBe("");
    }
  });

  test("09 anonymous private route redirects/denies while member direct calls succeed", async ({ page, request }) => {
    await page.goto(privatePageUrl());
    await expect(page).toHaveURL(/\/login\?/);
    const loginUrl = new URL(page.url());
    expect(loginUrl.origin).toBe(env.webOrigin);
    expect(loginUrl.pathname).toBe("/login");
    expect(loginUrl.searchParams.get("next")).toBe(`/plugins/${env.pluginId}/${env.companyId}`);

    const anonymousBootstrap = await request.get(gatewayUrl(
      privateBootstrapPath(env.pluginId, env.companyId, env.privateRoutePath),
    ), { timeout: env.httpTimeoutMs, maxRedirects: 0 });
    expect(anonymousBootstrap.status()).toBe(401);
    const anonymousList = await request.get(gatewayUrl(pluginpagePath(
      env.pluginId,
      env.companyId,
      scopedBackendPath(env.listBackendPath, primaryScope()),
    )), { timeout: env.httpTimeoutMs, maxRedirects: 0 });
    await expectGatewayDenial(anonymousList, "anonymous private list", 401, "PLUGIN_GATEWAY_SESSION_REQUIRED");

    const member = await memberToken(request);
    const list = await request.get(gatewayUrl(pluginpagePath(
      env.pluginId,
      env.companyId,
      scopedBackendPath(env.listBackendPath, primaryScope(), 50),
    )), { headers: authHeaders(member), timeout: env.httpTimeoutMs, maxRedirects: 0 });
    expect(list.status()).toBe(200);
    expect(Array.isArray(await list.json())).toBe(true);

    const greet = await request.post(gatewayUrl(pluginpagePath(
      env.pluginId,
      env.companyId,
      env.greetBackendPath,
    )), {
      headers: authHeaders(member),
      data: { ...primaryScope(), message: "disposable direct member acceptance" },
      timeout: env.httpTimeoutMs,
      maxRedirects: 0,
    });
    expect(greet.status()).toBe(201);
    const created = await readObject(greet, "member greet");
    expect(created.company_id).toBe(env.companyId);
    expect(created.message).toBe("disposable direct member acceptance");
    expect(created.id).toEqual(expect.any(String));

    const afterGreet = await request.get(gatewayUrl(pluginpagePath(
      env.pluginId,
      env.companyId,
      scopedBackendPath(env.listBackendPath, primaryScope(), 50),
    )), { headers: authHeaders(member), timeout: env.httpTimeoutMs, maxRedirects: 0 });
    expect(afterGreet.status()).toBe(200);
    const rows: unknown = await afterGreet.json();
    if (!Array.isArray(rows)) throw new Error("member list after greet must be an array");
    const createdRow = rows.find((row) => typeof row === "object" && row !== null
      && (row as Record<string, unknown>).id === created.id) as Record<string, unknown> | undefined;
    expect(createdRow).toMatchObject({
      company_id: env.companyId,
      cabinet_id: env.cabinetId,
      project_id: env.projectId,
    });

    const wrongCabinet = await request.post(gatewayUrl(pluginpagePath(
      env.pluginId,
      env.companyId,
      env.greetBackendPath,
    )), {
      headers: authHeaders(member),
      data: { ...primaryScope(), cabinet_id: "not-the-verified-cabinet", message: "must not be written" },
      timeout: env.httpTimeoutMs,
      maxRedirects: 0,
    });
    expectDeniedStatus(wrongCabinet, "client cabinet assertion outside verified scope");

    const wrongCompany = await request.get(gatewayUrl(pluginpagePath(
      env.pluginId,
      env.wrongCompanyId,
      scopedBackendPath(env.listBackendPath, { ...primaryScope(), company_id: env.wrongCompanyId }),
    )), { headers: authHeaders(member), timeout: env.httpTimeoutMs, maxRedirects: 0 });
    expectDeniedStatus(wrongCompany, "member token in foreign company");
  });

  test("10 revoke deactivates only the dedicated grant and old access stays denied", async ({ page, request, browser }) => {
    await signIn(page);
    await openPrivatePlugin(page, env.revokeCompanyId);
    const scope = {
      company_id: env.revokeCompanyId,
      cabinet_id: env.revokeCabinetId,
      project_id: env.revokeProjectId,
    };
    const openHandshake = await expectHelloHandshake(page, scope, new Date(Date.now() + 15 * 60_000));
    const openFrame = await pluginIframe(page);

    await revokeDedicatedGrant(request, env.revokeCompanyId, env.revokeGrantId);

    // Use the still-open plugin channel before any reload. The request reaches
    // only Gateway, which must deny the revoked grant without backend forward.
    const listPath = pluginpagePath(env.pluginId, env.revokeCompanyId, env.listBackendPath);
    const deniedResponsePromise = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === listPath && response.request().method() === "GET";
    });
    await openFrame.locator("#refresh").click();
    const deniedResponse = await deniedResponsePromise;
    expect(deniedResponse.status()).toBe(403);
    expect(await deniedResponse.json()).toMatchObject({ code: "PLUGIN_GATEWAY_GRANT_INACTIVE" });
    await expect(openFrame.locator("#state")).toHaveText("Ошибка RPC: CAPABILITY_DENIED");
    const deniedRpcMessages = await hostRpcResponses(openFrame);
    expect(deniedRpcMessages.some((entry) => {
      const error = entry.data.error;
      return entry.data.nonce === openHandshake.pluginReady.data.nonce
        && entry.data.instance_id === openHandshake.pluginReady.data.instance_id
        && entry.data.artifact_digest === openHandshake.pluginReady.data.artifact_digest
        && typeof error === "object" && error !== null
        && (error as Record<string, unknown>).code === "CAPABILITY_DENIED";
    })).toBe(true);

    const member = await memberToken(request);
    const oldCall = await request.get(gatewayUrl(pluginpagePath(
      env.pluginId,
      env.revokeCompanyId,
      scopedBackendPath(env.listBackendPath, scope),
    )), { headers: authHeaders(member), timeout: env.httpTimeoutMs, maxRedirects: 0 });
    await expectGatewayDenial(oldCall, "revoked grant list", 403, "PLUGIN_GATEWAY_GRANT_INACTIVE");

    const bootstrap = await request.get(gatewayUrl(
      privateBootstrapPath(env.pluginId, env.revokeCompanyId, env.privateRoutePath),
    ), { headers: authHeaders(member), timeout: env.httpTimeoutMs, maxRedirects: 0 });
    expect(bootstrap.status()).toBe(404);

    await page.reload();
    await expect(page.locator('.plugin-page[data-plugin-phase="error"]')).toBeVisible();
    await expect(page.locator('.plugin-frame[data-plugin-phase="ready"]')).toHaveCount(0);
    await expectFreshLoginDenied(browser, env.revokeCompanyId);
  });
});
