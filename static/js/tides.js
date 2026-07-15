/* tides.js
 * Makes the tide chart interactive:
 *  1. Drag/hover-to-scrub — inspect height at any time along the curve
 *  2. Live "now" indicator — the current-position dot moves in real time
 *  3. Tap a High/Low card to jump to (and pulse) its marker on the chart
 *
 * Data global set by the inline script block before this file loads:
 *   window._TIDE_CURVE = [{hour, height, x, y}, ...]  (dense samples, x/y in SVG user-space)
 */
(function () {
  'use strict';

  var CURVE = window._TIDE_CURVE || [];
  var svg = document.getElementById('tide-chart-svg');
  var wrap = document.getElementById('tide-svg-wrap');
  if (!svg || !wrap || CURVE.length < 2) return;

  var scrubLine = svg.querySelector('.tide-scrub-line');
  var scrubDot = svg.querySelector('.tide-scrub-dot');
  var tooltip = document.getElementById('tide-scrub-tooltip');
  var tooltipTime = document.getElementById('tide-scrub-time');
  var tooltipHeight = document.getElementById('tide-scrub-height');

  var nowLine = svg.querySelector('.tide-now-line');
  var nowDot = svg.querySelector('.tide-now-dot');
  var nowDotCenter = svg.querySelector('.tide-now-dot-center');
  var nowHalo = svg.querySelector('.tide-now-halo');
  var liveHeightEl = document.getElementById('tide-live-height');

  var markerEls = svg.querySelectorAll('.tide-chart-marker');
  var cardEls = document.querySelectorAll('.tide-point[data-hour]');

  /* ── helpers ─────────────────────────────────────────────────────────── */

  function formatHourLabel(hr) {
    var totalMin = Math.round(hr * 60);
    var h24 = Math.floor(totalMin / 60) % 24;
    if (h24 < 0) h24 += 24;
    var m = totalMin % 60;
    if (m < 0) m += 60;
    var ampm = h24 >= 12 ? 'PM' : 'AM';
    var h12 = h24 % 12;
    if (h12 === 0) h12 = 12;
    return h12 + ':' + (m < 10 ? '0' : '') + m + ' ' + ampm;
  }

  // Binary search CURVE (sorted ascending) for the sample bracketing `key`
  // along `field` ('x' or 'hour'), returning an interpolated {hour,height,x,y}.
  function sampleAt(field, value) {
    var n = CURVE.length;
    if (value <= CURVE[0][field]) return CURVE[0];
    if (value >= CURVE[n - 1][field]) return CURVE[n - 1];
    var lo = 0, hi = n - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (CURVE[mid][field] < value) lo = mid; else hi = mid;
    }
    var a = CURVE[lo], b = CURVE[hi];
    var span = b[field] - a[field];
    var t = span !== 0 ? (value - a[field]) / span : 0;
    return {
      hour: a.hour + t * (b.hour - a.hour),
      height: a.height + t * (b.height - a.height),
      x: a.x + t * (b.x - a.x),
      y: a.y + t * (b.y - a.y),
    };
  }

  function trendNear(hr) {
    var eps = 0.05;
    var before = sampleAt('hour', hr - eps).height;
    var after = sampleAt('hour', hr + eps).height;
    if (after - before > 0.01) return 'rising';
    if (before - after > 0.01) return 'falling';
    return 'slack';
  }

  function clientXToSvgX(clientX) {
    var vb = svg.viewBox.baseVal;
    var rect = svg.getBoundingClientRect();
    if (!rect.width) return vb.x;
    var frac = (clientX - rect.left) / rect.width;
    frac = Math.max(0, Math.min(1, frac));
    return vb.x + frac * vb.width;
  }

  function positionTooltip(svgX, svgY) {
    var vb = svg.viewBox.baseVal;
    var rect = svg.getBoundingClientRect();
    var wrapRect = wrap.getBoundingClientRect();
    var scaleX = rect.width / vb.width;
    var scaleY = rect.height / vb.height;
    var left = rect.left - wrapRect.left + (svgX - vb.x) * scaleX;
    var top = rect.top - wrapRect.top + (svgY - vb.y) * scaleY;
    // Keep the bubble from spilling past the card edges.
    var minLeft = 28, maxLeft = wrapRect.width - 28;
    tooltip.style.left = Math.max(minLeft, Math.min(maxLeft, left)) + 'px';
    tooltip.style.top = top + 'px';
    // Near a peak there's no headroom above the dot for the bubble (the wrap
    // clips overflow) — flip it below instead of letting it get cut off.
    tooltip.classList.toggle('tide-scrub-tooltip--below', svgY < 40);
  }

  /* ── scrub (drag / hover) ────────────────────────────────────────────── */

  function showScrubAtSvgX(svgX) {
    var s = sampleAt('x', svgX);
    scrubLine.setAttribute('x1', s.x);
    scrubLine.setAttribute('x2', s.x);
    scrubLine.style.display = '';
    scrubDot.setAttribute('cx', s.x);
    scrubDot.setAttribute('cy', s.y);
    scrubDot.style.display = '';
    tooltipTime.textContent = formatHourLabel(s.hour);
    tooltipHeight.textContent = s.height.toFixed(1) + ' ft · ' + trendNear(s.hour);
    positionTooltip(s.x, s.y);
    tooltip.hidden = false;
  }

  function hideScrub() {
    scrubLine.style.display = 'none';
    scrubDot.style.display = 'none';
    tooltip.hidden = true;
  }

  var dragging = false;
  var hideTimer = null;

  function onPointerDown(e) {
    dragging = true;
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    try { svg.setPointerCapture(e.pointerId); } catch (err) {}
    showScrubAtSvgX(clientXToSvgX(e.clientX));
  }

  function onPointerMove(e) {
    if (dragging || e.pointerType === 'mouse') {
      showScrubAtSvgX(clientXToSvgX(e.clientX));
    }
  }

  function onPointerUp(e) {
    dragging = false;
    try { svg.releasePointerCapture(e.pointerId); } catch (err) {}
    if (e.pointerType !== 'mouse') {
      // Leave the reading up briefly so a tap can actually be read.
      hideTimer = setTimeout(hideScrub, 2200);
    }
  }

  function onPointerLeave(e) {
    if (e.pointerType === 'mouse' && !dragging) hideScrub();
  }

  svg.style.touchAction = 'pan-y';
  svg.addEventListener('pointerdown', onPointerDown);
  svg.addEventListener('pointermove', onPointerMove);
  svg.addEventListener('pointerup', onPointerUp);
  svg.addEventListener('pointercancel', onPointerUp);
  svg.addEventListener('pointerleave', onPointerLeave);

  /* ── live "now" indicator ────────────────────────────────────────────── */

  function updateNow() {
    var now = new Date();
    var hr = now.getHours() + now.getMinutes() / 60;
    var first = CURVE[0].hour, last = CURVE[CURVE.length - 1].hour;
    var inRange = hr >= first && hr <= last;

    if (nowLine) nowLine.style.display = inRange ? '' : 'none';
    if (nowDot) nowDot.style.display = inRange ? '' : 'none';
    if (nowDotCenter) nowDotCenter.style.display = inRange ? '' : 'none';
    if (nowHalo) nowHalo.style.display = inRange ? '' : 'none';

    if (!inRange) {
      if (liveHeightEl) liveHeightEl.hidden = true;
      return;
    }

    var s = sampleAt('hour', hr);
    if (nowLine) { nowLine.setAttribute('x1', s.x); nowLine.setAttribute('x2', s.x); }
    if (nowDot) { nowDot.setAttribute('cx', s.x); nowDot.setAttribute('cy', s.y); }
    if (nowDotCenter) { nowDotCenter.setAttribute('cx', s.x); nowDotCenter.setAttribute('cy', s.y); }
    if (nowHalo) { nowHalo.setAttribute('cx', s.x); nowHalo.setAttribute('cy', s.y); }

    if (liveHeightEl) {
      liveHeightEl.textContent = '• ' + s.height.toFixed(1) + ' ft right now';
      liveHeightEl.hidden = false;
    }
  }

  updateNow();
  setInterval(updateNow, 60000);

  /* ── tap a High/Low card to jump to it on the chart ─────────────────── */

  function pulseMarker(hr) {
    var best = null, bestDiff = Infinity;
    markerEls.forEach(function (m) {
      var mh = parseFloat(m.getAttribute('data-hour'));
      var diff = Math.abs(mh - hr);
      if (diff < bestDiff) { bestDiff = diff; best = m; }
    });
    if (!best) return;
    best.classList.remove('tide-chart-marker--pulse');
    void best.getBoundingClientRect(); // restart the CSS animation
    best.classList.add('tide-chart-marker--pulse');
    showScrubAtSvgX(parseFloat(best.getAttribute('cx')));
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(hideScrub, 2600);
  }

  cardEls.forEach(function (card) {
    card.addEventListener('click', function () {
      var hr = parseFloat(card.getAttribute('data-hour'));
      if (isNaN(hr)) return;
      wrap.scrollIntoView({ behavior: 'smooth', block: 'center' });
      pulseMarker(hr);
    });
  });

  /* ── dawn/dusk feeding-window shading ───────────────────────────────── */
  // window._SUN_TIMES is "6:32 AM / 7:45 PM" (or '' if unavailable).
  // Mirrors the dawn/dusk window definition used for best-time scoring
  // elsewhere in the app: 30min before to 60min after sunrise, and
  // 60min before to 30min after sunset.

  function parseTimeToHour(str) {
    var m = /^\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*$/i.exec(str || '');
    if (!m) return null;
    var h = parseInt(m[1], 10), min = parseInt(m[2], 10);
    var ampm = m[3].toUpperCase();
    if (ampm === 'AM' && h === 12) h = 0;
    if (ampm === 'PM' && h !== 12) h += 12;
    return h + min / 60;
  }

  function paintBand(el, startHour, endHour) {
    var first = CURVE[0].hour, last = CURVE[CURVE.length - 1].hour;
    var s = Math.max(startHour, first), e = Math.min(endHour, last);
    if (e <= s) { el.style.display = 'none'; return false; }
    var x1 = sampleAt('hour', s).x, x2 = sampleAt('hour', e).x;
    el.setAttribute('x', x1);
    el.setAttribute('width', Math.max(0, x2 - x1));
    el.style.display = '';
    return true;
  }

  (function initDaynightBands() {
    var dawnEl = svg.querySelector('.tide-daynight-band--dawn');
    var duskEl = svg.querySelector('.tide-daynight-band--dusk');
    var hint = document.getElementById('tide-daynight-hint');
    if (!dawnEl || !duskEl) return;

    var parts = (window._SUN_TIMES || '').split('/');
    if (parts.length !== 2) return;
    var sunrise = parseTimeToHour(parts[0]);
    var sunset = parseTimeToHour(parts[1]);
    if (sunrise === null || sunset === null) return;

    var dawnShown = paintBand(dawnEl, sunrise - 0.5, sunrise + 1.0);
    var duskShown = paintBand(duskEl, sunset - 1.0, sunset + 0.5);
    if (hint) hint.hidden = !(dawnShown || duskShown);
  }());
}());
