/**
 * Times Mol* image-pass captures, on whatever Mol* version sits next to this
 * file as `molstar.js`.
 *
 * Written for backlog 40: upgrading Mol* 4.18.0 -> 5.11.0 took protean's browser
 * CI job from 23m12s to 48m18s with one *fewer* test, and the cost is localised
 * to the capture path. This page exists so that one capture can be measured on
 * every release between those two, on one runner, back to back — which is the
 * only comparison that survives a runner variance of about 40%.
 *
 * Deliberately NOT protean's viewer. `viewer/src` now imports Mol* 5 internals
 * (`mol-canvas3d/passes/illumination`, the trackball `axis`, bloom's
 * `transparency`), so building it against 4.18 would not even compile. This page
 * loads the prebuilt UMD bundle every release ships at `build/viewer/molstar.js`
 * and touches nothing that changed between them — verified by reading
 * `mol-plugin/util/viewport-screenshot.js` in both, where `createPass` and the
 * `imagePass` getter are byte-identical.
 *
 * What is timed is `ImagePass.getImageData` — render plus `readPixels` — not
 * `render` alone. `render` returns once the GL commands are issued; `readPixels`
 * is what forces them to have finished. Timing `render` by itself would measure
 * command submission and report a regression as absent.
 *
 * The runtime handed to the pass is a stub. `ImagePass.render` touches it only
 * inside the illumination branch, which is off here (`illumination.enabled`
 * defaults to false in both versions), so nothing is skipped by stubbing it —
 * and it keeps Mol*'s task scheduler, which yields to rAF, out of the number.
 *
 * The result is POSTed back to the server that served this page. There is no
 * CDP in the loop: a page that can `fetch` its own origin can report its own
 * result, and that removes a WebSocket, a dependency and a class of timeout from
 * the harness.
 */
