/* Layout editor — drag-to-reorder + show/hide page sections
 * Mobile-first: pointer events, auto-scroll, haptic, FAB scroll-aware
 */
(function () {
  'use strict';

  var LS_PREFIX = 'page_layout_';
  var container, lsKey;
  var originalOrder = [];   // section-id order snapshot for Cancel
  var originalHidden = {};  // hidden state snapshot for Cancel
  var dragState = null;
  var scrollRaf = null;     // auto-scroll rAF handle

  /* ── Init ────────────────────────────────────────────────────────────── */

  function init() {
    container = document.getElementById('sections-container');
    if (!container) return;

    lsKey = LS_PREFIX + (window.CURRENT_LOC_ID || 'default');

    applyStoredLayout();
    createFAB();
    initFABScrollBehavior();
  }

  /* ── Stored layout ──────────────────────────────────────────────────── */

  function getBlocks() {
    return Array.from(container.querySelectorAll(':scope > .section-block'));
  }

  function readStoredLayout() {
    try {
      var raw = localStorage.getItem(lsKey);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function applyStoredLayout() {
    var layout = readStoredLayout();
    if (!layout || !Array.isArray(layout) || !layout.length) return;

    layout.forEach(function (item) {
      var block = container.querySelector('[data-section-id="' + item.id + '"]');
      if (!block) return;
      if (item.hidden && !block.dataset.required) {
        block.setAttribute('data-hidden', '1');
        block.style.display = 'none';
      } else {
        block.removeAttribute('data-hidden');
        block.style.display = '';
      }
    });

    // Reorder: known IDs first, remaining stay at end
    layout.map(function (i) { return i.id; }).forEach(function (id) {
      var block = container.querySelector('[data-section-id="' + id + '"]');
      if (block) container.appendChild(block);
    });
  }

  function persistLayout() {
    var layout = getBlocks().map(function (block) {
      return {
        id: block.dataset.sectionId,
        hidden: block.getAttribute('data-hidden') === '1'
      };
    });

    try { localStorage.setItem(lsKey, JSON.stringify(layout)); } catch (e) {}

    if (window.LOGGED_IN) {
      fetch('/api/v1/page-layout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrf(),
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify({ layout: layout })
      }).catch(function () {});
    }
  }

  function resetLayout() {
    try { localStorage.removeItem(lsKey); } catch (e) {}
    if (window.LOGGED_IN) {
      fetch('/api/v1/page-layout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrf(),
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify({ layout: [] })
      }).catch(function () {});
    }
  }

  /* ── FAB + scroll-aware hide ────────────────────────────────────────── */

  function createFAB() {
    var fab = document.createElement('button');
    fab.id = 'layout-edit-fab';
    fab.className = 'layout-edit-fab';
    fab.type = 'button';
    fab.setAttribute('aria-label', 'Edit page layout');
    fab.innerHTML =
      '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
        '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>' +
        '<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>' +
      '</svg>' +
      '<span class="fab-label">Edit Layout</span>';
    fab.addEventListener('click', enterEditMode);
    document.body.appendChild(fab);
  }

  function initFABScrollBehavior() {
    var lastY = 0;
    var ticking = false;
    var fab = document.getElementById('layout-edit-fab');
    if (!fab) return;

    window.addEventListener('scroll', function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        var y = window.scrollY || window.pageYOffset;
        if (y > lastY + 8 && y > 120) {
          fab.classList.add('fab-hidden');
        } else if (y < lastY - 8 || y < 60) {
          fab.classList.remove('fab-hidden');
        }
        lastY = y;
        ticking = false;
      });
    }, { passive: true });
  }

  /* ── Enter edit mode ─────────────────────────────────────────────────── */

  function enterEditMode() {
    document.body.classList.add('layout-edit-mode');

    var blocks = getBlocks();
    // Snapshot for Cancel
    originalOrder = blocks.map(function (b) { return b.dataset.sectionId; });
    originalHidden = {};
    blocks.forEach(function (b) {
      originalHidden[b.dataset.sectionId] = b.getAttribute('data-hidden') === '1';
      // Reveal hidden sections so they can be dragged in edit mode
      if (b.getAttribute('data-hidden') === '1') {
        b.style.display = '';
        b.classList.add('section-edit-was-hidden');
      }
    });

    blocks.forEach(addEditOverlay);

    // Build toolbar
    var toolbar = document.createElement('div');
    toolbar.id = 'layout-edit-toolbar';
    toolbar.className = 'layout-edit-toolbar';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.innerHTML =
      '<button type="button" class="layout-edit-btn layout-edit-reset" id="layout-edit-reset" title="Reset to default order">' +
        '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>' +
        ' Reset' +
      '</button>' +
      '<span class="layout-edit-hint">Drag to reorder</span>' +
      '<div class="layout-edit-actions">' +
        '<button type="button" class="layout-edit-btn layout-edit-cancel" id="layout-edit-cancel">Cancel</button>' +
        '<button type="button" class="layout-edit-btn layout-edit-save" id="layout-edit-save">Save</button>' +
      '</div>';
    document.body.appendChild(toolbar);

    document.getElementById('layout-edit-reset').addEventListener('click', confirmReset);
    document.getElementById('layout-edit-cancel').addEventListener('click', cancelEditMode);
    document.getElementById('layout-edit-save').addEventListener('click', commitEditMode);

    // Scroll to top so user sees all sections
    window.scrollTo({ top: 0, behavior: 'smooth' });

    var fab = document.getElementById('layout-edit-fab');
    if (fab) { fab.setAttribute('hidden', ''); fab.classList.remove('fab-hidden'); }
  }

  function addEditOverlay(block) {
    var label = block.dataset.sectionLabel || block.dataset.sectionId;
    var required = !!block.dataset.required;
    var isHidden = block.getAttribute('data-hidden') === '1';

    var overlay = document.createElement('div');
    overlay.className = 'section-edit-overlay';

    var eyeOpen =
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
        '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>' +
      '</svg>';
    var eyeOff =
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
        '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>' +
      '</svg>';

    overlay.innerHTML =
      // Drag handle — large touch target
      '<div class="section-drag-handle" role="button" tabindex="0" aria-label="Drag to reorder ' + label + '">' +
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true">' +
          '<circle cx="9" cy="5" r="1.6"/><circle cx="15" cy="5" r="1.6"/>' +
          '<circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/>' +
          '<circle cx="9" cy="19" r="1.6"/><circle cx="15" cy="19" r="1.6"/>' +
        '</svg>' +
      '</div>' +
      '<span class="section-edit-label">' + label + '</span>' +
      // Visibility toggle — hidden for required sections
      (required
        ? '<span class="section-required-badge">Required</span>'
        : '<button type="button" class="section-visibility-btn" aria-label="' + (isHidden ? 'Show ' : 'Hide ') + label + '">' +
            (isHidden ? eyeOff : eyeOpen) +
          '</button>'
      );

    block.insertBefore(overlay, block.firstChild);

    if (isHidden) block.classList.add('section-edit-dimmed');

    // Visibility toggle
    var visBtn = overlay.querySelector('.section-visibility-btn');
    if (visBtn) {
      visBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        buzz();
        if (block.getAttribute('data-hidden') === '1') {
          block.removeAttribute('data-hidden');
          block.classList.remove('section-edit-dimmed');
          visBtn.setAttribute('aria-label', 'Hide ' + label);
          visBtn.innerHTML = eyeOpen;
        } else {
          block.setAttribute('data-hidden', '1');
          block.classList.add('section-edit-dimmed');
          visBtn.setAttribute('aria-label', 'Show ' + label);
          visBtn.innerHTML = eyeOff;
        }
      });
    }

    // Drag
    var handle = overlay.querySelector('.section-drag-handle');
    if (handle) {
      handle.addEventListener('pointerdown', function (e) {
        if (e.button !== undefined && e.button !== 0) return; // left / touch only
        e.preventDefault();
        startDrag(e, block, handle);
      });
      // Keyboard reorder (up/down arrow)
      handle.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          var prev = block.previousElementSibling;
          if (prev && prev.classList.contains('section-block')) {
            container.insertBefore(block, prev);
            handle.focus();
          }
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          var next = block.nextElementSibling;
          if (next && next.classList.contains('section-block')) {
            container.insertBefore(next, block);
            handle.focus();
          }
        }
      });
    }
  }

  /* ── Drag and drop ──────────────────────────────────────────────────── */

  function startDrag(e, block, handle) {
    buzz();

    var rect = block.getBoundingClientRect();

    // Placeholder holds the spot
    var placeholder = document.createElement('div');
    placeholder.className = 'section-drag-placeholder';
    placeholder.style.height = rect.height + 'px';
    container.insertBefore(placeholder, block);
    block.classList.add('section-dragging');
    block.style.display = 'none';

    // Compact pill ghost — label only (no content clone)
    var label = block.dataset.sectionLabel || block.dataset.sectionId;
    var ghost = document.createElement('div');
    ghost.className = 'section-drag-ghost';
    ghost.style.width = rect.width + 'px';
    ghost.style.left = rect.left + 'px';
    ghost.textContent = label;
    document.body.appendChild(ghost);

    dragState = {
      block: block,
      ghost: ghost,
      placeholder: placeholder,
      clientY: e.clientY,
      ghostH: 48  // pill height
    };

    // Position ghost at pointer
    positionGhost(e.clientY);

    handle.setPointerCapture(e.pointerId);
    handle.addEventListener('pointermove', onDragMove);
    handle.addEventListener('pointerup', onDragEnd);
    handle.addEventListener('pointercancel', onDragEnd);

    // Lock page scroll on touch
    document.body.style.overflow = 'hidden';
  }

  function positionGhost(clientY) {
    if (!dragState) return;
    // Center ghost on pointer vertically
    dragState.ghost.style.top = (clientY - dragState.ghostH / 2) + 'px';
  }

  function onDragMove(e) {
    if (!dragState) return;

    positionGhost(e.clientY);

    // Find insertion point
    var siblings = getBlocks().filter(function (b) { return b !== dragState.block; });
    var insertBefore = null;
    for (var i = 0; i < siblings.length; i++) {
      var r = siblings[i].getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) {
        insertBefore = siblings[i];
        break;
      }
    }
    if (insertBefore) {
      container.insertBefore(dragState.placeholder, insertBefore);
    } else {
      container.appendChild(dragState.placeholder);
    }

    // Auto-scroll near viewport edges
    autoScroll(e.clientY);
  }

  function autoScroll(clientY) {
    var ZONE = 80;   // px from edge to start scrolling
    var MAX_SPEED = 12;
    var vh = window.innerHeight;

    if (scrollRaf) { cancelAnimationFrame(scrollRaf); scrollRaf = null; }

    var speed = 0;
    if (clientY < ZONE) {
      speed = -Math.round(MAX_SPEED * (1 - clientY / ZONE));
    } else if (clientY > vh - ZONE) {
      speed = Math.round(MAX_SPEED * (1 - (vh - clientY) / ZONE));
    }

    if (speed !== 0) {
      (function scroll() {
        window.scrollBy(0, speed);
        scrollRaf = requestAnimationFrame(scroll);
      }());
    }
  }

  function onDragEnd(e) {
    if (!dragState) return;

    if (scrollRaf) { cancelAnimationFrame(scrollRaf); scrollRaf = null; }

    var block = dragState.block;
    container.insertBefore(block, dragState.placeholder);
    block.style.display = '';
    block.classList.remove('section-dragging');
    container.removeChild(dragState.placeholder);
    document.body.removeChild(dragState.ghost);
    document.body.style.overflow = '';

    e.currentTarget.removeEventListener('pointermove', onDragMove);
    e.currentTarget.removeEventListener('pointerup', onDragEnd);
    e.currentTarget.removeEventListener('pointercancel', onDragEnd);

    dragState = null;
    buzz();
  }

  /* ── Reset ───────────────────────────────────────────────────────────── */

  function confirmReset() {
    if (!window.confirm('Reset to default layout? This clears your saved order and visibility.')) return;
    // Restore DOM to default order (originalOrder was set on entry = the order as-stored)
    // We need the true factory order — read from data-section-id order in the page's source.
    // The best proxy is originalOrder itself if no layout was previously applied, but to be
    // safe we'll just clear storage and reload.
    resetLayout();
    // Briefly exit edit mode then reload
    exitEditMode(false);
    showToast('Layout reset — reloading…');
    setTimeout(function () { window.location.reload(); }, 900);
  }

  /* ── Commit / Cancel ─────────────────────────────────────────────────── */

  function commitEditMode() {
    getBlocks().forEach(function (block) {
      var hidden = block.getAttribute('data-hidden') === '1' && !block.dataset.required;
      block.style.display = hidden ? 'none' : '';
      block.classList.remove('section-edit-was-hidden');
    });

    persistLayout();
    exitEditMode(true);
    showToast('Layout saved!');
  }

  function cancelEditMode() {
    // Restore data-hidden and display from snapshot
    getBlocks().forEach(function (block) {
      var id = block.dataset.sectionId;
      var wasHidden = originalHidden[id];
      block.classList.remove('section-edit-was-hidden');
      if (wasHidden && !block.dataset.required) {
        block.setAttribute('data-hidden', '1');
        block.style.display = 'none';
      } else {
        block.removeAttribute('data-hidden');
        block.style.display = '';
      }
    });

    // Restore original order
    originalOrder.forEach(function (id) {
      var block = container.querySelector('[data-section-id="' + id + '"]');
      if (block) container.appendChild(block);
    });

    exitEditMode(false);
  }

  function exitEditMode(saved) {
    document.body.classList.remove('layout-edit-mode');
    document.body.style.overflow = '';

    document.querySelectorAll('.section-edit-overlay').forEach(function (el) {
      if (el.parentNode) el.parentNode.removeChild(el);
    });
    document.querySelectorAll('.section-edit-dimmed').forEach(function (el) {
      el.classList.remove('section-edit-dimmed');
    });

    var toolbar = document.getElementById('layout-edit-toolbar');
    if (toolbar) toolbar.parentNode.removeChild(toolbar);

    var fab = document.getElementById('layout-edit-fab');
    if (fab) fab.removeAttribute('hidden');
  }

  /* ── Toast ───────────────────────────────────────────────────────────── */

  function showToast(msg) {
    var existing = document.getElementById('layout-toast');
    if (existing) existing.parentNode.removeChild(existing);

    var toast = document.createElement('div');
    toast.id = 'layout-toast';
    toast.className = 'layout-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.textContent = msg;
    document.body.appendChild(toast);

    // Trigger transition
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { toast.classList.add('layout-toast--visible'); });
    });

    setTimeout(function () {
      toast.classList.remove('layout-toast--visible');
      setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 350);
    }, 2200);
  }

  /* ── Haptic ──────────────────────────────────────────────────────────── */

  function buzz(ms) {
    try { navigator.vibrate && navigator.vibrate(ms || 10); } catch (e) {}
  }

  /* ── CSRF ────────────────────────────────────────────────────────────── */

  function getCsrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute('content') || '';
    var m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  /* ── Boot ────────────────────────────────────────────────────────────── */

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
