/* tracker.js - Paperbase Meta tracking SDK (Pixel + server forwarding)
 *
 * Requirements:
 * - tracker.js is the only source of event_id
 * - fires Meta Pixel with eventID = event_id for dedup
 * - forwards the same event_id to Django /tracking/event
 */

(function () {
  "use strict";

  var DEFAULT_API_ORIGIN = "https://api.paperbase.me";
  var DEFAULT_INGEST_ENDPOINT = DEFAULT_API_ORIGIN + "/tracking/event";

  function isDebug() {
    try {
      return !!(window.tracker && window.tracker.config && window.tracker.config.debug);
    } catch (e) {
      return false;
    }
  }

  function debugLog() {
    try {
      if (!isDebug()) return;
      if (typeof console === "undefined" || typeof console.log !== "function") return;
      console.log.apply(console, arguments);
    } catch (e) {
      // never throw
    }
  }

  function nowUnix() {
    return Math.floor(Date.now() / 1000);
  }

  function randomString(len) {
    var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    try {
      var crypto = window.crypto || window.msCrypto;
      if (crypto && crypto.getRandomValues) {
        var arr = new Uint8Array(len);
        crypto.getRandomValues(arr);
        return Array.prototype.map.call(arr, function (byte) {
          return chars[byte % chars.length];
        }).join("");
      }
    } catch (e) {
      // Fall through to Math.random fallback
    }
    // Fallback for environments without Web Crypto API
    var out = "";
    for (var i = 0; i < len; i++) out += chars.charAt(Math.floor(Math.random() * chars.length));
    return out;
  }

  function generateEventId(eventName) {
    var ts = Date.now();
    return "event_" + String(eventName) + "_" + String(ts) + "_" + randomString(12);
  }

  function readCookie(name) {
    try {
      var cookies = document.cookie ? document.cookie.split(";") : [];
      for (var i = 0; i < cookies.length; i++) {
        var c = cookies[i].trim();
        if (!c) continue;
        if (c.indexOf(name + "=") === 0) return decodeURIComponent(c.substring(name.length + 1));
      }
      return null;
    } catch (e) {
      return null;
    }
  }

  function getFbp() {
    return readCookie("_fbp");
  }

  function getFbc() {
    return readCookie("_fbc");
  }

  function getTtp() {
    return readCookie("_ttp");
  }

  function getTtclid() {
    var fromCookie = readCookie("ttclid");
    if (fromCookie) return fromCookie;
    try {
      var params = new URLSearchParams(window.location.search);
      return params.get("ttclid") || null;
    } catch (e) {
      return null;
    }
  }

  function currentUrl() {
    try {
      return String(window.location.href || "");
    } catch (e) {
      return "";
    }
  }

  function userAgent() {
    try {
      return String(navigator.userAgent || "");
    } catch (e) {
      return "";
    }
  }

  function isAbsoluteUrl(url) {
    var s = String(url || "");
    return /^https?:\/\//i.test(s);
  }

  function normalizeEndpoint(url) {
    var s = String(url || "").trim();
    if (!s) return DEFAULT_INGEST_ENDPOINT;
    if (isAbsoluteUrl(s)) return s;
    if (s.indexOf("/") === 0) return DEFAULT_API_ORIGIN + s;
    // Any other non-absolute value is unsafe; fall back to default.
    return DEFAULT_INGEST_ENDPOINT;
  }

  function apiKeyFromConfig() {
    return (tracker.config && tracker.config.apiKey)
      ? tracker.config.apiKey
      : (window.PAPERBASE_PUBLISHABLE_KEY || "");
  }

  function resolvePixelIdFromGlobals() {
    try {
      var v = String(window.PAPERBASE_PIXEL_ID || "").trim();
      return v || null;
    } catch (e) {
      return null;
    }
  }

  function pixelIdFromStorePublic(respJson) {
    try {
      if (!respJson || typeof respJson !== "object") return null;
      var v = String(respJson.pixel_id || "").trim();
      return v || null;
    } catch (e) {
      return null;
    }
  }

  function pixelBootstrapSnippet() {
    // Meta-recommended bootstrap (verbatim structure).
    !function (f, b, e, v, n, t, s) {
      if (f.fbq) return;
      n = f.fbq = function () {
        n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
      };
      if (!f._fbq) f._fbq = n;
      n.push = n;
      n.loaded = !0;
      n.version = "2.0";
      n.queue = [];
      t = b.createElement(e);
      t.async = !0;
      t.src = v;
      s = b.getElementsByTagName(e)[0];
      s.parentNode.insertBefore(t, s);
    }(window, document, "script", "https://connect.facebook.net/en_US/fbevents.js");
  }

  var _pixelState = {
    initStarted: false,
    initDone: false,
    pixelId: null,
    queue: [],
  };

  function enqueuePixelTrack(eventName, customData, eventId) {
    _pixelState.queue.push({ eventName: eventName, customData: customData || {}, eventId: eventId });
  }

  function flushPixelQueue() {
    if (typeof window.fbq !== "function") return;
    while (_pixelState.queue.length) {
      var ev = _pixelState.queue.shift();
      try {
        window.fbq("track", ev.eventName, ev.customData || {}, { eventID: ev.eventId });
      } catch (e) {
        // ignore
      }
    }
  }

  function ensurePixelReady(cb) {
    cb = (typeof cb === "function") ? cb : function () { };
    if (_pixelState.initDone && typeof window.fbq === "function") {
      cb();
      return;
    }
    if (_pixelState.initStarted) {
      // We'll call cb after init completes by polling briefly.
      var tries = 0;
      var iv = setInterval(function () {
        tries++;
        if (_pixelState.initDone && typeof window.fbq === "function") {
          clearInterval(iv);
          cb();
        } else if (tries >= 40) { // ~2s max
          clearInterval(iv);
          cb();
        }
      }, 50);
      return;
    }

    _pixelState.initStarted = true;

    // 1) Resolve pixelId
    var pixelId = resolvePixelIdFromGlobals();
    if (pixelId) {
      _pixelState.pixelId = pixelId;
      try {
        if (typeof window.fbq !== "function") pixelBootstrapSnippet();
        if (typeof window.fbq === "function") {
          window.fbq("init", pixelId);
          _pixelState.initDone = true;
              debugLog("[tracker] pixel init", { pixel_id: pixelId, source: "global" });
          flushPixelQueue();
        }
      } catch (e) {
        // ignore; still allow cb
      }
      cb();
      return;
    }

    // 2) Best-effort fetch store public config to discover pixelId (API key required)
    var apiKey = apiKeyFromConfig();
    var endpoint = normalizeEndpoint(tracker.config && tracker.config.endpoint);
    var apiOrigin = DEFAULT_API_ORIGIN;
    try {
      var m = String(endpoint || "").match(/^(https?:\/\/[^/]+)/i);
      if (m && m[1]) apiOrigin = m[1];
    } catch (e2) { /* ignore */ }

    if (!apiKey) {
      debugLog("[tracker] pixel init skipped (missing apiKey)");
      cb();
      return;
    }

    try {
      fetch(apiOrigin + "/api/v1/store/public/", {
        method: "GET",
        headers: { "Authorization": "Bearer " + String(apiKey) },
        credentials: "omit",
        mode: "cors",
      }).then(function (resp) {
        if (!resp || !resp.ok) return null;
        return resp.json ? resp.json() : null;
      }).then(function (json) {
        var pid = pixelIdFromStorePublic(json);
        if (pid) {
          _pixelState.pixelId = pid;
          try {
            if (typeof window.fbq !== "function") pixelBootstrapSnippet();
            if (typeof window.fbq === "function") {
              window.fbq("init", pid);
              _pixelState.initDone = true;
              debugLog("[tracker] pixel init", { pixel_id: pid, source: "store_public" });
              flushPixelQueue();
            }
          } catch (e3) {
            // ignore
          }
        } else {
          debugLog("[tracker] pixel id not found in store public config");
        }
        cb();
      }).catch(function () {
        cb();
      });
    } catch (e4) {
      cb();
    }
  }

  function fbqTrack(eventName, customData, eventId) {
    try {
      // Ensure Pixel is bootstrapped before tracking. If not ready, queue.
      if (typeof window.fbq !== "function" || !_pixelState.initDone) {
        enqueuePixelTrack(eventName, customData, eventId);
        ensurePixelReady(function () {
          try { flushPixelQueue(); } catch (e2) { /* ignore */ }
        });
        return;
      }
      window.fbq("track", eventName, customData || {}, { eventID: eventId });
    } catch (e) {
      // never throw
    }
  }

  function fetchJson(url, body, headers) {
    return fetch(url, {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, headers || {}),
      body: JSON.stringify(body || {}),
      keepalive: true,
      credentials: "omit",
      mode: "cors",
    });
  }

  function normalizeArray(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value.map(function (x) { return String(x); }).filter(Boolean);
    return [String(value)];
  }

  function pickCurrency(obj, fallback) {
    var c = (obj && (obj.currency || obj.cur)) || fallback;
    c = String(c || "").trim();
    return c || "BDT";
  }

  function pickValue(obj, fallback) {
    var v = (obj && (obj.value != null ? obj.value : (obj.total != null ? obj.total : obj.price))) || fallback;
    var n = Number(v);
    return isFinite(n) ? n : 0;
  }

  function pickId(obj) {
    return (obj && (obj.public_id || obj.publicId || obj.id || obj.sku)) ? String(obj.public_id || obj.publicId || obj.id || obj.sku) : "";
  }

  var PII_FIELDS = [
    "email", "phone", "first_name", "last_name", "external_id",
    "city", "state", "zip_code", "country",
  ];

  function buildPayload(eventName, data) {
    var eventId = generateEventId(eventName);
    var value = data && data.value != null ? Number(data.value) : 0;
    if (!isFinite(value)) value = 0;
    var currency = (data && data.currency) ? String(data.currency) : "BDT";

    var contentIds = normalizeArray(data && data.content_ids);
    var contentType = (data && data.content_type) ? String(data.content_type) : "product";

    var payload = {
      event_name: eventName,
      event_id: eventId,
      event_time: data && data.event_time ? Number(data.event_time) : nowUnix(),
      event_source_url: data && data.event_source_url ? String(data.event_source_url) : currentUrl(),
      value: value,
      currency: currency,
      content_type: contentType,
      content_ids: contentIds,
      fbp: getFbp(),
      fbc: getFbc(),
      ttp: getTtp(),
      ttclid: getTtclid(),
      user_agent: userAgent(),
      extra: (data && data.extra && typeof data.extra === "object") ? data.extra : {},
    };

    // Forward PII fields for server-side hashing — never sent to Meta directly.
    for (var i = 0; i < PII_FIELDS.length; i++) {
      var f = PII_FIELDS[i];
      if (data && data[f]) payload[f] = String(data[f]);
    }

    // Forward structured cart/order fields.
    if (data && Array.isArray(data.items) && data.items.length) {
      payload.items = data.items;
    }
    if (data && data.order_id) {
      payload.order_id = String(data.order_id);
    }

    return payload;
  }

  function sendEvent(eventName, data, pixelCustomData) {
    var payload = buildPayload(eventName, data || {});

    // Pixel first (browser)
    fbqTrack(eventName, pixelCustomData || {
      value: payload.value,
      currency: payload.currency,
      content_ids: payload.content_ids,
      content_type: payload.content_type,
    }, payload.event_id);

    // Server forwarding
    var endpoint = normalizeEndpoint((tracker.config && tracker.config.endpoint) ? tracker.config.endpoint : DEFAULT_INGEST_ENDPOINT);
    var apiKey = apiKeyFromConfig();
    var headers = {};
    if (apiKey) headers["Authorization"] = "Bearer " + String(apiKey);

    function attemptPost(isRetry) {
      try {
        var p = fetchJson(endpoint, payload, headers);
        if (p && typeof p.then === "function") {
          p.then(function (resp) {
            if (!resp) return;
            debugLog("[tracker] backend", isRetry ? "retry" : "first", {
              event_name: payload.event_name,
              event_id: payload.event_id,
              status: resp.status,
              ok: resp.ok
            });
            if (!resp.ok && !isRetry) {
              setTimeout(function () { attemptPost(true); }, 2000);
            }
          }).catch(function (err) {
            debugLog("[tracker] backend error", isRetry ? "retry" : "first", {
              event_name: payload.event_name,
              event_id: payload.event_id,
              error: String(err || "")
            });
            if (!isRetry) {
              setTimeout(function () { attemptPost(true); }, 2000);
            }
          });
        }
      } catch (e) {
        debugLog("[tracker] backend exception", {
          event_name: payload.event_name,
          event_id: payload.event_id,
          error: String(e || "")
        });
        if (!isRetry) {
          try { setTimeout(function () { attemptPost(true); }, 2000); } catch (e2) { /* ignore */ }
        }
      }
    }

    debugLog("[tracker] event", { event_name: payload.event_name, event_id: payload.event_id });
    attemptPost(false);

    return payload.event_id;
  }

  var tracker = {
    config: {
      endpoint: DEFAULT_INGEST_ENDPOINT,
      apiKey: "",
      currency: "BDT",
      debug: false,
      pixelId: "",
    },

    init: function (opts) {
      opts = opts || {};
      if (opts.endpoint) this.config.endpoint = String(opts.endpoint);
      if (opts.apiKey) this.config.apiKey = String(opts.apiKey);
      if (opts.currency) this.config.currency = String(opts.currency);
      if (typeof opts.debug === "boolean") this.config.debug = opts.debug;
      if (opts.pixelId) this.config.pixelId = String(opts.pixelId);

      // Normalize endpoint immediately to guarantee absolute URL usage.
      this.config.endpoint = normalizeEndpoint(this.config.endpoint);

      // Allow pixelId override via init (still optional).
      if (this.config.pixelId && !resolvePixelIdFromGlobals()) {
        try { window.PAPERBASE_PIXEL_ID = this.config.pixelId; } catch (e) { /* ignore */ }
      }

      // Ensure Pixel is initialized as early as possible.
      try { ensurePixelReady(function () { }); } catch (e2) { /* ignore */ }
      return this;
    },

    pageView: function () {
      return sendEvent("PageView", {
        currency: tracker.config.currency || "BDT",
        content_type: "product",
        content_ids: [],
        value: 0,
      }, {});
    },

    viewContent: function (product) {
      // product may include an optional `customer` object with PII fields.
      // PII fields (email, phone, etc.) are sent over HTTPS to YOUR backend
      // only. They are hashed (SHA-256) server-side before forwarding to Meta.
      // They are never stored in your database. Never log these fields.
      var id = pickId(product);
      var customer = (product && product.customer) || {};
      return sendEvent("ViewContent", {
        currency: pickCurrency(product, tracker.config.currency),
        content_type: "product",
        content_ids: id ? [id] : [],
        value: pickValue(product, 0),
        extra: { product: product || {} },
        email: customer.email || "",
        phone: customer.phone || "",
        first_name: customer.first_name || "",
        last_name: customer.last_name || "",
        external_id: customer.external_id || "",
        city: customer.city || "",
        state: customer.state || "",
        zip_code: customer.zip_code || "",
        country: customer.country || "",
      });
    },

    addToCart: function (product) {
      // product may include an optional `customer` object with PII fields.
      // PII fields (email, phone, etc.) are sent over HTTPS to YOUR backend
      // only. They are hashed (SHA-256) server-side before forwarding to Meta.
      // They are never stored in your database. Never log these fields.
      var id = pickId(product);
      var customer = (product && product.customer) || {};
      return sendEvent("AddToCart", {
        currency: pickCurrency(product, tracker.config.currency),
        content_type: "product",
        content_ids: id ? [id] : [],
        value: pickValue(product, 0),
        extra: { product: product || {} },
        email: customer.email || "",
        phone: customer.phone || "",
        first_name: customer.first_name || "",
        last_name: customer.last_name || "",
        external_id: customer.external_id || "",
        city: customer.city || "",
        state: customer.state || "",
        zip_code: customer.zip_code || "",
        country: customer.country || "",
      });
    },

    initiateCheckout: function (cart) {
      // cart may include an optional `customer` object with PII fields.
      // PII fields (email, phone, etc.) are sent over HTTPS to YOUR backend
      // only. They are hashed (SHA-256) server-side before forwarding to Meta.
      // They are never stored in your database. Never log these fields.
      var ids = [];
      try {
        if (cart && Array.isArray(cart.items)) {
          ids = cart.items.map(pickId).filter(Boolean);
        }
      } catch (e) {
        ids = [];
      }
      var customer = (cart && cart.customer) || {};
      return sendEvent("InitiateCheckout", {
        currency: pickCurrency(cart, tracker.config.currency),
        content_type: "product",
        content_ids: ids,
        items: (cart && cart.items) || [],
        value: pickValue(cart, 0),
        extra: { cart: cart || {} },
        email: customer.email || "",
        phone: customer.phone || "",
        first_name: customer.first_name || "",
        last_name: customer.last_name || "",
        external_id: customer.external_id || "",
        city: customer.city || "",
        state: customer.state || "",
        zip_code: customer.zip_code || "",
        country: customer.country || "",
      });
    },

    purchase: function (order) {
      // order may include a `customer` object with PII fields.
      // PII fields (email, phone, etc.) are sent over HTTPS to YOUR backend
      // only. They are hashed (SHA-256) server-side before forwarding to Meta.
      // They are never stored in your database. Never log these fields.
      var ids = [];
      try {
        if (order && Array.isArray(order.items)) {
          ids = order.items.map(pickId).filter(Boolean);
        }
      } catch (e) {
        ids = [];
      }
      var customer = (order && order.customer) || {};
      return sendEvent("Purchase", {
        currency: pickCurrency(order, tracker.config.currency),
        content_type: "product",
        content_ids: ids,
        items: (order && order.items) || [],
        order_id: (order && (order.order_id || order.id)) ? String(order.order_id || order.id) : "",
        value: pickValue(order, 0),
        extra: { order: order || {} },
        email: customer.email || "",
        phone: customer.phone || "",
        first_name: customer.first_name || "",
        last_name: customer.last_name || "",
        external_id: customer.external_id || (order && order.user_id) || "",
        city: customer.city || "",
        state: customer.state || "",
        zip_code: customer.zip_code || "",
        country: customer.country || "",
      });
    },
  };

  window.tracker = tracker;

  // Auto PageView on load
  try {
    // Ensure endpoint is always absolute even without explicit init.
    tracker.config.endpoint = normalizeEndpoint(tracker.config.endpoint || DEFAULT_INGEST_ENDPOINT);

    // Best-effort bootstrap Pixel first, then fire PageView.
    ensurePixelReady(function () {
      try { tracker.pageView(); } catch (e2) { /* ignore */ }
    });
  } catch (e) {
    // ignore
  }
})();

