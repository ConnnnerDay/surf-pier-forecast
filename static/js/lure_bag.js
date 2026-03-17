/**
 * Lure Bag — product-specific lure inventory with live species matching.
 *
 * Loads /static/data/lures_db.json for the full product catalogue.
 * Reads window._lureData (injected by _lures.html) for active-species data
 * keyed by lure_type_key → lure name in _lureData.
 * State stored under localStorage key "surf_pier_lure_bag_v1" as a JSON array
 * of lure id strings.
 */

(function () {
  'use strict';

  var STORAGE_KEY = 'surf_pier_lure_bag_v1';
  var luresDb = [];          // full product catalogue from lures_db.json
  var activeFilter = '';     // '' = all, else category string
  var activeBrandFilter = ''; // '' = all, else manufacturer string
  var searchQuery = '';      // current search text

  // ── localStorage helpers ─────────────────────────────────────────────────

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

  function addToBag(id) {
    var bag = loadBag();
    if (bag.indexOf(id) === -1) {
      bag.push(id);
      saveBag(bag);
    }
    return loadBag();
  }

  function removeFromBag(id) {
    var bag = loadBag().filter(function (x) { return x !== id; });
    saveBag(bag);
    return bag;
  }

  // ── lure data helpers ────────────────────────────────────────────────────

  function getLureById(id) {
    return luresDb.find(function (l) { return l.id === id; }) || null;
  }

  function getActiveSpeciesForLure(lure) {
    if (!window._lureData || !lure.lure_type_key) return [];
    var typeEntry = window._lureData.find(function (t) {
      return t.lure === lure.lure_type_key;
    });
    return (typeEntry && typeEntry.active_species) ? typeEntry.active_species : [];
  }

  function filterLures(query, category, brand) {
    var q = query.trim().toLowerCase();
    return luresDb.filter(function (l) {
      if (category && l.category !== category) return false;
      if (brand && l.manufacturer !== brand) return false;
      if (q) {
        var haystack = [l.name, l.manufacturer]
          .concat(l.colors || [])
          .join(' ')
          .toLowerCase();
        if (haystack.indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  // ── render: bag count badge ───────────────────────────────────────────────

  function renderBagCount(bag) {
    var el = document.getElementById('lure-bag-count');
    if (!el) return;
    el.textContent = bag.length + (bag.length === 1 ? ' lure' : ' lures');
    el.classList.toggle('has-items', bag.length > 0);
  }

  // ── render: search results ────────────────────────────────────────────────

  function buildResultItem(lure, inBag) {
    var item = document.createElement('div');
    item.className = 'lure-result-item' + (inBag ? ' in-bag' : '');
    item.dataset.lureId = lure.id;

    var info = document.createElement('div');
    info.className = 'lure-result-info';

    var nameEl = document.createElement('div');
    nameEl.className = 'lure-result-name';
    nameEl.textContent = lure.name;
    info.appendChild(nameEl);

    var metaEl = document.createElement('div');
    metaEl.className = 'lure-result-meta';
    metaEl.textContent = lure.manufacturer + ' · ' + lure.category;
    info.appendChild(metaEl);

    if (lure.colors && lure.colors.length > 0) {
      var colorsEl = document.createElement('div');
      colorsEl.className = 'lure-result-colors';
      lure.colors.slice(0, 4).forEach(function (c) {
        var tag = document.createElement('span');
        tag.className = 'lure-color-tag';
        tag.textContent = c;
        colorsEl.appendChild(tag);
      });
      if (lure.colors.length > 4) {
        var more = document.createElement('span');
        more.className = 'lure-color-more';
        more.textContent = '+' + (lure.colors.length - 4) + ' more';
        colorsEl.appendChild(more);
      }
      info.appendChild(colorsEl);
    }

    item.appendChild(info);

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'lure-result-add-btn' + (inBag ? ' in-bag' : '');
    btn.dataset.lureId = lure.id;
    btn.setAttribute('aria-label', (inBag ? 'Remove ' : 'Add ') + lure.name + (inBag ? ' from bag' : ' to bag'));
    btn.textContent = inBag ? 'In bag \u2713' : '+ Add';
    item.appendChild(btn);

    return item;
  }

  function renderResults() {
    var container = document.getElementById('lure-search-results');
    var hint = document.getElementById('lure-search-hint');
    if (!container) return;

    // Clear previous results (preserve hint element)
    Array.from(container.children).forEach(function (child) {
      if (child.id !== 'lure-search-hint') child.remove();
    });

    var isBlank = !searchQuery.trim() && !activeFilter && !activeBrandFilter;
    if (isBlank) {
      if (hint) hint.style.display = '';
      return;
    }
    if (hint) hint.style.display = 'none';

    var filtered = filterLures(searchQuery, activeFilter, activeBrandFilter);
    var bag = loadBag();

    if (filtered.length === 0) {
      var none = document.createElement('p');
      none.className = 'lure-no-results';
      none.textContent = 'No lures match \u201c' + (searchQuery || activeFilter) + '\u201d';
      container.appendChild(none);
      return;
    }

    // Sort: not-in-bag first, then alpha
    filtered.sort(function (a, b) {
      var aIn = bag.indexOf(a.id) !== -1 ? 1 : 0;
      var bIn = bag.indexOf(b.id) !== -1 ? 1 : 0;
      if (aIn !== bIn) return aIn - bIn;
      return a.name.localeCompare(b.name);
    });

    filtered.forEach(function (lure) {
      container.appendChild(buildResultItem(lure, bag.indexOf(lure.id) !== -1));
    });
  }

  // ── render: your bag ─────────────────────────────────────────────────────

  function buildBagRow(lure, activeSpecies) {
    var row = document.createElement('div');
    row.className = 'lure-bag-row';
    row.dataset.lureId = lure.id;

    var info = document.createElement('div');
    info.className = 'lure-bag-row-info';

    var nameEl = document.createElement('div');
    nameEl.className = 'lure-bag-row-name';
    nameEl.textContent = lure.name;
    info.appendChild(nameEl);

    var metaEl = document.createElement('div');
    metaEl.className = 'lure-bag-row-meta';
    metaEl.textContent = lure.manufacturer + ' \u00b7 ' + lure.category;
    info.appendChild(metaEl);

    if (activeSpecies.length > 0) {
      var speciesWrap = document.createElement('div');
      speciesWrap.className = 'lure-bag-row-species';

      var label = document.createElement('span');
      label.className = 'lure-active-label';
      label.textContent = 'Biting today:';
      speciesWrap.appendChild(label);

      activeSpecies.slice(0, 4).forEach(function (sp) {
        var tag = document.createElement('span');
        tag.className = 'lure-species-tag';
        tag.textContent = sp.replace(/\s*\(.*\)$/, '');
        speciesWrap.appendChild(tag);
      });
      if (activeSpecies.length > 4) {
        var moreEl = document.createElement('span');
        moreEl.className = 'lure-species-more';
        moreEl.textContent = '+' + (activeSpecies.length - 4) + ' more';
        speciesWrap.appendChild(moreEl);
      }
      info.appendChild(speciesWrap);
    } else {
      var noMatch = document.createElement('p');
      noMatch.className = 'lure-bag-no-match';
      noMatch.textContent = 'No active fish matching this lure right now';
      info.appendChild(noMatch);
    }

    row.appendChild(info);

    // Active indicator badge
    if (activeSpecies.length > 0) {
      var badge = document.createElement('span');
      badge.className = 'lure-bag-match-active';
      badge.textContent = '\u25cf Biting';
      row.appendChild(badge);
    }

    var removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'lure-bag-remove-btn';
    removeBtn.dataset.lureId = lure.id;
    removeBtn.setAttribute('aria-label', 'Remove ' + lure.name + ' from bag');
    removeBtn.innerHTML = '<svg viewBox="0 0 20 20" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="4" y1="4" x2="16" y2="16"/><line x1="16" y1="4" x2="4" y2="16"/></svg>';
    row.appendChild(removeBtn);

    return row;
  }

  function renderBag() {
    var bag = loadBag();
    var itemsContainer = document.getElementById('lure-bag-items');
    var emptyMsg = document.getElementById('lure-bag-empty');
    if (!itemsContainer) return;

    // Remove previous rows (keep empty msg)
    Array.from(itemsContainer.children).forEach(function (child) {
      if (child.id !== 'lure-bag-empty') child.remove();
    });

    renderBagCount(bag);

    if (bag.length === 0) {
      if (emptyMsg) emptyMsg.style.display = '';
      return;
    }
    if (emptyMsg) emptyMsg.style.display = 'none';

    // Sort: lures with active fish first, then alpha
    var sorted = bag.slice().sort(function (a, b) {
      var la = getLureById(a);
      var lb = getLureById(b);
      var aSpec = la ? getActiveSpeciesForLure(la).length : 0;
      var bSpec = lb ? getActiveSpeciesForLure(lb).length : 0;
      if (aSpec !== bSpec) return bSpec - aSpec;
      return (la ? la.name : a).localeCompare(lb ? lb.name : b);
    });

    sorted.forEach(function (id) {
      var lure = getLureById(id);
      if (!lure) return;
      var row = buildBagRow(lure, getActiveSpeciesForLure(lure));
      // Wire remove button immediately
      var removeBtn = row.querySelector('.lure-bag-remove-btn');
      if (removeBtn) {
        removeBtn.addEventListener('click', function () {
          removeFromBag(id);
          renderBag();
          renderResults();
        });
      }
      itemsContainer.appendChild(row);
    });
  }

  // ── event wiring ─────────────────────────────────────────────────────────

  function init() {
    var searchInput = document.getElementById('lure-search-input');
    var clearBtn = document.getElementById('lure-search-clear');
    var filterChips = document.querySelectorAll('#lure-filter-chips .lure-filter-chip');
    var brandChips = document.querySelectorAll('.lure-brand-chip');
    var resultsContainer = document.getElementById('lure-search-results');

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        searchQuery = searchInput.value;
        if (clearBtn) clearBtn.style.display = searchQuery ? '' : 'none';
        renderResults();
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        searchQuery = '';
        if (searchInput) searchInput.value = '';
        clearBtn.style.display = 'none';
        renderResults();
      });
    }

    filterChips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        activeFilter = chip.dataset.filter || '';
        filterChips.forEach(function (c) {
          c.classList.toggle('lure-filter-chip--active', c === chip);
        });
        renderResults();
      });
    });

    brandChips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        activeBrandFilter = chip.dataset.brand || '';
        brandChips.forEach(function (c) {
          c.classList.toggle('lure-brand-chip--active', c === chip);
        });
        renderResults();
      });
    });

    // Delegate add/remove clicks on results list
    if (resultsContainer) {
      resultsContainer.addEventListener('click', function (e) {
        var btn = e.target.closest('.lure-result-add-btn');
        if (!btn) return;
        var id = btn.dataset.lureId;
        if (!id) return;
        var bag = loadBag();
        if (bag.indexOf(id) !== -1) {
          removeFromBag(id);
        } else {
          addToBag(id);
        }
        renderResults();
        renderBag();
      });
    }

    renderBag();
  }

  // Fetch catalogue then boot
  fetch('/static/data/lures_db.json')
    .then(function (res) { return res.json(); })
    .then(function (data) {
      luresDb = data;
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
      } else {
        init();
      }
    })
    .catch(function (err) {
      console.warn('lure_bag: could not load lures_db.json', err);
    });

})();
