/* hello_plugin frontend: единый plugin-rpc/1, только postMessage, без сети. */
(function () {
  "use strict";

  var PROTOCOL = "plugin-rpc/1";
  var MAX_MESSAGE_BYTES = 64 * 1024;
  var REQUEST_TIMEOUT_MS = 30 * 1000;
  var DIGEST = /^sha256:[0-9a-f]{64}$/;
  var INSTANCE_TOKEN = /^[A-Za-z0-9_-]{16,128}$/;
  var REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var CAPABILITY = /^[a-z][a-z0-9._-]{0,127}(?:@[0-9A-Za-z][0-9A-Za-z._+-]{0,63})?$/;
  var ERROR_CODES = [
    "ORIGIN_MISMATCH", "PROTOCOL_UNSUPPORTED", "HANDSHAKE_MISMATCH", "HANDSHAKE_TIMEOUT",
    "NONCE_MISMATCH", "UNKNOWN_METHOD", "CAPABILITY_DENIED", "DUPLICATE_REQUEST_ID",
    "PAYLOAD_TOO_LARGE", "SCOPE_DENIED", "GRANT_EXPIRED", "INVALID_ENVELOPE",
    "REQUEST_FAILED", "REQUEST_TIMEOUT"
  ];

  var root = document.documentElement;
  var PLUGIN_ID = root.getAttribute("data-plugin-id") || "";
  var PLUGIN_VERSION = root.getAttribute("data-plugin-version") || "";
  // data-host-origin remains canonical. Its empty value may fall back only to
  // the bounded server-derived iframe.name DTO installed by the trusted host.
  // Neither window.location, URL query, tokens nor first messages are trust inputs.
  var canonicalHostOrigin = root.getAttribute("data-host-origin") || "";
  var shellHandoff = null;
  var HOST_ORIGIN = canonicalHostOrigin;
  if (!HOST_ORIGIN) {
    shellHandoff = readShellHandoff();
    if (shellHandoff) HOST_ORIGIN = shellHandoff.plugin_shell_origin;
  }

  var stateEl = document.getElementById("state");
  var greetBtn = document.getElementById("greet");
  var refreshBtn = document.getElementById("refresh");
  var listEl = document.getElementById("greetings");
  var pending = new Map();
  var grantTimer = null;
  var handshake = {
    host_ready: false,
    granted: false,
    blocked: false,
    instance_id: "",
    nonce: "",
    artifact_digest: "",
    capabilities: [],
    scope: {},
    expires_at: 0
  };

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function exactShape(value, required, optional) {
    if (!isRecord(value)) return false;
    var allowed = new Set(required.concat(optional || []));
    return Object.keys(value).every(function (key) {
      return allowed.has(key) && key !== "__proto__" && key !== "prototype" && key !== "constructor";
    }) && required.every(function (key) {
      return Object.prototype.hasOwnProperty.call(value, key);
    });
  }

  function messageSizeOk(data) {
    try {
      return new TextEncoder().encode(JSON.stringify(data)).length <= MAX_MESSAGE_BYTES;
    } catch (_err) {
      return false;
    }
  }

  function validHostOrigin(value) {
    if (!value || value === "null" || value === "*") return false;
    try {
      var parsed = new URL(value);
      return parsed.protocol === "https:" && parsed.origin === value;
    } catch (_err) {
      return false;
    }
  }

  function readShellHandoff() {
    if (window.parent === window || typeof window.name !== "string"
      || !window.name || window.name.length > 4096) return null;
    var value;
    try { value = JSON.parse(window.name); } catch (_err) { return null; }
    var required = [
      "protocol", "plugin_shell_origin", "plugin_id", "manifest_version", "artifact_digest"
    ];
    if (!exactShape(value, required)
      || value.protocol !== "agentscale-plugin-shell/1"
      || !validHostOrigin(value.plugin_shell_origin)
      || value.plugin_id !== PLUGIN_ID || value.manifest_version !== PLUGIN_VERSION
      || typeof value.artifact_digest !== "string" || !DIGEST.test(value.artifact_digest)) {
      return null;
    }
    return value;
  }

  function isTrusted(event) {
    return validHostOrigin(HOST_ORIGIN)
      && event.origin === HOST_ORIGIN
      && event.source === window.parent
      && isRecord(event.data);
  }

  function setState(text, isError) {
    stateEl.textContent = text;
    stateEl.className = isError ? "state-error" : "state-ok";
  }

  function newId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    throw new Error("secure randomUUID unavailable");
  }

  function envelope(type) {
    return {
      protocol: PROTOCOL,
      type: type,
      plugin_id: PLUGIN_ID,
      version: PLUGIN_VERSION,
      artifact_digest: handshake.artifact_digest,
      instance_id: handshake.instance_id,
      nonce: handshake.nonce
    };
  }

  function hasExpectedIdentity(data) {
    return data.protocol === PROTOCOL
      && data.plugin_id === PLUGIN_ID
      && data.version === PLUGIN_VERSION
      && data.artifact_digest === handshake.artifact_digest
      && data.instance_id === handshake.instance_id
      && data.nonce === handshake.nonce;
  }

  function send(message) {
    if (handshake.blocked || !validHostOrigin(HOST_ORIGIN) || !messageSizeOk(message)) return false;
    try {
      window.parent.postMessage(message, HOST_ORIGIN);
      return true;
    } catch (_err) {
      return false;
    }
  }

  function reset(reason) {
    handshake.blocked = true;
    handshake.host_ready = false;
    handshake.granted = false;
    if (grantTimer !== null) {
      clearTimeout(grantTimer);
      grantTimer = null;
    }
    pending.forEach(function (entry) { clearTimeout(entry.timer); });
    pending.clear();
    greetBtn.disabled = true;
    refreshBtn.disabled = true;
    // Причина формируется только нашим кодом, не текстом из host payload.
    setState("Канал закрыт: " + reason, true);
  }

  function onHostReady(data) {
    var required = ["protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce"];
    if (handshake.host_ready || handshake.granted || handshake.blocked) {
      reset("повторный host.ready");
      return;
    }
    if (!exactShape(data, required) || data.type !== "host.ready"
      || data.protocol !== PROTOCOL || data.plugin_id !== PLUGIN_ID || data.version !== PLUGIN_VERSION
      || typeof data.artifact_digest !== "string" || !DIGEST.test(data.artifact_digest)
      || typeof data.instance_id !== "string" || !INSTANCE_TOKEN.test(data.instance_id)
      || typeof data.nonce !== "string" || !INSTANCE_TOKEN.test(data.nonce)
      || (shellHandoff !== null && data.artifact_digest !== shellHandoff.artifact_digest)) {
      reset("некорректный host.ready");
      return;
    }
    handshake.host_ready = true;
    handshake.instance_id = data.instance_id;
    handshake.nonce = data.nonce;
    handshake.artifact_digest = data.artifact_digest;
    if (!send(envelope("plugin.ready"))) {
      reset("plugin.ready не отправлен");
      return;
    }
    setState("Ожидание grant.ready…", false);
  }

  function validScope(scope) {
    if (!exactShape(scope, ["company_id"], ["cabinet_id", "project_id", "session_id"])) return false;
    return Object.keys(scope).every(function (key) {
      return typeof scope[key] === "string" && scope[key].trim().length > 0 && scope[key].length <= 128;
    });
  }

  function onGrantReady(data) {
    var required = [
      "protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce",
      "capabilities", "scope", "expires_at"
    ];
    if (!handshake.host_ready || handshake.granted || !exactShape(data, required)
      || data.type !== "grant.ready" || !hasExpectedIdentity(data)
      || !Array.isArray(data.capabilities) || data.capabilities.length > 64
      || new Set(data.capabilities).size !== data.capabilities.length
      || data.capabilities.some(function (item) { return typeof item !== "string" || !CAPABILITY.test(item); })
      || !validScope(data.scope) || typeof data.expires_at !== "string") {
      reset("некорректный grant.ready");
      return;
    }
    var expiry = Date.parse(data.expires_at);
    if (!Number.isFinite(expiry) || expiry <= Date.now()) {
      reset("grant истёк");
      return;
    }
    if (data.capabilities.indexOf("hello.greetings@1.0") < 0) {
      reset("capability hello.greetings@1.0 не выдана");
      return;
    }
    handshake.capabilities = data.capabilities.slice();
    handshake.scope = Object.assign({}, data.scope);
    handshake.expires_at = expiry;
    handshake.granted = true;
    grantTimer = setTimeout(function () { reset("grant истёк"); }, Math.max(1, expiry - Date.now()));
    greetBtn.disabled = false;
    refreshBtn.disabled = false;
    setState("Плагин готов.", false);
    rpcRequest("list", {});
  }

  function operationParams(operation, params) {
    // Tenant assertions come only from the verified grant, never from URL or
    // caller-controlled operation params. Backend still revalidates every value.
    if (!validScope(handshake.scope) || !isRecord(params)) return undefined;
    var reserved = ["company_id", "cabinet_id", "project_id", "session_id"];
    if (Object.keys(params).some(function (key) { return reserved.indexOf(key) >= 0; })) return undefined;
    var scoped = { company_id: handshake.scope.company_id };
    ["cabinet_id", "project_id"].forEach(function (key) {
      if (handshake.scope[key] !== undefined) scoped[key] = handshake.scope[key];
    });
    if (operation === "list") {
      if (Object.keys(params).some(function (key) { return key !== "limit"; })) return undefined;
      if (params.limit !== undefined
        && (!Number.isInteger(params.limit) || params.limit < 1 || params.limit > 200)) return undefined;
    } else if (operation === "greet") {
      if (Object.keys(params).some(function (key) { return key !== "message"; })) return undefined;
      if (typeof params.message !== "string" || !params.message.trim() || params.message.length > 4000) return undefined;
    } else {
      return undefined;
    }
    return Object.assign(scoped, params);
  }

  function rpcRequest(operation, params) {
    if (!handshake.granted || handshake.blocked || Date.now() >= handshake.expires_at) {
      reset("RPC без действующего grant");
      return;
    }
    var scopedParams = operationParams(operation, params);
    if (!scopedParams) {
      reset("некорректный scope или params");
      return;
    }
    if (pending.size >= 128) {
      setState("Слишком много ожидающих RPC", true);
      return;
    }
    var requestId;
    try { requestId = newId(); } catch (_err) {
      reset("secure random недоступен");
      return;
    }
    var message = envelope("plugin.rpc.request");
    message.request_id = requestId;
    message.method = "capability.call";
    message.capability = { id: "hello.greetings", version: "1.0" };
    message.context = Object.assign({}, handshake.scope);
    message.idempotency_key = requestId;
    message.input = { method: operation, params: scopedParams };
    if (!messageSizeOk(message)) {
      setState("RPC-пакет превышает лимит", true);
      return;
    }
    var timer = setTimeout(function () {
      pending.delete(requestId);
      setState("Таймаут RPC", true);
    }, REQUEST_TIMEOUT_MS);
    pending.set(requestId, { operation: operation, timer: timer });
    if (!send(message)) {
      clearTimeout(timer);
      pending.delete(requestId);
      reset("RPC не отправлен");
    }
  }

  function validRpcError(error) {
    return exactShape(error, ["code", "message", "trace_id"])
      && typeof error.code === "string" && ERROR_CODES.indexOf(error.code) >= 0
      && typeof error.message === "string" && error.message.length > 0 && error.message.length <= 500
      && typeof error.trace_id === "string" && REQUEST_ID.test(error.trace_id);
  }

  function onRpcResponse(data) {
    var required = [
      "protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce", "request_id", "ok"
    ];
    if (!handshake.granted || !exactShape(data, required, ["result", "error"])
      || !hasExpectedIdentity(data) || typeof data.request_id !== "string"
      || !REQUEST_ID.test(data.request_id) || typeof data.ok !== "boolean") {
      reset("некорректный RPC response");
      return;
    }
    var entry = pending.get(data.request_id);
    if (!entry) return; // duplicate/unknown response не меняет состояние
    if ((data.ok && (!Object.prototype.hasOwnProperty.call(data, "result") || data.error !== undefined))
      || (!data.ok && (data.result !== undefined || !validRpcError(data.error)))) {
      reset("некорректный RPC result");
      return;
    }
    pending.delete(data.request_id);
    clearTimeout(entry.timer);
    if (!data.ok) {
      setState("Ошибка RPC: " + data.error.code, true);
      return;
    }
    var result = data.result;
    if (entry.operation === "list") {
      if (Array.isArray(result)) renderList(result);
      else if (isRecord(result) && Array.isArray(result.items)) renderList(result.items);
      else {
        reset("некорректный список приветствий");
        return;
      }
    }
    setState("Готово.", false);
  }

  function onPluginError(data) {
    var required = ["protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce", "error"];
    if (!exactShape(data, required, ["request_id"]) || !hasExpectedIdentity(data) || !validRpcError(data.error)) {
      reset("некорректный plugin.error");
      return;
    }
    reset("ошибка протокола " + data.error.code);
  }

  function renderList(items) {
    listEl.replaceChildren();
    if (!items.length) {
      var empty = document.createElement("li");
      empty.textContent = "Приветствий пока нет";
      listEl.appendChild(empty);
      return;
    }
    items.slice(0, 1000).forEach(function (item) {
      var li = document.createElement("li");
      li.textContent = isRecord(item) && typeof item.message === "string" ? item.message : "";
      listEl.appendChild(li);
    });
  }

  window.addEventListener("message", function (event) {
    if (!isTrusted(event)) return;
    var data = event.data;
    if (!messageSizeOk(data)) {
      reset("превышен размер сообщения");
      return;
    }
    if (data.type === "host.ready") {
      onHostReady(data);
      return;
    }
    if (data.type === "grant.ready") {
      onGrantReady(data);
      return;
    }
    if (data.type === "plugin.rpc.response") {
      onRpcResponse(data);
      return;
    }
    if (data.type === "plugin.error") {
      onPluginError(data);
      return;
    }
    reset("неизвестный тип сообщения");
  });

  greetBtn.addEventListener("click", function () {
    rpcRequest("greet", { message: "Привет от hello_plugin" });
  });
  refreshBtn.addEventListener("click", function () { rpcRequest("list", {}); });

  if (!validHostOrigin(HOST_ORIGIN)) reset("data-host-origin/server handoff не настроен");
})();
