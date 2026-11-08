/* frontend/app.js — пилотный плагин hello_plugin, протокол plugin-rpc/1.
 *
 * Контур (§5.2, §5.3, §7.1 v4 + dynamic-ui-implementation-contract.md 4.2/4.3):
 *   * iframe живёт на ОТДЕЛЬНОМ static-origin, без cookie и без сетевого доступа
 *     (connect-src 'none' в CSP). Никаких сетевых вызовов к платформенному API:
 *     все вызовы backend идут ТОЛЬКО через postMessage к хосту, который
 *     проксирует их в /pluginpage/{plugin}/{company}/api/* (задача 13 / §7.1).
 *   * capability-токен во frontend НЕ попадает и НИКОГДА не кладётся в URL
 *     (утечка через Referer, историю и access-логи, §5.2).
 *     Здесь нет ни одного сетевого URL вообще — только postMessage.
 *   * проверка `event.origin` ОБЯЗАТЕЛЬНА для КАЖДОГО сообщения, включая ответы:
 *     fail-closed, чужой origin (в т.ч. opaque "null") отбрасывается молча.
 *   * handshake: host.ready -> plugin.ready -> grant.ready. Идентификаторы
 *     (instance_id, nonce, digest) не придумываются плагином, а приходят от хоста.
 *
 * «plugin.rpc.request» — имя RPC-запроса пилота: envelope с kind="request"
 * (закрытая форма контракта 4.3) и type="plugin.rpc.request" как тегом маршрута
 * для хоста. Ответ — kind="response" / type="plugin.rpc.response".
 */
