/**
 * Lure Bag — localStorage-backed lure inventory with live species matching.
 *
 * State stored under localStorage key "surf_pier_lure_bag_v1" as a JSON array
 * of lure name strings.  Reads window._lureData (injected by _lures.html) for
 * the full lure list with active-species data.
 */

(function () {
  'use strict';

  var STORAGE_KEY = 'surf_pier_lure_bag_v1';

  // ── helpers ──────────────────────────────────────────────────────────────

  function loadBag() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    } catch (_) {
      return [];
    }
  }

  function saveBag(bag) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(bag));
    } catch (_) { /* quota exceeded — ignore */ }
  }

  function toggleBag(lureName) {
    var bag = loadBag();
    var idx = bag.indexOf(lureName);
    if (idx === -1) {
      bag.push(lureName);
    } else {
      bag.splice(idx, 1);
    }
    saveBag(bag);
    return bag;
  }

  function getLureData(lureName) {
    if (!window._lureData) return null;
    return window._lureData.find(function (l) { return l.lure === lureName; }) || null;
  }

  // ── render ────────────────────────────────────────────────────────────────

  function renderBagCount(bag) {
    var el = document.getElementById('lure-bag-count');
    if (!el) return;
    el.textContent = bag.length + (bag.length === 1 ? ' lure' : ' lures');
    el.classList.toggle('has-items', bag.length > 0);
  }

  function renderChips(bag) {
    var chips = document.querySelectorAll('.lure-bag-toggle-chip');
    chips.forEach(function (chip) {
      var name = chip.dataset.lureName;
      var inBag = bag.indexOf(name) !== -1;
      chip.setAttribute('aria-pressed', inBag ? 'true' : 'false');
    });
  }

  function renderPickCards(bag) {
    var cards = document.querySelectorAll('.lure-pick-card');
    cards.forEach(function (card) {
      var name = card.dataset.lureName;
      var inBag = bag.indexOf(name) !== -1;
      card.classList.toggle('in-bag', inBag);
      var btn = card.querySelector('.lure-bag-btn-text');
      if (btn) btn.textContent = inBag ? 'In bag ✓' : 'Add to bag';
    });
  }

  function buildMatchRow(lureEntry) {
    var row = document.createElement('div');
    row.className = 'lure-bag-match-row';

    // Image
    if (lureEntry.image) {
      var img = document.createElement('img');
      img.src = '/static/' + lureEntry.image;
      img.alt = lureEntry.lure;
      img.className = 'lure-bag-match-img';
      img.loading = 'lazy';
      row.appendChild(img);
    }

    // Info
    var info = document.createElement('div');
    info.className = 'lure-bag-match-info';

    var nameEl = document.createElement('div');
    nameEl.className = 'lure-bag-match-name';
    nameEl.textContent = lureEntry.lure;
    info.appendChild(nameEl);

    if (lureEntry.active_species && lureEntry.active_species.length > 0) {
      var speciesWrap = document.createElement('div');
      speciesWrap.className = 'lure-bag-match-species';
      lureEntry.active_species.forEach(function (sp) {
        var tag = document.createElement('span');
        tag.className = 'lure-species-tag';
        // trim parenthetical from species name for brevity
        tag.textContent = sp.replace(/\s*\(.*\)$/, '');
        speciesWrap.appendChild(tag);
      });
      info.appendChild(speciesWrap);
    } else {
      var noMatch = document.createElement('p');
      noMatch.className = 'lure-bag-no-match';
      noMatch.textContent = 'No fish actively biting this lure right now';
      info.appendChild(noMatch);
    }

    row.appendChild(info);

    // Active indicator
    if (lureEntry.active_species && lureEntry.active_species.length > 0) {
      var badge = document.createElement('span');
      badge.className = 'lure-bag-match-active';
      badge.textContent = '● Biting';
      row.appendChild(badge);
    }

    return row;
  }

  function renderBagMatches(bag) {
    var container = document.getElementById('lure-bag-matches');
    var emptyMsg = document.getElementById('lure-bag-empty');
    if (!container) return;

    // Remove previous match rows (keep empty msg element)
    var existing = container.querySelectorAll('.lure-bag-match-row');
    existing.forEach(function (el) { el.remove(); });

    if (bag.length === 0) {
      if (emptyMsg) emptyMsg.style.display = '';
      return;
    }

    if (emptyMsg) emptyMsg.style.display = 'none';

    // Sort: lures with active fish first
    var sorted = bag.slice().sort(function (a, b) {
      var da = getLureData(a);
      var db = getLureData(b);
      var aActive = (da && da.active_species && da.active_species.length > 0) ? 1 : 0;
      var bActive = (db && db.active_species && db.active_species.length > 0) ? 1 : 0;
      return bActive - aActive;
    });

    sorted.forEach(function (lureName) {
      var data = getLureData(lureName);
      if (data) {
        container.appendChild(buildMatchRow(data));
      }
    });
  }

  function refreshAll() {
    var bag = loadBag();
    renderBagCount(bag);
    renderChips(bag);
    renderPickCards(bag);
    renderBagMatches(bag);
  }

  // ── event wiring ──────────────────────────────────────────────────────────

  function init() {
    // Top-pick "Add to bag" buttons
    document.querySelectorAll('.lure-bag-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.dataset.lureName;
        if (!name) return;
        toggleBag(name);
        refreshAll();
      });
    });

    // Bag toggle chips
    document.querySelectorAll('.lure-bag-toggle-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var name = chip.dataset.lureName;
        if (!name) return;
        toggleBag(name);
        refreshAll();
      });
    });

    // Initial render
    refreshAll();
  }

  // Run after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