(function () {
  'use strict';

  var params = new URLSearchParams(location.search);

  function num(name, dflt) {
    var v = parseFloat(params.get(name));
    return isNaN(v) ? dflt : v;
  }

  var WIDTH = num('width', 800);
  var HEIGHT = num('height', 600);
  var REPEATS = num('repeats', 8);
  var WARMUP = num('warmup', 2);
  var LEVELS = (params.get('levels') || '4,1')
    .split(',')
    .map(function (s) { return parseInt(s, 10); })
    .filter(function (n) { return !isNaN(n); });
  var FULL_PATH_REPEATS = num('fullPath', 2);
  var STRUCTURE = params.get('structure') || './1ubq.pdb';
  var LABEL = params.get('label') || 'unknown';

  var log = [];
  function note(m) {
    log.push(m);
    console.log('[bench] ' + m);
    // Also down the wire, unawaited. A run of nineteen releases is most of an
    // hour, and a page that reports only at the end is a black box for all of
    // it — worse, a version that hangs and one that is merely slow look
    // identical until the timeout fires. Losing a progress line costs nothing;
    // the result comes back on its own channel.
    try {
      fetch('/__bench_progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: LABEL, note: m }),
      }).catch(function () {});
    } catch (e) {
      /* a progress line is never worth failing a measurement over */
    }
  }

  // See the header: the illumination branch is the only user of `runtime`.
  var runtime = {
    update: function () { return Promise.resolve(); },
    shouldUpdate: false,
  };

  function sleep(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }

  function stats(xs) {
    if (!xs.length) return null;
    var s = xs.slice().sort(function (a, b) { return a - b; });
    function q(p) {
      var i = (s.length - 1) * p;
      var lo = Math.floor(i);
      var hi = Math.ceil(i);
      return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (i - lo);
    }
    var mean = s.reduce(function (a, b) { return a + b; }, 0) / s.length;
    return {
      n: s.length,
      min: s[0],
      p25: q(0.25),
      median: q(0.5),
      p75: q(0.75),
      max: s[s.length - 1],
      mean: mean,
    };
  }

  /** Read a value that may not exist on this Mol* version, without throwing. */
  function safe(fn, dflt) {
    try {
      var v = fn();
      return v === undefined ? (dflt === undefined ? null : dflt) : v;
    } catch (e) {
      return dflt === undefined ? null : dflt;
    }
  }

  /**
   * Wait for the canvas to stop redrawing.
   *
   * A fixed sleep would either be too short on a slow runner or waste time on a
   * fast one, and the difference lands straight in the first timed capture.
   * `didDraw` is the canvas's own signal; the fallback is there because this
   * page has to survive every release from 4.18 to 5.11 and an observable that
   * moved would otherwise take the whole run down.
   */
  async function settle(plugin, budgetMs, quietMs) {
    var start = performance.now();
    var last = start;
    var sub = safe(function () {
      return plugin.canvas3d.didDraw.subscribe(function () {
        last = performance.now();
      });
    });
    try {
      while (performance.now() - start < budgetMs) {
        await sleep(100);
        if (performance.now() - last > quietMs) break;
      }
    } finally {
      if (sub && sub.unsubscribe) sub.unsubscribe();
    }
    return Math.round(performance.now() - start);
  }

  /**
   * Everything about the environment that could explain a difference between two
   * versions without any of them being a Mol* regression.
   *
   * The float-texture pair is the important one: the screenshot helper picks
   * `sampleLevel: colorBufferFloat && textureFloat ? 4 : 2`, so a renderer that
   * gained float support would silently double the sample count and look exactly
   * like a per-sample regression.
   */
  function environment(plugin) {
    var canvas3d = plugin.canvas3d;
    var webgl = canvas3d.webgl;
    var gl = webgl.gl;
    var dbg = safe(function () { return gl.getExtension('WEBGL_debug_renderer_info'); });
    var ext = webgl.extensions || {};
    var extNames = [
      'colorBufferFloat', 'textureFloat', 'textureFloatLinear',
      'colorBufferHalfFloat', 'textureHalfFloat', 'textureHalfFloatLinear',
      'depthTexture', 'drawBuffers', 'drawBuffersIndexed', 'fragDepth',
      'shaderTextureLod', 'sRGB', 'textureFilterAnisotropic',
      'parallelShaderCompile', 'noNonInstancedActiveAttribs',
      'blendMinMax', 'vertexArrayObject', 'disjointTimerQuery',
      'multiDraw', 'drawInstancedBaseVertexBaseInstance',
    ];
    var extensions = {};
    extNames.forEach(function (n) {
      extensions[n] = safe(function () { return !!ext[n]; }, null);
    });
    return {
      molstarVersion: safe(function () { return molstar.version; }),
      userAgent: navigator.userAgent,
      devicePixelRatio: window.devicePixelRatio,
      isWebGL2: safe(function () { return webgl.isWebGL2; }),
      pixelRatio: safe(function () { return webgl.pixelRatio; }),
      glVendor: safe(function () {
        return dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
      }),
      glRenderer: safe(function () {
        return dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      }),
      glVersion: safe(function () { return gl.getParameter(gl.VERSION); }),
      maxSamples: safe(function () { return gl.getParameter(gl.MAX_SAMPLES); }),
      extensions: extensions,
      // The whole canvas3d parameter tree. Diffing this between two versions is
      // the direct test of backlog 40's second candidate — "a default that
      // changed on our side" — and it costs one JSON stringify.
      canvas3dProps: safe(function () { return JSON.parse(JSON.stringify(canvas3d.props)); }),
    };
  }

  /**
   * The camera's own numbers, because a changed *fit* would look exactly like a
   * per-sample regression and is not one.
   *
   * Under a software rasteriser the cost of a capture is very nearly the number
   * of covered pixels, so a release that pulled the camera in — 5.4.1 put atom
   * VDW radii into the structure's bounding sphere, 5.10.0 reworked focus and
   * easing — would make every capture more expensive without a shader changing
   * at all. Recording the fit is what lets that be ruled in or out instead of
   * assumed.
   */
  function cameraState(plugin) {
    var cam = safe(function () { return plugin.canvas3d.camera.state; });
    if (!cam) return null;
    function vec(v) { return v ? Array.prototype.slice.call(v) : null; }
    var position = vec(cam.position);
    var target = vec(cam.target);
    var distance = null;
    if (position && target && position.length === target.length) {
      var sum = 0;
      for (var i = 0; i < position.length; i++) {
        sum += (position[i] - target[i]) * (position[i] - target[i]);
      }
      distance = Math.sqrt(sum);
    }
    return {
      mode: cam.mode,
      fov: cam.fov,
      near: cam.near,
      far: cam.far,
      radius: cam.radius,
      radiusMax: cam.radiusMax,
      position: position,
      target: target,
      up: vec(cam.up),
      distanceToTarget: distance,
      boundingSphereRadius: safe(function () {
        return plugin.canvas3d.boundingSphere.radius;
      }),
    };
  }

  /**
   * The picture itself: how much of the frame the molecule covers, and a
   * thumbnail small enough to travel in the result.
   *
   * Coverage is the number that closes the framing question numerically. The
   * thumbnail is there because this project's answer to "is it the same thing?"
   * is to look at it, and a table of milliseconds cannot be looked at.
   *
   * The corner pixel is taken as the ground. A fitted view never reaches the
   * corner, and a benchmark that guessed the background colour instead would
   * report full coverage the day a default changed.
   */
  async function describePicture(pass) {
    var img = await pass.getImageData(runtime, WIDTH, HEIGHT);
    var d = img.data;
    var r0 = d[0], g0 = d[1], b0 = d[2];
    var covered = 0;
    for (var i = 0; i < d.length; i += 4) {
      if (Math.abs(d[i] - r0) + Math.abs(d[i + 1] - g0) + Math.abs(d[i + 2] - b0) > 12) {
        covered++;
      }
    }
    var full = document.createElement('canvas');
    full.width = img.width;
    full.height = img.height;
    full.getContext('2d').putImageData(img, 0, 0);
    var thumbWidth = Math.min(240, img.width);
    var thumb = document.createElement('canvas');
    thumb.width = thumbWidth;
    thumb.height = Math.max(1, Math.round((thumbWidth * img.height) / img.width));
    thumb.getContext('2d').drawImage(full, 0, 0, thumb.width, thumb.height);
    return {
      width: img.width,
      height: img.height,
      background: [r0, g0, b0],
      coverage: covered / (img.width * img.height),
      thumbnail: thumb.toDataURL('image/png'),
    };
  }

  /** A proxy for how much work the scene is, so "twice as slow" can be told
   *  apart from "twice as much geometry". */
  function sceneWork(plugin, pass) {
    var webgl = plugin.canvas3d.webgl;
    return {
      stats: safe(function () { return JSON.parse(JSON.stringify(webgl.stats)); }),
      transparencyMode: safe(function () { return pass.drawPass.transparencyMode; }),
      imagePassMiB: safe(function () { return pass.getByteCount() / 1024 / 1024; }),
      passWidth: safe(function () { return pass.width; }),
      passHeight: safe(function () { return pass.height; }),
    };
  }

  async function timeLevel(pass, level, out) {
    // `setProps` is `Object.assign` over the props object, so a partial
    // multiSample group would drop `reuseOcclusion` and change what is measured.
    // Spread what is already there.
    pass.setProps({
      multiSample: Object.assign({}, pass.props.multiSample, {
        mode: level > 0 ? 'on' : 'off',
        sampleLevel: level,
      }),
    });
    var applied = JSON.parse(JSON.stringify(pass.props.multiSample));
    note('level ' + level + ': warming up (' + WARMUP + ')');
    for (var w = 0; w < WARMUP; w++) {
      await pass.getImageData(runtime, WIDTH, HEIGHT);
    }
    var times = [];
    for (var i = 0; i < REPEATS; i++) {
      var t = performance.now();
      await pass.getImageData(runtime, WIDTH, HEIGHT);
      times.push(performance.now() - t);
      // Yield, so a long synchronous run cannot be mistaken for a hang by the
      // page's own watchdog and so the browser gets to do its housekeeping
      // between captures rather than inside one.
      await sleep(0);
    }
    note('level ' + level + ': median ' + stats(times).median.toFixed(1) + ' ms');
    out.push({ sampleLevel: level, applied: applied, timesMs: times, stats: stats(times) });
  }

  async function main() {
    var t0 = performance.now();
    var pdb = await (await fetch(STRUCTURE)).text();
    note('structure fetched: ' + pdb.length + ' bytes');

    var tViewer = performance.now();
    var viewer = await molstar.Viewer.create('app', {
      layoutIsExpanded: false,
      layoutShowControls: false,
      layoutShowRemoteState: false,
      layoutShowSequence: false,
      layoutShowLog: false,
      layoutShowLeftPanel: false,
      viewportShowExpand: false,
      viewportShowSelectionMode: false,
      viewportShowAnimation: false,
      // Never reached — nothing here loads over the network — but left explicit
      // so a future edit cannot quietly acquire a network dependency.
      pdbProvider: 'rcsb',
      emdbProvider: 'rcsb',
    });
    var viewerMs = performance.now() - tViewer;
    var plugin = viewer.plugin;
    note('viewer up in ' + Math.round(viewerMs) + ' ms');

    var tLoad = performance.now();
    await viewer.loadStructureFromData(pdb, 'pdb');
    var loadMs = performance.now() - tLoad;
    var settleMs = await settle(plugin, 20000, 700);
    note('structure loaded in ' + Math.round(loadMs) + ' ms, settled in ' + settleMs + ' ms');

    var env = environment(plugin);

    var helper = plugin.helpers.viewportScreenshot;
    if (!helper) throw new Error('this Mol* build has no viewport screenshot helper');
    helper.behaviors.values.next(
      Object.assign({}, helper.values, {
        resolution: { name: 'custom', params: { width: WIDTH, height: HEIGHT } },
        format: { name: 'png', params: {} },
      })
    );
    helper.resetCrop();

    // Taken ONCE. The getter re-applies cameraHelper, transparentBackground,
    // postprocessing, marking and illumination on every access and deliberately
    // leaves multiSample alone — so holding the pass is what makes a sample
    // level set here survive, and re-reading `helper.imagePass` between levels
    // would not undo it but would quietly change four other things mid-run.
    var pass = helper.imagePass;
    if (!pass) throw new Error('Mol* built no image pass');

    // What the helper chose for itself, before we override anything. This is the
    // number the real CI job runs at, and it is a capability check — so if it
    // differs between two versions on the same renderer, that alone is the
    // finding.
    var helperChoice = JSON.parse(JSON.stringify(pass.props.multiSample));
    note('helper chose sampleLevel ' + helperChoice.sampleLevel + ' mode ' + helperChoice.mode);

    var levels = [];
    for (var i = 0; i < LEVELS.length; i++) {
      await timeLevel(pass, LEVELS[i], levels);
    }

    // The full protean path, for a handful of repeats: through the plugin's task
    // scheduler and out as a PNG data URI. Not the headline number — the PNG
    // encode is browser work that dilutes the ratio — but it is what
    // `snapshot` actually calls, so it says whether this benchmark is measuring
    // the same thing the job spends its time on.
    // Back to the level the helper picked for itself — the one CI actually runs
    // at — for the picture and the full-path timings.
    pass.setProps({
      multiSample: Object.assign({}, pass.props.multiSample, helperChoice),
    });
    var picture = await describePicture(pass);
    note('coverage ' + (picture.coverage * 100).toFixed(2) + '% of the frame');

    var fullPath = [];
    if (FULL_PATH_REPEATS > 0) {
      for (var j = 0; j < FULL_PATH_REPEATS + 1; j++) {
        var tf = performance.now();
        var uri = await helper.getImageDataUri();
        var ms = performance.now() - tf;
        if (j > 0) fullPath.push(ms); // first one discarded, as for the levels
        if (j === FULL_PATH_REPEATS) note('data uri bytes: ' + uri.length);
      }
    }

    var work = sceneWork(plugin, pass);

    return {
      label: LABEL,
      ok: true,
      params: {
        width: WIDTH, height: HEIGHT, repeats: REPEATS, warmup: WARMUP,
        levels: LEVELS, fullPathRepeats: FULL_PATH_REPEATS, structure: STRUCTURE,
      },
      environment: env,
      helperChosenMultiSample: helperChoice,
      camera: cameraState(plugin),
      picture: picture,
      setup: {
        viewerCreateMs: Math.round(viewerMs),
        structureLoadMs: Math.round(loadMs),
        settleMs: settleMs,
        totalMs: Math.round(performance.now() - t0),
      },
      levels: levels,
      fullPath: { timesMs: fullPath, stats: stats(fullPath) },
      work: work,
      log: log,
    };
  }

  function report(payload) {
    window.__bench = payload;
    // Beacon first: it survives the page being torn down. The fetch is what
    // actually carries the body reliably, and the server accepts either.
    try {
      fetch('/__bench_result', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      console.error('[bench] could not report', e);
    }
  }

  main().then(report, function (err) {
    // A failure has to come back over the same channel as a success, or the
    // harness cannot tell "this version broke" from "the page never finished"
    // and would sit out its whole timeout to say the same thing.
    report({
      label: LABEL,
      ok: false,
      error: String((err && err.stack) || err),
      log: log,
    });
  });
})();