(function () {
  "use strict";

  var PROTOCOL = "plugin-rpc/1";
  var TYPE_PLUGIN_READY = "plugin.ready";
  var TYPE_REQUEST = "plugin.rpc.request";
  var TYPE_RESPONSE = "plugin.rpc.response";
  var MAX_MESSAGE_BYTES = 64 * 1024;

  var root = document.documentElement;
  var PLUGIN_ID = root.getAttribute("data-plugin-id") || "";
  var PLUGIN_VERSION = root.getAttribute("data-plugin-version") || "";
  // Политика доверенного отправителя: публичный origin shell, НЕ секрет.
  // Пустое/невалидное значение => handshake не выполняется (fail-closed).
  var HOST_ORIGIN = root.getAttribute("data-host-origin") || "";

  var stateEl = document.getElementById("state");
  var greetBtn = document.getElementById("greet");
  var refreshBtn = document.getElementById("refresh");
  var listEl = document.getElementById("greetings");

  var handshake = {
    ready: false,
    instanceId: "",
    nonce: "",
    appHandle: "",
    digest: "",
    scope: {},
    capabilities: [],
    granted: false,
  };
  var pending = new Map();

  function setState(text, isError) {
    stateEl.textContent = text;
    stateEl.className = isError ? "state-error" : "state-ok";
  }

  function newId() {
    // crypto.randomUUID доступен только в secure context (HTTPS static-origin).
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    throw new Error(" crypto.randomUUID недоступен: нужен secure context");
  }

  /* Единственная проверка происхождения. Отдельный origin обязателен:
     "null" (opaque origin) НЕ принимается — иначе сообщение может прийти из
     любого iframe с sandbox. Сверяем и source с точным WindowProxy родителя. */
  function isTrusted(event) {
    return (
      Boolean(HOST_ORIGIN) &&
      event.origin === HOST_ORIGIN &&
      event.origin !== "null" &&
      event.source === window.parent &&
      event.data !== null &&
      typeof event.data === "object" &&
      event.data.protocol === PROTOCOL &&
      !Array.isArray(event.data)
    );
  }

  function messageSizeOk(data) {
    try {
      return new TextEncoder().encode(JSON.stringify(data)).length <= MAX_MESSAGE_BYTES;
    } catch (_err) {
      return false;
    }
  }

  function send(message) {
    // postMessage ТОЛЬКО родителю и ТОЛЬКО на доверенный origin: '*' запрещён.
    window.parent.postMessage(message, HOST_ORIGIN);
  }

  function onHostReady(data) {
    if (handshake.ready) {
      // Повторный handshake / навигация инвалидируют инстанс (контракт 4.2).
      reset("повторный host.ready отклонён");
      return;
    }
    if (
      typeof data.instance_id !== "string" || !data.instance_id ||
      typeof data.nonce !== "string" || !data.nonce ||
      typeof data.digest !== "string" || !data.digest ||
      typeof data.app_handle !== "string" || !data.app_handle ||
      typeof data.protocol !== "string" || data.protocol !== PROTOCOL
    ) {
      reset("некорректный host.ready");
      return;
    }

    handshake.ready = true;
    handshake.instanceId = data.instance_id;
    handshake.nonce = data.nonce;
    handshake.appHandle = data.app_handle;
    // digest артефакта приходит от реестра через хост: самореференции в
    // манифесте/коде плагина нет (§5.1, запрет artifact_digest/signature).
    handshake.digest = data.digest;
    handshake.scope = data.scope && typeof data.scope === "object" ? data.scope : {};

    // plugin.ready: эхо identity + nonce, без токенов и без секретов.
    send({
      protocol: PROTOCOL,
      type: TYPE_PLUGIN_READY,
      instance_id: handshake.instanceId,
      nonce: handshake.nonce,
      plugin_id: PLUGIN_ID,
      version: PLUGIN_VERSION,
      digest: handshake.digest,
    });
    setState("Handshake отправлен, ожидание grant.ready…", false);
  }

  function onGrantReady(data) {
    if (!handshake.ready || data.instance_id !== handshake.instanceId || data.nonce !== handshake.nonce) {
      reset("grant.ready не привязан к инстансу");
      return;
    }
    if (!Array.isArray(data.capabilities)) {
      reset("grant.ready без capabilities");
      return;
    }
    handshake.capabilities = data.capabilities;
    handshake.granted = true;
    greetBtn.disabled = false;
    refreshBtn.disabled = false;
    setState("Плагин готов. Нажмите «Поздороваться».", false);
    // Сразу показываем список приветствий компании.
    rpcRequest("list", {});
  }

  function reset(reason) {
    handshake.ready = false;
    handshake.granted = false;
    pending.clear();
    greetBtn.disabled = true;
    refreshBtn.disabled = true;
    setState("Handshake сброшен: " + reason, true);
  }

  /* plugin.rpc.request: единственный путь к backend. Хост проксирует вызов в
     /pluginpage/hello_plugin/{company}/api/greetings (§7.1), перезаписывая
     доверенные заголовки. Плагин не знает и не задаёт X-Plugin-*/Cookie. */
  function rpcRequest(operation, params) {
    if (!handshake.granted) {
      setState("RPC недоступен: grant не получен", true);
      return;
    }
    var requestId = newId();
    var envelope = {
      protocol: PROTOCOL,
      kind: "request",
      type: TYPE_REQUEST,
      requestId: requestId,
      instanceId: handshake.instanceId,
      method: "capability.call",
      capability: { id: "hello.greetings", version: "1" },
      context: {
        cabinetId: handshake.scope.cabinetId || "",
        projectId: handshake.scope.projectId || "",
      },
      idempotencyKey: requestId,
      input: { method: operation, params: params },
    };
    if (!messageSizeOk(envelope)) {
      setState("RPC-пакет превышает лимит", true);
      return;
    }
    pending.set(requestId, operation);
    send(envelope);
  }

  function onRpcResponse(data) {
    // Тот же строгий origin/source для ОТВЕТА: поддельный результат игнорируем.
    if (data.type !== TYPE_RESPONSE || data.kind !== "response") return;
    if (data.instanceId !== handshake.instanceId) return;
    if (typeof data.requestId !== "string" || !pending.has(data.requestId)) return;
    // Важно: читаем операцию ДО удаления из pending, иначе ветвление рендера
    // потеряется (requestId — ключ, а не значение).
    var operation = pending.get(data.requestId);
    pending.delete(data.requestId);

    if (!data.ok) {
      var code = data.error && typeof data.error.code === "string" ? data.error.code : "REQUEST_FAILED";
      setState("Ошибка RPC: " + code, true);
      return;
    }
    var result = data.result;
    if (operation === "list" || Array.isArray(result)) {
      renderList(Array.isArray(result) ? result : []);
    } else if (result && Array.isArray(result.items)) {
      renderList(result.items);
    }
    setState("Готово.", false);
  }

  function renderList(items) {
    listEl.replaceChildren();
    if (!items.length) {
      var empty = document.createElement("li");
      empty.textContent = "Приветствий пока нет";
      listEl.appendChild(empty);
      return;
    }
    items.forEach(function (item) {
      var li = document.createElement("li");
      // textContent: никакой вставки HTML из ответа backend (XSS-безопасность).
      li.textContent = String(item.message !== undefined ? item.message : "");
      listEl.appendChild(li);
    });
  }

  window.addEventListener("message", function (event) {
    if (!isTrusted(event)) return; // fail-closed: чужой origin отбрасывается
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
    if (data.kind === "response" || data.type === TYPE_RESPONSE) {
      onRpcResponse(data);
      return;
    }
    // Неизвестный тип envelope — игнорируем (закрытый протокол).
  });

  greetBtn.addEventListener("click", function () {
    rpcRequest("greet", { message: "Привет от hello_plugin" });
  });
  refreshBtn.addEventListener("click", function () {
    rpcRequest("list", {});
  });

  if (!HOST_ORIGIN) {
    reset("data-host-origin не задан");
  }
})();
