/* collapsibles.js
 * Three improvements bundled together:
 *  1. Smooth height animation for details/summary collapsibles
 *  2. Persist each section's open/closed state in localStorage
 *  3. Pull-to-refresh gesture (at top of page)
 *  4. Scroll-to-top button
 */
(function () {
  'use strict';

  /* ═══════════════════════════════════════════════════════════════════════
     1.  COLLAPSIBLE PERSIST + ANIMATE
     ═══════════════════════════════════════════════════════════════════════ */

  var LS_COLLAPSE_PREFIX = 'coll_';

  function collapseKey(el) {
    if (el.id) return el.id;
    var block = el.closest && el.closest('.section-block');
    if (block && block.dataset.sectionId) return block.dataset.sectionId;
    var summary = el.querySelector('summary');
    return summary
      ? 'coll_' + summary.textContent.trim().replace(/\s+/g, '_').slice(0, 24)
      : null;
  }

  function getCollapseState(key) {
    try { return localStorage.getItem(LS_COLLAPSE_PREFIX + key); }
    catch (e) { return null; }
  }

  function saveCollapseState(key, state) {
    try { localStorage.setItem(LS_COLLAPSE_PREFIX + key, state); }
    catch (e) {}
  }

  function openPanel(details, key, animate) {
    var body = details.querySelector('.collapsible-module-body');
    if (!body) { details.open = true; if (key) saveCollapseState(key, 'open'); return; }

    details.open = true;
    if (key) saveCollapseState(key, 'open');

    if (!animate) return;

    body.style.overflow = 'hidden';
    body.style.height = '0px';
    body.style.opacity = '0';

    requestAnimationFrame(function () {
      var h = body.scrollHeight;
      body.style.transition = 'height 0.28s cubic-bezier(0.4,0,0.2,1), opacity 0.22s ease';
      body.style.height = h + 'px';
      body.style.opacity = '1';

      body.addEventListener('transitionend', function fin(e) {
        if (e.propertyName !== 'height') return;
        body.removeEventListener('transitionend', fin);
        body.style.height = '';
        body.style.overflow = '';
        body.style.transition = '';
        body.style.opacity = '';
      });
    });
  }

  function closePanel(details, key, animate) {
    var body = details.querySelector('.collapsible-module-body');
    if (!body) { details.open = false; if (key) saveCollapseState(key, 'closed'); return; }

    if (key) saveCollapseState(key, 'closed');

    if (!animate) { details.open = false; return; }

    var h = body.getBoundingClientRect().height;
    body.style.overflow = 'hidden';
    body.style.height = h + 'px';

    requestAnimationFrame(function () {
      body.style.transition = 'height 0.2s cubic-bezier(0.4,0,0.2,1), opacity 0.15s ease';
      body.style.height = '0px';
      body.style.opacity = '0';

      body.addEventListener('transitionend', function fin(e) {
        if (e.propertyName !== 'height') return;
        body.removeEventListener('transitionend', fin);
        details.open = false;
        body.style.height = '';
        body.style.overflow = '';
        body.style.transition = '';
        body.style.opacity = '';
      });
    });
  }

  function initCollapsibles() {
    var panels = document.querySelectorAll('details.collapsible-module');

    panels.forEach(function (details) {
      var key = collapseKey(details);

      // Restore saved state (no animation on page load — just snap)
      if (key) {
        var saved = getCollapseState(key);
        if (saved === 'open' && !details.open) {
          details.open = true;
        } else if (saved === 'closed' && details.open) {
          details.open = false;
        }
      }

      // Intercept summary click for animated toggle
      var summary = details.querySelector('summary');
      if (!summary) return;

      summary.addEventListener('click', function (e) {
        // Don't intercept clicks in layout edit mode (overlay handles it)
        if (document.body.classList.contains('layout-edit-mode')) return;

        e.preventDefault();
        if (details.open) {
          closePanel(details, key, true);
        } else {
          openPanel(details, key, true);
        }
      });
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     2.  PULL-TO-REFRESH
     ═══════════════════════════════════════════════════════════════════════ */

  var PTR_THRESHOLD = 72;   // px of pull needed to trigger
  var PTR_MAX_PULL = 110;   // max visual translation
  var ptrStartY = null;
  var ptrDeltaY = 0;
  var ptrTriggered = false;
  var ptrEl = null;
  var mainEl = null;

  function initPullToRefresh() {
    // Only on the forecast page (main element exists)
    mainEl = document.getElementById('main-content');
    if (!mainEl) return;

    // Create indicator element
    ptrEl = document.createElement('div');
    ptrEl.id = 'ptr-indicator';
    ptrEl.className = 'ptr-indicator';
    ptrEl.setAttribute('aria-hidden', 'true');
    ptrEl.innerHTML =
      '<div class="ptr-spinner"></div>' +
      '<span class="ptr-label">Pull to refresh</span>';
    document.body.insertBefore(ptrEl, mainEl);

    document.addEventListener('touchstart', onPTRTouchStart, { passive: true });
    document.addEventListener('touchmove', onPTRTouchMove, { passive: false });
    document.addEventListener('touchend', onPTRTouchEnd, { passive: true });
    document.addEventListener('touchcancel', onPTRTouchEnd, { passive: true });
  }

  function onPTRTouchStart(e) {
    // Only start if at very top of page
    if (window.scrollY <= 2 && e.touches.length === 1) {
      ptrStartY = e.touches[0].clientY;
      ptrDeltaY = 0;
      ptrTriggered = false;
    } else {
      ptrStartY = null;
    }
  }

  function onPTRTouchMove(e) {
    if (ptrStartY === null) return;
    if (window.scrollY > 2) { ptrStartY = null; return; }

    var dy = e.touches[0].clientY - ptrStartY;
    if (dy <= 0) { ptrStartY = null; return; }

    ptrDeltaY = dy;
    // Prevent native scroll / overscroll while pulling
    if (dy > 6) e.preventDefault();

    var pull = Math.min(dy, PTR_MAX_PULL);
    var progress = dy / PTR_THRESHOLD;

    // Translate main content down
    mainEl.style.transition = 'none';
    mainEl.style.transform = 'translateY(' + Math.round(pull * 0.55) + 'px)';

    // Update indicator
    ptrEl.style.opacity = String(Math.min(progress, 1));
    ptrEl.style.transform = 'translateY(' + Math.round(pull * 0.7 - 28) + 'px)';

    if (dy >= PTR_THRESHOLD && !ptrTriggered) {
      ptrTriggered = true;
      ptrEl.querySelector('.ptr-label').textContent = 'Release to refresh';
      ptrEl.classList.add('ptr-ready');
      try { navigator.vibrate && navigator.vibrate(12); } catch (_) {}
    } else if (dy < PTR_THRESHOLD) {
      ptrTriggered = false;
      ptrEl.querySelector('.ptr-label').textContent = 'Pull to refresh';
      ptrEl.classList.remove('ptr-ready');
    }
  }

  function onPTRTouchEnd() {
    if (ptrStartY === null) return;
    ptrStartY = null;

    // Snap back
    mainEl.style.transition = 'transform 0.3s cubic-bezier(0.4,0,0.2,1)';
    mainEl.style.transform = '';
    ptrEl.style.opacity = '0';
    ptrEl.style.transform = '';
    ptrEl.classList.remove('ptr-ready');

    mainEl.addEventListener('transitionend', function cleanup() {
      mainEl.removeEventListener('transitionend', cleanup);
      mainEl.style.transition = '';
    });

    if (ptrTriggered) {
      ptrTriggered = false;
      // Brief delay so user sees the snap-back before refresh
      setTimeout(triggerRefresh, 250);
    }
  }

  function triggerRefresh() {
    var form = document.getElementById('refresh-form');
    if (form) {
      // Use existing handler if available (shows spinner)
      if (typeof window.handleRefreshSubmit === 'function') {
        window.handleRefreshSubmit(form);
      }
      // Submit via requestSubmit so submit event fires
      form.requestSubmit ? form.requestSubmit() : form.submit();
    } else {
      window.location.reload();
    }
  }

  /* ═══════════════════════════════════════════════════════════════════════
     3.  SCROLL-TO-TOP BUTTON
     ═══════════════════════════════════════════════════════════════════════ */

  var SHOW_THRESHOLD = 380;
  var scrollTopBtn = null;
  var sttVisible = false;
  var sttTicking = false;

  function initScrollToTop() {
    scrollTopBtn = document.createElement('button');
    scrollTopBtn.id = 'scroll-top-btn';
    scrollTopBtn.className = 'scroll-top-btn';
    scrollTopBtn.type = 'button';
    scrollTopBtn.setAttribute('aria-label', 'Back to top');
    scrollTopBtn.innerHTML =
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true">' +
        '<polyline points="18 15 12 9 6 15"/>' +
      '</svg>';
    scrollTopBtn.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    document.body.appendChild(scrollTopBtn);

    window.addEventListener('scroll', onScrollForSTT, { passive: true });
  }

  function onScrollForSTT() {
    if (sttTicking) return;
    sttTicking = true;
    requestAnimationFrame(function () {
      var y = window.scrollY || window.pageYOffset;
      var shouldShow = y > SHOW_THRESHOLD;
      if (shouldShow !== sttVisible) {
        sttVisible = shouldShow;
        scrollTopBtn.classList.toggle('scroll-top-btn--visible', shouldShow);
      }
      sttTicking = false;
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     BOOT
     ═══════════════════════════════════════════════════════════════════════ */

  function init() {
    initCollapsibles();
    initPullToRefresh();
    initScrollToTop();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
