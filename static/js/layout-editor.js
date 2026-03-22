/* Layout editor — drag-to-reorder + show/hide page sections */
(function () {
  'use strict';

  var LS_PREFIX = 'page_layout_';
  var container, lsKey;
  var originalOrder = []; // section-id order snapshot for cancel
  var originalHidden = {}; // hidden state snapshot for cancel
  var dragState = null;

  /* ── Init ────────────────────────────────────────────────────────────── */

  function init() {
    container = document.getElementById('sections-container');
    if (!container) return;

    lsKey = LS_PREFIX + (window.CURRENT_LOC_ID || 'default');

    // Load persisted layout
    applyStoredLayout();

    // Create the floating "Edit Layout" button
    createFAB();
  }

  /* ── Layout persistence ─────────────────────────────────────────────── */

  function getBlocks() {
    return Array.from(container.querySelectorAll(':scope > .section-block'));
  }

  function readStoredLayout() {
    try {
      var raw = localStorage.getItem(lsKey);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function applyStoredLayout() {
    var layout = readStoredLayout();
    if (!layout || !Array.isArray(layout) || !layout.length) return;

    // Apply visibility
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

    // Reorder: append each known block in saved order; unknown blocks stay at end
    var knownIds = layout.map(function (i) { return i.id; });
    knownIds.forEach(function (id) {
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
      var csrf = getCsrf();
      fetch('/api/v1/page-layout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf,
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify({ layout: layout })
      }).catch(function () {});
    }
  }

  /* ── FAB ────────────────────────────────────────────────────────────── */

  function createFAB() {
    var fab = document.createElement('button');
    fab.id = 'layout-edit-fab';
    fab.className = 'layout-edit-fab';
    fab.type = 'button';
    fab.setAttribute('aria-label', 'Edit page layout');
    fab.innerHTML =
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
        '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>' +
        '<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>' +
      '</svg>' +
      '<span>Edit Layout</span>';
    fab.addEventListener('click', enterEditMode);
    document.body.appendChild(fab);
  }

  /* ── Edit mode ─────────────────────────────────────────────────────── */

  function enterEditMode() {
    document.body.classList.add('layout-edit-mode');

    // Snapshot current state so Cancel can restore it
    var blocks = getBlocks();
    originalOrder = blocks.map(function (b) { return b.dataset.sectionId; });
    originalHidden = {};
    blocks.forEach(function (b) {
      originalHidden[b.dataset.sectionId] = b.getAttribute('data-hidden') === '1';
    });

    // Add edit overlay to each block
    blocks.forEach(addEditOverlay);

    // Build fixed toolbar
    var toolbar = document.createElement('div');
    toolbar.id = 'layout-edit-toolbar';
    toolbar.className = 'layout-edit-toolbar';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.innerHTML =
      '<span class="layout-edit-hint">Drag to reorder &bull; tap eye to hide</span>' +
      '<div class="layout-edit-actions">' +
        '<button type="button" class="layout-edit-btn layout-edit-cancel" id="layout-edit-cancel">Cancel</button>' +
        '<button type="button" class="layout-edit-btn layout-edit-save" id="layout-edit-save">Save</button>' +
      '</div>';
    document.body.appendChild(toolbar);

    document.getElementById('layout-edit-cancel').addEventListener('click', cancelEditMode);
    document.getElementById('layout-edit-save').addEventListener('click', commitEditMode);

    // Hide FAB while editing
    document.getElementById('layout-edit-fab').setAttribute('hidden', '');
  }

  function addEditOverlay(block) {
    var label = block.dataset.sectionLabel || block.dataset.sectionId;
    var required = !!block.dataset.required;
    var isHidden = block.getAttribute('data-hidden') === '1';

    var overlay = document.createElement('div');
    overlay.className = 'section-edit-overlay';

    var eyeOpen =
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
        '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>' +
      '</svg>';
    var eyeOff =
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
        '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94' +
        'M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19' +
        'm-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>' +
      '</svg>';

    overlay.innerHTML =
      '<div class="section-drag-handle" aria-label="Drag to reorder ' + label + '">' +
        '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">' +
          '<circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/>' +
          '<circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/>' +
          '<circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/>' +
        '</svg>' +
      '</div>' +
      '<span class="section-edit-label">' + label + '</span>' +
      (required ? '' :
        '<button type="button" class="section-visibility-btn" aria-label="' +
          (isHidden ? 'Show ' : 'Hide ') + label + '">' +
          (isHidden ? eyeOff : eyeOpen) +
        '</button>'
      );

    block.insertBefore(overlay, block.firstChild);

    // Dim if currently hidden (so user can still drag it)
    if (isHidden) block.classList.add('section-edit-dimmed');

    // Visibility toggle
    var visBtn = overlay.querySelector('.section-visibility-btn');
    if (visBtn) {
      visBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        var hidden = block.getAttribute('data-hidden') === '1';
        if (hidden) {
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

    // Drag handle
    var handle = overlay.querySelector('.section-drag-handle');
    if (handle) {
      handle.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        startDrag(e, block, handle);
      });
    }
  }

  /* ── Drag and drop ──────────────────────────────────────────────────── */

  function startDrag(e, block, handle) {
    var rect = block.getBoundingClientRect();
    var scrollTop = window.scrollY || document.documentElement.scrollTop;

    // Placeholder keeps the gap in the list
    var placeholder = document.createElement('div');
    placeholder.className = 'section-drag-placeholder';
    placeholder.style.height = rect.height + 'px';
    container.insertBefore(placeholder, block);

    // Ghost follows the pointer
    var ghost = block.cloneNode(true);
    ghost.className = block.className + ' section-drag-ghost';
    ghost.style.width = rect.width + 'px';
    ghost.style.top = (rect.top + scrollTop) + 'px';
    ghost.style.left = rect.left + 'px';
    document.body.appendChild(ghost);

    block.classList.add('section-dragging');
    block.style.display = 'none';

    dragState = {
      block: block,
      ghost: ghost,
      placeholder: placeholder,
      offsetY: e.clientY - rect.top,
      pointerId: e.pointerId
    };

    handle.setPointerCapture(e.pointerId);
    handle.addEventListener('pointermove', onDragMove);
    handle.addEventListener('pointerup', onDragEnd);
    handle.addEventListener('pointercancel', onDragEnd);
  }

  function onDragMove(e) {
    if (!dragState) return;
    var scrollTop = window.scrollY || document.documentElement.scrollTop;
    dragState.ghost.style.top = (e.clientY + scrollTop - dragState.offsetY) + 'px';

    // Find insertion point among visible blocks
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
  }

  function onDragEnd(e) {
    if (!dragState) return;

    var block = dragState.block;
    var ghost = dragState.ghost;
    var placeholder = dragState.placeholder;

    container.insertBefore(block, placeholder);
    block.style.display = '';
    block.classList.remove('section-dragging');
    container.removeChild(placeholder);
    document.body.removeChild(ghost);

    e.currentTarget.removeEventListener('pointermove', onDragMove);
    e.currentTarget.removeEventListener('pointerup', onDragEnd);
    e.currentTarget.removeEventListener('pointercancel', onDragEnd);

    dragState = null;
  }

  /* ── Commit / Cancel ────────────────────────────────────────────────── */

  function commitEditMode() {
    // Apply display state from data-hidden
    getBlocks().forEach(function (block) {
      if (block.getAttribute('data-hidden') === '1' && !block.dataset.required) {
        block.style.display = 'none';
      } else {
        block.style.display = '';
      }
    });

    persistLayout();
    exitEditMode();
  }

  function cancelEditMode() {
    // Restore original hidden state
    getBlocks().forEach(function (block) {
      var id = block.dataset.sectionId;
      var wasHidden = originalHidden[id];
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

    exitEditMode();
  }

  function exitEditMode() {
    document.body.classList.remove('layout-edit-mode');

    // Remove overlays
    document.querySelectorAll('.section-edit-overlay').forEach(function (el) {
      el.parentNode && el.parentNode.removeChild(el);
    });

    // Remove dimmed class
    document.querySelectorAll('.section-edit-dimmed').forEach(function (el) {
      el.classList.remove('section-edit-dimmed');
    });

    // Remove toolbar
    var toolbar = document.getElementById('layout-edit-toolbar');
    if (toolbar) toolbar.parentNode.removeChild(toolbar);

    // Show FAB
    var fab = document.getElementById('layout-edit-fab');
    if (fab) fab.removeAttribute('hidden');
  }

  /* ── Helpers ────────────────────────────────────────────────────────── */

  function getCsrf() {
    // Try meta tag first, then cookie
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
