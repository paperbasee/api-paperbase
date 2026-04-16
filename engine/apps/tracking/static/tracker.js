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
    var chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
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
      : (window.PAPERBASE_PUBLISHABLE_KEY || window.__PAPERBASE_API_KEY__ || "");
  }

  function readMetaContent(name) {
    try {
      var el = document && document.querySelector ? document.querySelector('meta[name="' + name + '"]') : null;
      if (!el) return null;
      var c = el.getAttribute("content");
      c = (c == null) ? "" : String(c);
      c = c.trim();
      return c || null;
    } catch (e) {
      return null;
    }
  }

  function resolvePixelIdFromGlobals() {
    try {
      var candidates = [
        (window.PAPERBASE_PIXEL_ID || ""),
        (window.__PAPERBASE_PIXEL_ID__ || ""),
        (window.PAPERBASE_META_PIXEL_ID || ""),
        (window.__PAPERBASE_META_PIXEL_ID__ || ""),
        readMetaContent("paperbase-pixel-id"),
        readMetaContent("paperbase-meta-pixel-id"),
        readMetaContent("meta-pixel-id"),
      ];
      for (var i = 0; i < candidates.length; i++) {
        var v = (candidates[i] == null) ? "" : String(candidates[i]).trim();
        if (v) return v;
      }
      return null;
    } catch (e) {
      return null;
    }
  }

  function guessPixelIdFromStorePublic(respJson) {
    try {
      if (!respJson || typeof respJson !== "object") return null;
      // If the store has already embedded it into storefront_public in some key,
      // it may be surfaced here without backend changes.
      var keys = [
        "pixel_id",
        "pixelId",
        "meta_pixel_id",
        "metaPixelId",
        "facebook_pixel_id",
        "facebookPixelId",
        "fb_pixel_id",
        "fbPixelId",
      ];
      for (var i = 0; i < keys.length; i++) {
        var v = respJson[keys[i]];
        if (typeof v === "string" && v.trim()) return v.trim();
      }
      // Nested common containers
      var nested = [respJson.meta, respJson.tracking, respJson.integrations, respJson.marketing];
      for (var j = 0; j < nested.length; j++) {
        var o = nested[j];
        if (!o || typeof o !== "object") continue;
        for (var k = 0; k < keys.length; k++) {
          var vv = o[keys[k]];
          if (typeof vv === "string" && vv.trim()) return vv.trim();
        }
      }
      return null;
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
          debugLog("[tracker] pixel init", { pixel_id: pixelId, source: "global/meta" });
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
        var pid = guessPixelIdFromStorePublic(json);
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

  function buildPayload(eventName, data) {
    var eventId = generateEventId(eventName);
    var value = data && data.value != null ? Number(data.value) : 0;
    if (!isFinite(value)) value = 0;
    var currency = (data && data.currency) ? String(data.currency) : "BDT";

    var contentIds = normalizeArray(data && data.content_ids);
    var contentType = (data && data.content_type) ? String(data.content_type) : "product";

    return {
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
      user_agent: userAgent(),
      extra: (data && data.extra && typeof data.extra === "object") ? data.extra : {},
    };
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
        try { window.__PAPERBASE_PIXEL_ID__ = this.config.pixelId; } catch (e) { /* ignore */ }
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
      var id = pickId(product);
      return sendEvent("ViewContent", {
        currency: pickCurrency(product, tracker.config.currency),
        content_type: "product",
        content_ids: id ? [id] : [],
        value: pickValue(product, 0),
        extra: { product: product || {} },
      });
    },

    addToCart: function (product) {
      var id = pickId(product);
      return sendEvent("AddToCart", {
        currency: pickCurrency(product, tracker.config.currency),
        content_type: "product",
        content_ids: id ? [id] : [],
        value: pickValue(product, 0),
        extra: { product: product || {} },
      });
    },

    initiateCheckout: function (cart) {
      var ids = [];
      try {
        if (cart && Array.isArray(cart.items)) {
          ids = cart.items.map(pickId).filter(Boolean);
        }
      } catch (e) {
        ids = [];
      }
      return sendEvent("InitiateCheckout", {
        currency: pickCurrency(cart, tracker.config.currency),
        content_type: "product",
        content_ids: ids,
        value: pickValue(cart, 0),
        extra: { cart: cart || {} },
      });
    },

    purchase: function (order) {
      var ids = [];
      try {
        if (order && Array.isArray(order.items)) {
          ids = order.items.map(pickId).filter(Boolean);
        }
      } catch (e) {
        ids = [];
      }
      return sendEvent("Purchase", {
        currency: pickCurrency(order, tracker.config.currency),
        content_type: "product",
        content_ids: ids,
        value: pickValue(order, 0),
        extra: { order: order || {} },
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

