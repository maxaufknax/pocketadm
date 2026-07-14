/* PocketADM native bridge — progressive enhancement for the mobile shell.

   Inside the Capacitor app this wires the native keyboard, status bar and
   haptics (the plugin proxies live on window.Capacitor.Plugins — the runtime
   is injected by the shell, nothing to bundle). In a plain browser/PWA it
   degrades gracefully: visualViewport/focus tracking stands in for keyboard
   events and navigator.vibrate (Android) stands in for the Taptic engine.

   app.js only ever talks to window.PocketNative — never to Capacitor. */
(function () {
  "use strict";

  const C = window.Capacitor;
  const isNative = !!(C && typeof C.isNativePlatform === "function" && C.isNativePlatform());
  const platform = isNative ? C.getPlatform() : "web";
  const P = (isNative && C.Plugins) || {};
  const coarse = matchMedia("(pointer: coarse)").matches;
  const root = document.documentElement;

  const N = {
    isNative,
    platform,
    kbOpen: false,
    haptic,
    themeChanged,
  };
  window.PocketNative = N;

  /* ------------------------------------------------------------ haptics
     One vocabulary, tuned per platform:
       tick    – barely-there selection click (any control press)
       light   – soft impact (tab switch, toggles, send)
       medium  – firm impact (approvals, stop)
       success/warning/error – notification patterns (agent finished /
                               checkpoint / failed)                        */

  const hapticsOn = () => localStorage.getItem("pocketadm_haptics") !== "off";
  const VIB = { tick: 8, light: 14, medium: 24, heavy: 34,
    success: [12, 60, 18], warning: [18, 70, 18], error: [24, 60, 24, 60, 28] };
  let lastTick = 0;

  // plugin calls return promises; feedback must never break (or spam) the app
  const quiet = (fn) => { try { const p = fn(); if (p && p.catch) p.catch(() => {}); } catch (e) {} };

  function haptic(kind = "tick") {
    if (!hapticsOn() || !coarse) return;
    if (kind === "tick" || kind === "light") {
      const now = Date.now();               // don't buzz like a phone on silent
      if (now - lastTick < 50) return;
      lastTick = now;
    }
    const H = P.Haptics;
    if (H) {
      if (kind === "tick") quiet(() => H.selectionChanged());
      else if (kind === "light" || kind === "medium" || kind === "heavy")
        quiet(() => H.impact({ style: kind.toUpperCase() }));
      else quiet(() => H.notification({ type: kind.toUpperCase() }));
    } else if (navigator.vibrate && VIB[kind]) {
      quiet(() => navigator.vibrate(VIB[kind]));
    }
  }

  // arm the iOS selection generator once so the first tick isn't swallowed
  if (P.Haptics && P.Haptics.selectionStart) quiet(() => P.Haptics.selectionStart());

  // a subtle press-tick on every enabled control, fired on touch-down so it
  // feels attached to the finger (like UIKit / Material touch feedback)
  const TICKABLE = "button, [role=button], .tab, .stat, .suggest, .chat-open, " +
    ".attach-item, .pick-row, .connect-srv, .theme-card, select, summary";
  document.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse") return;
    const t = e.target && e.target.closest ? e.target.closest(TICKABLE) : null;
    if (!t || t.disabled || t.classList.contains("no-haptic")) return;
    haptic(t.classList.contains("tab") ? "light" : "tick");
  }, { capture: true, passive: true });

  // switches/checkboxes confirm on state change, not on press
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t && t.type === "checkbox") haptic("light");
  }, { capture: true, passive: true });

  /* ----------------------------------------------------------- keyboard
     body.kb-open + --kb let the CSS ride the keyboard: the tab bar slides
     away so the composer sits directly on top of it.
       native: the webview itself resizes (Keyboard resize "native"), so
               --kb stays 0 — only the class toggles chrome.
       web:    on iOS Safari the layout viewport does NOT shrink, so --kb
               carries the overlap height (visualViewport); on Android the
               viewport resizes itself and --kb computes to 0.             */

  let kbTimer = null;

  function measureTabbar() {
    const tb = document.querySelector(".tabbar");
    if (tb && tb.offsetHeight) root.style.setProperty("--tabbar-real", tb.offsetHeight + "px");
  }

  function setKb(open, height) {
    clearTimeout(kbTimer);
    if (open) {
      measureTabbar();                       // measure while it's still visible
      N.kbOpen = true;
      root.style.setProperty("--kb", Math.max(0, Math.round(height || 0)) + "px");
      document.body.classList.add("kb-open");
      fireKb(true, height || 0);
    } else {
      // debounced: hopping between two fields must not flash the tab bar
      kbTimer = setTimeout(() => {
        N.kbOpen = false;
        root.style.setProperty("--kb", "0px");
        document.body.classList.remove("kb-open");
        fireKb(false, 0);
      }, 120);
    }
  }
  function fireKb(open, height) {
    window.dispatchEvent(new CustomEvent("pocketadm-kb", { detail: { open, height } }));
  }

  if (isNative && P.Keyboard) {
    const K = P.Keyboard;
    K.addListener("keyboardWillShow", () => setKb(true, 0));
    K.addListener("keyboardWillHide", () => setKb(false));
    // no grey "< > Done" bar over the keyboard — this is an app, not a form
    if (K.setAccessoryBarVisible) quiet(() => K.setAccessoryBarVisible({ isVisible: false }));
  } else if (coarse) {
    let focused = false;
    const editable = (el) => !!el && (el.tagName === "TEXTAREA" || el.isContentEditable ||
      (el.tagName === "INPUT" && !/^(checkbox|radio|button|submit|file|range|color)$/.test(el.type || "")));
    const vv = window.visualViewport;
    const overlap = () => {
      if (!vv) return 0;
      const h = window.innerHeight - vv.height - vv.offsetTop;
      return h > 80 ? h : 0;
    };
    const update = () => {
      const kb = overlap();
      if (focused || kb > 0) setKb(true, kb);
      else setKb(false);
    };
    document.addEventListener("focusin", (e) => { if (editable(e.target)) { focused = true; update(); } });
    document.addEventListener("focusout", (e) => { if (editable(e.target)) { focused = false; update(); } });
    if (vv) vv.addEventListener("resize", update);
  }

  /* ------------------------------------- status bar & keyboard appearance
     Called by app.js whenever the theme changes (incl. first paint).      */

  function themeChanged(mode /* "dark" | "light" */, bg) {
    if (!isNative) return;
    const dark = mode !== "light";
    if (P.StatusBar) {
      // Style DARK = dark background = light text
      quiet(() => P.StatusBar.setStyle({ style: dark ? "DARK" : "LIGHT" }));
      if (platform === "android" && bg) quiet(() => P.StatusBar.setBackgroundColor({ color: bg }));
    }
    if (P.Keyboard && P.Keyboard.setStyle)
      quiet(() => P.Keyboard.setStyle({ style: dark ? "DARK" : "LIGHT" }));
  }

  /* --------------------------------------------- app lifecycle (native) */

  if (isNative && P.App) {
    // Android hardware/gesture back: let the app handle it (close modal,
    // go home) — minimize only when it says there's nothing left to close.
    P.App.addListener("backButton", () => {
      const unhandled = window.dispatchEvent(
        new CustomEvent("pocketadm-back", { cancelable: true }));
      if (unhandled && P.App.minimizeApp) P.App.minimizeApp().catch(() => {});
    });
    // foregrounded again — nudge the app to reconnect its sockets
    P.App.addListener("appStateChange", (s) => {
      if (s && s.isActive) window.dispatchEvent(new Event("pocketadm-resume"));
    });
  }

  window.addEventListener("resize", () => { if (!N.kbOpen) measureTabbar(); });
  measureTabbar();
})();
