(function () {
  var section = document.getElementById('fish-log');
  if (!section) return;

  var currentLocId = section.dataset.locationId || '';
  var loggedIn = section.dataset.loggedIn === '1';
  var LOG_KEY = 'fishlog_' + currentLocId;

  function getLog() {
    try { return JSON.parse(localStorage.getItem(LOG_KEY)) || []; }
    catch (e) { return []; }
  }

  function saveLog(entries) {
    localStorage.setItem(LOG_KEY, JSON.stringify(entries));
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  var MAX_PHOTO_BYTES = 8 * 1024 * 1024; // 8 MB per photo

  function readPhotos(files) {
    var validFiles = Array.prototype.slice.call(files || []).filter(function (file) {
      return file && file.type && file.type.indexOf('image/') === 0 && file.size <= MAX_PHOTO_BYTES;
    }).slice(0, 8);

    return Promise.all(validFiles.map(function (file) {
      return new Promise(function (resolve) {
        var reader = new FileReader();
        reader.onload = function (ev) { resolve(ev.target && ev.target.result ? ev.target.result : ''); };
        reader.onerror = function () { resolve(''); };
        reader.readAsDataURL(file);
      });
    })).then(function (photos) {
      return photos.filter(Boolean);
    });
  }

  function renderPersonalBests(entries) {
    var pbEl = document.getElementById('personal-bests');
    var pbList = document.getElementById('pb-list');
    if (!pbEl || !pbList) return;

    var bests = {};
    entries.forEach(function (e) {
      if (!e.size) return;
      var num = parseFloat(e.size);
      if (isNaN(num) || num <= 0) return;
      var unit = e.size.replace(/[\d.\s]+/g, '').trim() || '"';
      var sp = e.species.trim();
      var key = sp.toLowerCase();
      if (!bests[key] || num > bests[key].size) {
        bests[key] = { species: sp, size: num, unit: unit, date: e.date || '' };
      }
    });

    var sorted = Object.values(bests).sort(function (a, b) { return b.size - a.size; });
    if (!sorted.length) {
      pbEl.style.display = 'none';
      return;
    }

    pbEl.style.display = 'block';
    var html = '';
    sorted.forEach(function (pb) {
      html += '<div class="pb-entry">';
      html += '<span class="pb-species">' + esc(pb.species) + '</span>';
      html += '<span class="pb-size">' + pb.size + (pb.unit ? ' ' + esc(pb.unit) : '') + '</span>';
      if (pb.date) html += '<span class="pb-date">' + esc(pb.date.split(' ')[0]) + '</span>';
      html += '</div>';
    });
    pbList.innerHTML = html;
  }

  function renderCatchStats(entries) {
    var statsEl = document.getElementById('catch-stats');
    if (!statsEl || !entries.length) return;
    statsEl.style.display = 'block';

    document.getElementById('stat-total').textContent = entries.length;

    var speciesMap = {};
    entries.forEach(function (e) {
      var sp = e.species.toLowerCase().trim();
      speciesMap[sp] = (speciesMap[sp] || 0) + 1;
    });
    document.getElementById('stat-species').textContent = Object.keys(speciesMap).length;

    var topSpecies = '—';
    var topCount = 0;
    for (var sp in speciesMap) {
      if (speciesMap[sp] > topCount) {
        topCount = speciesMap[sp];
        topSpecies = sp.charAt(0).toUpperCase() + sp.slice(1);
      }
    }
    document.getElementById('stat-top').textContent = topSpecies;

    if (entries[0] && entries[0].date) {
      document.getElementById('stat-recent').textContent = entries[0].date.split(' ')[0];
    }

    renderPersonalBests(entries);
  }

  function renderLog() {
    var entries = getLog();
    var container = document.getElementById('fishlog-entries');
    var empty = document.getElementById('fishlog-empty');
    var statsEl = document.getElementById('catch-stats');
    if (!entries.length) {
      container.innerHTML = '';
      empty.style.display = 'block';
      if (statsEl) statsEl.style.display = 'none';
      var pb = document.getElementById('personal-bests');
      if (pb) pb.style.display = 'none';
      return;
    }

    empty.style.display = 'none';
    var html = '';
    entries.forEach(function (e, i) {
      html += '<div class="fishlog-entry">';
      html += '<div class="fishlog-entry-main">';
      html += '<strong class="fishlog-species">' + esc(e.species) + '</strong>';
      if (e.size) html += ' <span class="fishlog-size">' + esc(e.size) + '</span>';
      if (e.notes) html += '<p class="fishlog-notes">' + esc(e.notes) + '</p>';
      if (e.photos && e.photos.length) {
        html += '<div class="fishlog-photo-grid">';
        e.photos.forEach(function (photo, pIdx) {
          html += '<a href="' + esc(photo) + '" target="_blank" rel="noopener noreferrer" aria-label="Open fish photo ' + (pIdx + 1) + '">';
          html += '<img src="' + esc(photo) + '" alt="' + esc(e.species) + ' catch photo ' + (pIdx + 1) + '" class="fishlog-photo-thumb">';
          html += '</a>';
        });
        html += '</div>';
      }
      html += '</div>';
      html += '<div class="fishlog-entry-meta">';
      html += '<span class="fishlog-date">' + esc(e.date) + '</span>';
      html += '<button class="fishlog-del" data-index="' + i + '" title="Remove" aria-label="Remove this catch entry">&times;</button>';
      html += '</div></div>';
    });

    container.innerHTML = html;
    renderCatchStats(entries);
  }

  function addLogEntry() {
    var speciesInput = document.getElementById('log-species');
    var sizeInput = document.getElementById('log-size');
    var notesInput = document.getElementById('log-notes');
    var photosInput = document.getElementById('log-photos');

    var species = speciesInput.value.trim();
    if (!species) return;
    var size = sizeInput.value.trim();
    var notes = notesInput.value.trim();

    // Warn if any selected photos were skipped due to size limit
    var allFiles = Array.prototype.slice.call(photosInput.files || []);
    var oversized = allFiles.filter(function (f) { return f.size > MAX_PHOTO_BYTES; });
    if (oversized.length) {
      var helpEl = document.querySelector('.fishlog-photo-help');
      if (helpEl) {
        helpEl.textContent = oversized.length + ' photo(s) skipped (max 8 MB each).';
        helpEl.style.color = '#c0392b';
        setTimeout(function () {
          helpEl.textContent = 'You can add multiple photos per catch.';
          helpEl.style.color = '';
        }, 4000);
      }
    }

    readPhotos(photosInput.files).then(function (photos) {
      var entries = getLog();
      var now = new Date();
      entries.unshift({
        species: species,
        size: size,
        notes: notes,
        photos: photos,
        date: now.toLocaleDateString() + ' ' + now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      });
      if (entries.length > 50) entries = entries.slice(0, 50);
      saveLog(entries);

      speciesInput.value = '';
      sizeInput.value = '';
      notesInput.value = '';
      photosInput.value = '';
      renderLog();

      if (loggedIn && currentLocId) {
        fetch('/api/log?location=' + encodeURIComponent(currentLocId), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ species: species, size: size, notes: notes })
        }).catch(function () {});
      }
    });
  }

  function deleteLog(index) {
    var entries = getLog();
    entries.splice(index, 1);
    saveLog(entries);
    renderLog();
  }

  function exportLogCSV() {
    var entries = getLog();
    if (!entries.length) return;

    var rows = [['Date', 'Species', 'Size', 'Notes', 'Photo Count']];
    entries.forEach(function (e) {
      rows.push([
        '"' + (e.date || '').replace(/"/g, '""') + '"',
        '"' + (e.species || '').replace(/"/g, '""') + '"',
        '"' + (e.size || '').replace(/"/g, '""') + '"',
        '"' + (e.notes || '').replace(/"/g, '""') + '"',
        String((e.photos || []).length)
      ]);
    });

    var csv = rows.map(function (r) { return r.join(','); }).join('\n');
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'fishing-log.csv';
    link.click();
    URL.revokeObjectURL(link.href);
  }

  document.getElementById('fishlog-form').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      addLogEntry();
    }
  });

  document.getElementById('fishlog-add-btn').addEventListener('click', addLogEntry);
  document.getElementById('fishlog-export-btn').addEventListener('click', exportLogCSV);
  document.getElementById('fishlog-entries').addEventListener('click', function (e) {
    if (!e.target.classList.contains('fishlog-del')) return;
    var idx = Number(e.target.getAttribute('data-index'));
    if (!Number.isNaN(idx)) deleteLog(idx);
  });

  renderLog();
})();
