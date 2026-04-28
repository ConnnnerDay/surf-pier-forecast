(function () {
  var section = document.getElementById('fish-log');
  if (!section) return;

  var currentLocId = section.dataset.locationId || '';
  var loggedIn = section.dataset.loggedIn === '1';
  var LOG_KEY = 'fishlog_' + currentLocId;

  // Index of the entry currently being edited (-1 = add-new mode)
  var _editingIndex = -1;

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

  // ── Toast notification ────────────────────────────────────────────────────
  function showToast(msg, isError) {
    var existing = document.getElementById('fishlog-toast');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.id = 'fishlog-toast';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.style.cssText = [
      'position:fixed;bottom:1.25rem;left:50%;transform:translateX(-50%)',
      'background:' + (isError ? '#c0392b' : '#0e5f78'),
      'color:#fff;padding:0.6rem 1.2rem;border-radius:24px',
      'font-size:0.875rem;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,.25)',
      'z-index:9999;white-space:nowrap;pointer-events:none',
      'opacity:0;transition:opacity .2s'
    ].join(';');
    el.textContent = msg;
    document.body.appendChild(el);
    requestAnimationFrame(function() { el.style.opacity = '1'; });
    setTimeout(function() {
      el.style.opacity = '0';
      setTimeout(function() { if (el.parentNode) el.remove(); }, 250);
    }, 2600);
  }

  // ── Validation shake ──────────────────────────────────────────────────────
  function shakeInput(input) {
    input.setAttribute('aria-invalid', 'true');
    input.style.transition = 'none';
    input.style.outline = '2px solid #c0392b';
    var keyframes = [0, -6, 5, -4, 3, -2, 0];
    var i = 0;
    function step() {
      if (i >= keyframes.length) {
        input.style.transform = '';
        return;
      }
      input.style.transform = 'translateX(' + keyframes[i] + 'px)';
      i++;
      setTimeout(step, 40);
    }
    step();
    input.focus();
    setTimeout(function() {
      input.style.outline = '';
      input.removeAttribute('aria-invalid');
    }, 2000);
  }

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
      html += '<div class="fishlog-entry" data-index="' + i + '">';
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
      html += '<div class="fishlog-entry-actions">';
      html += '<button class="fishlog-edit" data-index="' + i + '" title="Edit" aria-label="Edit this catch entry">';
      html += '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
      html += '</button>';
      html += '<button class="fishlog-del" data-index="' + i + '" title="Remove" aria-label="Remove this catch entry">&times;</button>';
      html += '</div>';
      html += '</div></div>';
    });

    container.innerHTML = html;
    renderCatchStats(entries);
  }

  function cancelEdit() {
    _editingIndex = -1;
    var btn = document.getElementById('fishlog-add-btn');
    var cancelBtn = document.getElementById('fishlog-cancel-btn');
    if (btn) { btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Add'; }
    if (cancelBtn) cancelBtn.style.display = 'none';
    var speciesInput = document.getElementById('log-species');
    var sizeInput = document.getElementById('log-size');
    var notesInput = document.getElementById('log-notes');
    var photosInput = document.getElementById('log-photos');
    if (speciesInput) speciesInput.value = '';
    if (sizeInput) sizeInput.value = '';
    if (notesInput) notesInput.value = '';
    if (photosInput) photosInput.value = '';
    window._capturedPhotos = [];
    var help = document.getElementById('fishlog-photo-help');
    if (help) { help.textContent = 'You can add multiple photos per catch.'; help.style.color = ''; }
    // Remove highlight from any entry being edited
    document.querySelectorAll('.fishlog-entry--editing').forEach(function(el) { el.classList.remove('fishlog-entry--editing'); });
  }

  function startEdit(index) {
    var entries = getLog();
    var entry = entries[index];
    if (!entry) return;
    _editingIndex = index;

    var speciesInput = document.getElementById('log-species');
    var sizeInput = document.getElementById('log-size');
    var notesInput = document.getElementById('log-notes');
    var photosInput = document.getElementById('log-photos');
    if (speciesInput) speciesInput.value = entry.species || '';
    if (sizeInput) sizeInput.value = entry.size || '';
    if (notesInput) notesInput.value = entry.notes || '';
    if (photosInput) photosInput.value = '';
    window._capturedPhotos = [];

    var btn = document.getElementById('fishlog-add-btn');
    var cancelBtn = document.getElementById('fishlog-cancel-btn');
    if (btn) btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg> Save';
    if (cancelBtn) cancelBtn.style.display = '';

    var help = document.getElementById('fishlog-photo-help');
    if (help) {
      var photoCount = (entry.photos || []).length;
      help.textContent = photoCount
        ? photoCount + ' existing photo' + (photoCount === 1 ? '' : 's') + '. Upload new ones to replace.'
        : 'You can add multiple photos per catch.';
      help.style.color = photoCount ? 'var(--ocean, #0e5f78)' : '';
    }

    // Highlight the entry being edited
    document.querySelectorAll('.fishlog-entry').forEach(function(el) { el.classList.remove('fishlog-entry--editing'); });
    var entryEl = document.querySelector('.fishlog-entry[data-index="' + index + '"]');
    if (entryEl) entryEl.classList.add('fishlog-entry--editing');

    if (speciesInput) speciesInput.focus();
  }

  function addLogEntry() {
    var speciesInput = document.getElementById('log-species');
    var sizeInput = document.getElementById('log-size');
    var notesInput = document.getElementById('log-notes');
    var photosInput = document.getElementById('log-photos');

    var species = speciesInput.value.trim();
    if (!species) {
      shakeInput(speciesInput);
      showToast('Enter the species you caught', true);
      return;
    }
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

    var capturedPhotos = window._capturedPhotos || [];
    window._capturedPhotos = [];

    readPhotos(photosInput.files).then(function (pickerPhotos) {
      var newPhotos = capturedPhotos.concat(pickerPhotos).slice(0, 8);
      var entries = getLog();
      var isEdit = _editingIndex >= 0 && _editingIndex < entries.length;

      if (isEdit) {
        var existing = entries[_editingIndex];
        // Keep existing photos if no new ones were supplied
        var photos = newPhotos.length ? newPhotos : (existing.photos || []);
        entries[_editingIndex] = {
          species: species,
          size: size,
          notes: notes,
          photos: photos,
          date: existing.date
        };
        saveLog(entries);
        cancelEdit();
        renderLog();
        showToast('Catch updated');

        if (loggedIn && currentLocId) {
          var ctrl = new AbortController();
          var tid = setTimeout(function() { ctrl.abort(); }, 10000);
          fetch('/api/log?location=' + encodeURIComponent(currentLocId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ species: species, size: size, notes: notes }),
            signal: ctrl.signal
          }).then(function() { clearTimeout(tid); }).catch(function (err) {
            clearTimeout(tid);
            console.error('Failed to sync log entry to server:', err);
          });
        }
      } else {
        var now = new Date();
        entries.unshift({
          species: species,
          size: size,
          notes: notes,
          photos: newPhotos,
          date: now.toLocaleDateString() + ' ' + now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        });
        if (entries.length > 50) entries = entries.slice(0, 50);
        saveLog(entries);

        speciesInput.value = '';
        sizeInput.value = '';
        notesInput.value = '';
        photosInput.value = '';
        var help = document.getElementById('fishlog-photo-help');
        if (help) { help.textContent = 'You can add multiple photos per catch.'; help.style.color = ''; }
        renderLog();
        showToast('Catch logged!');

        if (loggedIn && currentLocId) {
          var ctrl2 = new AbortController();
          var tid2 = setTimeout(function() { ctrl2.abort(); }, 10000);
          fetch('/api/log?location=' + encodeURIComponent(currentLocId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ species: species, size: size, notes: notes }),
            signal: ctrl2.signal
          }).then(function() { clearTimeout(tid2); }).catch(function (err) {
            clearTimeout(tid2);
            console.error('Failed to sync log entry to server:', err);
          });
        }
      }
    });
  }

  function deleteLog(index) {
    var entries = getLog();
    var entry = entries[index];
    var name = entry ? entry.species : 'this entry';
    if (!window.confirm('Remove "' + name + '" from your log?')) return;
    if (_editingIndex === index) cancelEdit();
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

  // ── In-app camera capture ──────────────────────────────────────────────────
  (function () {
    var cameraBtn = document.getElementById('camera-btn');
    var modal = document.getElementById('camera-modal');
    var preview = document.getElementById('camera-preview');
    var captureBtn = document.getElementById('camera-capture-btn');
    var closeBtn = document.getElementById('camera-close-btn');
    var canvas = document.getElementById('camera-canvas');
    if (!cameraBtn || !modal || !preview || !canvas) return;

    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      cameraBtn.hidden = false;
    } else {
      return;
    }

    var stream = null;

    function stopStream() {
      if (stream) {
        stream.getTracks().forEach(function (t) { t.stop(); });
        stream = null;
      }
    }

    function openCamera() {
      navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
        .then(function (s) {
          stream = s;
          preview.srcObject = s;
          modal.hidden = false;
          captureBtn.focus();
        })
        .catch(function (err) {
          var help = document.getElementById('fishlog-photo-help');
          if (err && err.name === 'NotAllowedError') {
            if (help) { help.textContent = 'Camera access denied — choose a photo from your library instead.'; help.style.color = '#c0392b'; }
            setTimeout(function() { if (help) { help.textContent = 'You can add multiple photos per catch.'; help.style.color = ''; } }, 4000);
          } else {
            document.getElementById('log-photos').click();
          }
        });
    }

    function closeCamera() {
      stopStream();
      modal.hidden = true;
      preview.srcObject = null;
    }

    function capturePhoto() {
      if (!stream) return;
      canvas.width = preview.videoWidth;
      canvas.height = preview.videoHeight;
      canvas.getContext('2d').drawImage(preview, 0, 0);

      canvas.toBlob(function (blob) {
        if (!blob) return;
        var reader = new FileReader();
        reader.onload = function (ev) {
          var dataUrl = ev.target.result;
          if (!window._capturedPhotos) window._capturedPhotos = [];
          window._capturedPhotos.push(dataUrl);
          var help = document.getElementById('fishlog-photo-help');
          if (help) {
            var n = window._capturedPhotos.length;
            help.textContent = n + ' photo' + (n === 1 ? '' : 's') + ' ready to add with your catch.';
            help.style.color = 'var(--ocean, #0e5f78)';
          }
        };
        reader.readAsDataURL(blob);
        closeCamera();
      }, 'image/jpeg', 0.88);
    }

    cameraBtn.addEventListener('click', openCamera);
    captureBtn.addEventListener('click', capturePhoto);
    closeBtn.addEventListener('click', closeCamera);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !modal.hidden) closeCamera();
    });

    window.addEventListener('pagehide', stopStream);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden && !modal.hidden) closeCamera();
    });
  })();

  document.getElementById('fishlog-form').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      addLogEntry();
    }
  });

  document.getElementById('fishlog-add-btn').addEventListener('click', addLogEntry);
  document.getElementById('fishlog-export-btn').addEventListener('click', exportLogCSV);

  document.getElementById('fishlog-entries').addEventListener('click', function (e) {
    var delBtn = e.target.closest('.fishlog-del');
    if (delBtn) {
      var idx = Number(delBtn.getAttribute('data-index'));
      if (!Number.isNaN(idx)) deleteLog(idx);
      return;
    }
    var editBtn = e.target.closest('.fishlog-edit');
    if (editBtn) {
      var idx2 = Number(editBtn.getAttribute('data-index'));
      if (!Number.isNaN(idx2)) startEdit(idx2);
    }
  });

  // Cancel-edit button (injected into the form)
  (function() {
    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.id = 'fishlog-cancel-btn';
    cancelBtn.className = 'fishlog-cancel-btn';
    cancelBtn.style.display = 'none';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', cancelEdit);
    var addBtn = document.getElementById('fishlog-add-btn');
    if (addBtn && addBtn.parentNode) addBtn.parentNode.insertBefore(cancelBtn, addBtn.nextSibling);
  })();

  renderLog();
})();
