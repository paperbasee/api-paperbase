/* tracker.js - Paperbase Meta tracking SDK (Pixel + server forwarding)
 *
 * Requirements:
 * - tracker.js is the only source of event_id
 * - fires Meta Pixel with eventID = event_id for dedup
 * - forwards the same event_id to Django /tracking/event
 */

(function () {
  "use strict";

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
    var cryptoObj = null;
    try {
      cryptoObj = (window.crypto || window.msCrypto) || null;
    } catch (e) {
      cryptoObj = null;
    }

    if (cryptoObj && typeof cryptoObj.getRandomValues === "function" && typeof Uint8Array !== "undefined") {
      try {
        var bytes = new Uint8Array(len);
        cryptoObj.getRandomValues(bytes);
        for (var j = 0; j < len; j++) {
          out += chars.charAt(bytes[j] % chars.length);
        }
        return out;
      } catch (e2) {
        // fall back to Math.random
      }
    }

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

  function fbqTrack(eventName, customData, eventId) {
    try {
      if (typeof window.fbq !== "function") return;
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
    var endpoint = (tracker.config && tracker.config.endpoint) ? tracker.config.endpoint : "/tracking/event";
    var apiKey = (tracker.config && tracker.config.apiKey) ? tracker.config.apiKey : (window.PAPERBASE_PUBLISHABLE_KEY || window.__PAPERBASE_API_KEY__ || "");
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
      endpoint: "/tracking/event",
      apiKey: "",
      currency: "BDT",
      debug: false,
    },

    init: function (opts) {
      opts = opts || {};
      if (opts.endpoint) this.config.endpoint = String(opts.endpoint);
      if (opts.apiKey) this.config.apiKey = String(opts.apiKey);
      if (opts.currency) this.config.currency = String(opts.currency);
      if (typeof opts.debug === "boolean") this.config.debug = opts.debug;
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
    tracker.pageView();
  } catch (e) {
    // ignore
  }
})();

