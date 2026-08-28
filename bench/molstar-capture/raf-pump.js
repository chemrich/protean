/**
 * Keeps Mol*'s render loop running while the viewer tab is hidden.
 *
 * Browsers pause requestAnimationFrame entirely in background tabs. Mol* drives
 * both its time-sliced renderable commit loop and a bare
 * `await new Promise(r => requestAnimationFrame(r))` (in the structure preset's
 * camera reset) off rAF, so a structure load dispatched to a hidden tab hangs
 * forever instead of failing — the bridge just times out.
 *
 * This wraps requestAnimationFrame so that, while the document is hidden,
 * callbacks are driven by us instead of the paused native clock:
 *
 *   - idle:  setTimeout, which background tabs clamp to ~1s. That is a slow but
 *            live heartbeat, enough to keep Mol*'s animation loop from parking
 *            itself in the frozen native queue (once a callback is handed to the
 *            paused native rAF it is unreachable, so the loop must never get
 *            there while hidden).
 *   - turbo: a MessageChannel, whose delivery is not clamped in background tabs.
 *            dispatch.ts turns this on only for the duration of a
 *            render-dependent action, so a hidden idle tab costs ~1 tick/sec.
 *
 * This file MUST be loaded before molstar.js: Mol* captures a reference to
 * window.requestAnimationFrame at module-eval time for its animation loop, and a
 * patch installed afterwards would not be picked up by it.
 */
(function () {
  'use strict';

  var nativeRequest = window.requestAnimationFrame.bind(window);
  var nativeCancel = window.cancelAnimationFrame.bind(window);

  // Background tabs clamp timers to ~1s; asking for a frame interval just means
  // "as soon as you'll let me".
  var IDLE_MS = 16;

  var channel = new MessageChannel();
  var pending = new Map();
  var nextHandle = 1;
  var turbo = false;
  var armed = false;
  var nativeHandle;

  function flush() {
    armed = false;
    nativeHandle = undefined;
    if (pending.size === 0) return;
    var due = pending;
    pending = new Map();
    var now = performance.now();
    due.forEach(function (callback) {
      try {
        callback(now);
      } catch (err) {
        console.error('protean: rAF callback failed', err);
      }
    });
    // Mol*'s loop re-registers itself from inside its own callback.
    if (pending.size > 0) arm();
  }

  function arm() {
    if (armed) return;
    armed = true;
    if (document.visibilityState === 'visible') nativeHandle = nativeRequest(flush);
    else if (turbo) channel.port2.postMessage(0);
    else setTimeout(flush, IDLE_MS);
  }

  channel.port1.onmessage = flush;

  // Every frame request is queued here rather than passed through, so that the
  // clock backing it can be swapped when visibility changes. Handing a callback
  // straight to the native rAF while visible would strand it the moment the tab
  // is hidden: the native queue freezes with the callback inside, and since
  // Mol*'s loop only re-requests from within its own callback, the loop would
  // never run again — which is exactly how a hidden tab ends up with a built but
  // never-committed scene.
  window.requestAnimationFrame = function (callback) {
    // Negative handles mark frames we own; the native ones are positive.
    var handle = -nextHandle++;
    pending.set(handle, callback);
    arm();
    return handle;
  };

  window.cancelAnimationFrame = function (handle) {
    if (handle < 0) pending.delete(handle);
    else nativeCancel(handle);
  };

  document.addEventListener('visibilitychange', function () {
    // Re-arm on the clock that matches the new state; anything queued against
    // the old one is either frozen (hidden) or needlessly slow (visible).
    if (nativeHandle !== undefined) {
      nativeCancel(nativeHandle);
      nativeHandle = undefined;
    }
    armed = false;
    if (pending.size > 0) arm();
  });

  window.__protean = window.__protean || {};

  /** Switch the hidden-tab pump between the idle heartbeat and full speed. */
  window.__protean.setTurbo = function (on) {
    on = !!on;
    if (on === turbo) return;
    turbo = on;
    // Don't wait out an already-scheduled (clamped) idle timer — that could be a
    // second or more away, and under Chrome's intensive throttling far longer.
    // A stale flush firing later is harmless: it finds the queue already drained.
    if (turbo && pending.size > 0) {
      armed = true;
      channel.port2.postMessage(0);
    }
  };

  window.__protean.pumpState = function () {
    return { turbo: turbo, queued: pending.size };
  };
})();
