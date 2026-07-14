// Forecast page interactions
// Reads globals set inline by the server-rendered script block:
//   LOGGED_IN, FAV_KEY, CURRENT_LOC_ID, CURRENT_LOC_STATE, CURRENT_LOC_NAME,
//   CURRENT_LOC_LAT, CURRENT_LOC_LNG, IS_REFRESHING, MAP_IS_ADMIN,
//   SERVER_PROFILE, SHARE_ID, SHARE_TEXT_URL

/* ---- HTML escape helper ---- */
function esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ---- Alerts collapse/expand ---- */
function toggleAlerts(btn) {
    var detail = document.getElementById('alerts-detail');
    if (!detail) return;
    var expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    detail.hidden = expanded;
    var chevron = btn.querySelector('.alerts-toggle-chevron');
    if (chevron) chevron.innerHTML = expanded ? '&#9660;' : '&#9650;';
}

/* ---- Favorite Locations (localStorage) ---- */
function getFavorites() {
    try { return JSON.parse(localStorage.getItem(FAV_KEY)) || []; }
    catch(e) { return []; }
}

function saveFavorites(favs) {
    localStorage.setItem(FAV_KEY, JSON.stringify(favs));
    syncToServer();
}

function isFavorite() {
    return getFavorites().some(function(f) { return f.id === CURRENT_LOC_ID; });
}

function toggleFavorite() {
    var favs = getFavorites();
    var idx = favs.findIndex(function(f) { return f.id === CURRENT_LOC_ID; });
    if (idx >= 0) {
        favs.splice(idx, 1);
    } else {
        favs.push({ id: CURRENT_LOC_ID, name: CURRENT_LOC_NAME });
        if (favs.length > 8) favs = favs.slice(-8);
    }
    saveFavorites(favs);
    renderFavorites();
}

function renderFavorites() {
    var favs = getFavorites();
    var isFav = isFavorite();
    var toggle = document.getElementById('fav-toggle');
    if (toggle) {
        toggle.classList.toggle('fav-toggle--active', isFav);
        var _favToggleLabel = isFav ? 'Remove from favorites' : 'Save to favorites';
        toggle.setAttribute('aria-pressed', isFav ? 'true' : 'false');
        toggle.setAttribute('aria-label', _favToggleLabel);
        toggle.title = _favToggleLabel;
    }
    var bar = document.getElementById('favorites-bar');
    var wrap = document.getElementById('favorites-bar-wrap');
    if (!bar) return;
    if (favs.length === 0) {
        bar.innerHTML = '';
        if (wrap) wrap.hidden = true;
        return;
    }
    if (wrap) wrap.hidden = false;
    var html = '';
    favs.forEach(function(f) {
        var active = f.id === CURRENT_LOC_ID ? ' fav-chip--active' : '';
        var shortName = f.name.split(',')[0];
        html += '<a href="/f/' + encodeURIComponent(f.id) + '" class="fav-chip' + active + '">' + esc(shortName) + '</a>';
    });
    bar.innerHTML = html;
}

/* ---- Fishing Profile ---- */
function getProfile() {
    if (!SERVER_PROFILE || !Object.keys(SERVER_PROFILE).length) return null;
    return SERVER_PROFILE;
}

function buildProfileParams(profile) {
    var params = new URLSearchParams(window.location.search);
    var ft = (profile.fishing_types || profile.fishing_type || []).join(',');
    var tg = (profile.targets || []).join(',');
    if (ft) params.set('fishing_types', ft);
    else params.delete('fishing_types');
    if (tg) params.set('targets', tg);
    else params.delete('targets');
    return params;
}

function profileParamsMatch(profile) {
    var params = new URLSearchParams(window.location.search);
    var currentFt = params.get('fishing_types') || '';
    var currentTg = params.get('targets') || '';
    var expectedFt = (profile.fishing_types || profile.fishing_type || []).join(',');
    var expectedTg = (profile.targets || []).join(',');
    return currentFt === expectedFt && currentTg === expectedTg;
}

function applyBaitFilters() {
    var profile = getProfile();
    if (!profile || !profile.completed) return;
    var liveKeywords = ['live shrimp','live finger','live mullet','live menhaden','live blue runner','live cigar','live crab','live baitfish','live eel','live minnow','live pogies'];
    var lureKeywords = ['jig','spoon','plug','lure','feather','cedar plug','trolling','dart','artificial','fishbites'];
    var cutKeywords  = ['cut ','blood','squid','clam','crab piece','shrimp piece','sand flea','fiddler','fresh shrimp','mussel'];
    function methodScore(pref) { return pref === 'yes' ? 2 : pref === 'sometimes' ? 1 : 0; }

    var baitList = document.querySelector('.bait-list');
    if (baitList) {
        var baitItems = Array.prototype.slice.call(baitList.querySelectorAll('.bait-item[data-bait-name]'));
        baitItems.forEach(function(item, idx) {
            var name = item.getAttribute('data-bait-name') || '';
            var isLive = liveKeywords.some(function(k) { return name.indexOf(k) >= 0; });
            var isLure = lureKeywords.some(function(k) { return name.indexOf(k) >= 0; });
            var isCut  = cutKeywords.some(function(k)  { return name.indexOf(k) >= 0; });
            var score = 0;
            if (isLive) score = Math.max(score, methodScore(profile.live_bait));
            if (isLure) score = Math.max(score, methodScore(profile.lures));
            if (isCut)  score = Math.max(score, methodScore(profile.cut_bait));
            if (!isLive && !isLure && !isCut) score = 1;
            item._profileScore = score;
            item._origIndex = idx;
        });
        baitItems.sort(function(a,b) {
            if (b._profileScore !== a._profileScore) return b._profileScore - a._profileScore;
            return a._origIndex - b._origIndex;
        });
        baitItems.forEach(function(item, idx) {
            baitList.appendChild(item);
            var numEl = item.querySelector('.bait-num');
            if (numEl) numEl.textContent = idx + 1;
        });
    }

    var speciesCards = document.querySelectorAll('.species-card[data-species-bait]');
    speciesCards.forEach(function(card) {
        var baitText = card.getAttribute('data-species-bait') || '';
        var metaBait = card.querySelector('.meta-item--bait');
        if (!metaBait) return;
        var existingLabel = metaBait.querySelector('.personalized-label');
        if (existingLabel) existingLabel.remove();
        var hasLive = liveKeywords.some(function(k) { return baitText.indexOf(k) >= 0; });
        var hasLure = lureKeywords.some(function(k) { return baitText.indexOf(k) >= 0; });
        var hasCut  = cutKeywords.some(function(k)  { return baitText.indexOf(k) >= 0; });
        var tips = [];
        if (hasLive && (profile.live_bait === 'yes' || profile.live_bait === 'sometimes')) tips.push('live bait');
        if (hasLure && (profile.lures === 'yes' || profile.lures === 'sometimes')) tips.push('lures');
        if (hasCut  && (profile.cut_bait === 'yes' || profile.cut_bait === 'sometimes')) tips.push('cut bait');
        if (tips.length > 0) {
            var badge = document.createElement('span');
            badge.className = 'personalized-label';
            badge.textContent = 'Matches: ' + tips.join(', ');
            var label = metaBait.querySelector('.meta-label');
            (label || metaBait).appendChild(badge);
        }
    });
}

(function() {
    var profile = getProfile();
    if (!profile || !profile.completed) {
        if (LOGGED_IN) {
            var profilePrompted = sessionStorage.getItem('profile_prompted');
            if (!profilePrompted) {
                sessionStorage.setItem('profile_prompted', 'true');
                window.location.href = '/profile';
                return;
            }
        }
        return;
    }
    if (!profileParamsMatch(profile)) {
        var params = buildProfileParams(profile);
        var newUrl = window.location.pathname + '?' + params.toString();
        window.location.replace(newUrl);
        return;
    }
    applyBaitFilters();
})();

/* ---- Theme toggle (called by menu) ---- */
function toggleTheme() {
    var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    if (isDark) {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('theme', 'light');
    } else {
        document.documentElement.setAttribute('data-theme', 'dark');
        localStorage.setItem('theme', 'dark');
    }
    syncToServer();
}

/* ---- Unit toggle ---- */
function applyUnits(unit) {
    var els = document.querySelectorAll('.unit-temp');
    els.forEach(function(el) {
        var f = parseFloat(el.getAttribute('data-f'));
        if (isNaN(f)) return;
        if (unit === 'C') {
            var c = Math.round((f - 32) * 5 / 9);
            el.innerHTML = c + '&deg;C';
        } else {
            el.innerHTML = Math.round(f) + '&deg;F';
        }
    });
}

function toggleUnits() {
    var current = localStorage.getItem('units') || 'F';
    var next = current === 'F' ? 'C' : 'F';
    localStorage.setItem('units', next);
    applyUnits(next);
    syncToServer();
}

(function() {
    var u = localStorage.getItem('units');
    if (u === 'C') applyUnits('C');
})();

/* ---- Toast ---- */
function showToast(msg) {
    var t = document.getElementById('share-toast');
    if (!t) return;
    t.textContent = msg;
    t.hidden = false;
    setTimeout(function() { t.hidden = true; }, 2200);
}

/* ---- Share ---- */
function shareLink() {
    var url = window.location.origin + '/f/' + SHARE_ID;
    if (navigator.share) {
        navigator.share({ title: 'Fishing Forecast', url: url }).catch(function(){});
    } else if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(function() { showToast('Link copied!'); });
    } else {
        prompt('Copy this link:', url);
    }
}

function shareText() {
    fetch(SHARE_TEXT_URL)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.text) {
                var url = window.location.origin + '/f/' + data.location_id;
                var full = data.text + '\n\n' + url;
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(full).then(function() { showToast('Summary copied!'); });
                } else {
                    prompt('Copy this summary:', full);
                }
            }
        });
}

/* ---- Quick-share from verdict bar ---- */
function shareConditions(btn) {
    var url = SHARE_ID ? (window.location.origin + '/f/' + SHARE_ID) : window.location.href;

    var loc = (typeof CURRENT_LOC_NAME !== 'undefined' && CURRENT_LOC_NAME)
        ? CURRENT_LOC_NAME
        : ((document.querySelector('.location-bar-name') || {}).textContent || '').trim();

    var verdictEl = document.querySelector('.csc-verdict-badge');
    var verdict = verdictEl ? verdictEl.textContent.trim() : '';

    var windEl = document.querySelector('.wind-direction-context') || document.querySelector('.wind-quality-indicator');
    var wind = windEl ? windEl.textContent.trim() : '';

    var tempEl = document.querySelector('.unit-temp');
    var temp = tempEl ? tempEl.textContent.trim() : '';

    var wavesCard = Array.prototype.find.call(document.querySelectorAll('.stat-card'), function (c) {
        var lbl = c.querySelector('.stat-label');
        return lbl && lbl.textContent.trim() === 'Waves';
    });
    var waves = wavesCard ? ((wavesCard.querySelector('.stat-value') || {}).textContent || '').trim() : '';

    var lines = [];
    if (loc) lines.push(loc + ' fishing forecast');
    if (verdict) lines.push('Conditions: ' + verdict);
    if (wind) lines.push('Wind: ' + wind);
    if (temp) lines.push('Water: ' + temp);
    if (waves) lines.push('Waves: ' + waves);
    var text = lines.join('\n');

    var CHECK_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';

    function flashSuccess() {
        if (!btn) return;
        var orig = btn.innerHTML;
        btn.innerHTML = CHECK_SVG;
        btn.style.color = 'var(--sea-green, #1a9c72)';
        setTimeout(function () { btn.innerHTML = orig; btn.style.color = ''; }, 1800);
    }

    if (navigator.share) {
        navigator.share({ title: (loc || 'Fishing') + ' Forecast', text: text, url: url })
            .then(flashSuccess)
            .catch(function () {});
    } else if (navigator.clipboard) {
        navigator.clipboard.writeText(text + '\n\n' + url).then(function () {
            flashSuccess();
            showToast('Forecast copied!');
        });
    } else {
        prompt('Copy this forecast:', text + '\n\n' + url);
    }
}

/* ---- Sync localStorage → server ---- */
function syncToServer() {
    if (!LOGGED_IN) return;
    var payload = {};
    var theme = localStorage.getItem('theme');
    if (theme) payload.theme = theme;
    var units = localStorage.getItem('units');
    if (units) payload.units = units;
    var profile = getProfile();
    if (profile) payload.fishing_profile = profile;
    var favs = getFavorites();
    if (favs.length) payload.favorites = favs;
    if (CURRENT_LOC_ID) payload.location_id = CURRENT_LOC_ID;
    if (Object.keys(payload).length === 0) return;
    var payloadStr = JSON.stringify(payload);
    if (localStorage.getItem('_prefs_sync_hash') === payloadStr) return;
    fetch('/api/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payloadStr
    }).then(function(r) {
        if (r.ok) localStorage.setItem('_prefs_sync_hash', payloadStr);
    }).catch(function() {});
}

function syncLogToServer() {
    if (!LOGGED_IN || !CURRENT_LOC_ID) return;
    if (typeof getLog !== 'function' || typeof saveLog !== 'function') return;
    var entries = getLog();
    if (!entries.length) return;
    entries.forEach(function(e) {
        if (e.synced) return;
        fetch('/api/log?location=' + encodeURIComponent(CURRENT_LOC_ID), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ species: e.species, size: e.size || '', notes: e.notes || '' })
        }).then(function(r) { if (r.ok) e.synced = true; saveLog(entries); }).catch(function() {});
    });
}

/* ---- Refresh ---- */
function handleRefreshSubmit(form) {
    var btn = document.getElementById('refresh-btn');
    var label = document.getElementById('refresh-btn-label');
    var spinner = document.getElementById('refresh-btn-spinner');
    if (btn) btn.disabled = true;
    if (label) label.hidden = true;
    if (spinner) spinner.hidden = false;
}

// Format ISO timestamps from data attributes
(function() {
    var fmt = { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };
    document.querySelectorAll('.iso-timestamp').forEach(function(el) {
        try {
            var d = new Date(el.dataset.iso);
            if (!isNaN(d.getTime())) el.textContent = d.toLocaleString(undefined, fmt);
        } catch(e) {}
    });
})();

// Service worker
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function(){});
}

// Screen wake lock — keeps screen on while forecast is visible (useful at the pier)
(function () {
    if (!('wakeLock' in navigator)) return;
    var _lock = null;
    function acquireLock() {
        navigator.wakeLock.request('screen').then(function (l) {
            _lock = l;
        }).catch(function () {});
    }
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') acquireLock();
    });
    acquireLock();
})();

// Install-to-homescreen prompt
(function () {
    if (window.matchMedia('(display-mode: standalone)').matches) return;
    if (window.navigator.standalone) return;
    var deferredPrompt = null;
    window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        deferredPrompt = e;
        if (sessionStorage.getItem('install_nudge_shown')) return;
        sessionStorage.setItem('install_nudge_shown', '1');
        var banner = document.createElement('div');
        banner.className = 'install-banner';
        banner.setAttribute('role', 'status');
        banner.innerHTML =
            '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12m0 0l-4-4m4 4l4-4"/><rect x="3" y="17" width="18" height="4" rx="2"/></svg>' +
            '<span>Add to home screen for offline access at the water</span>' +
            '<button class="install-banner-btn" id="install-yes">Install</button>' +
            '<button class="install-banner-close" id="install-dismiss" aria-label="Dismiss">&times;</button>';
        document.body.appendChild(banner);
        requestAnimationFrame(function () { banner.classList.add('install-banner--visible'); });
        document.getElementById('install-yes').addEventListener('click', function () {
            banner.remove();
            deferredPrompt.prompt();
            deferredPrompt.userChoice.then(function () { deferredPrompt = null; });
        });
        document.getElementById('install-dismiss').addEventListener('click', function () {
            banner.classList.remove('install-banner--visible');
            setTimeout(function () { banner.remove(); }, 300);
        });
    });
})();

// Auto-refresh countdown (reloads after 30 min)
(function() {
    var REFRESH_MS = 30 * 60 * 1000;
    var note = document.getElementById('auto-refresh-note');
    var start = Date.now();
    function updateCountdown() {
        var elapsed = Date.now() - start;
        var remaining = Math.max(0, REFRESH_MS - elapsed);
        var mins = Math.ceil(remaining / 60000);
        if (remaining <= 0) { if (note) note.textContent = '— refreshing...'; window.location.reload(); return; }
        if (note && mins <= 5) { note.textContent = '— auto-refresh in ' + mins + ' min'; }
    }
    setInterval(updateCountdown, 30000);
})();

/* ---- Species Expand/Collapse ---- */
(function() {
    var expandBtn = document.getElementById('species-expand-btn');
    var viewAllBtn = document.getElementById('view-all-species-btn');
    var panel = document.getElementById('full-species-panel');

    function expandSpecies() {
        if (!panel) return;
        panel.hidden = false;
        panel.classList.add('full-species-list--visible');
        if (expandBtn) { expandBtn.setAttribute('aria-expanded','true'); expandBtn.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="18 15 12 9 6 15"/></svg> Collapse Species List'; }
        if (viewAllBtn) viewAllBtn.setAttribute('aria-expanded','true');
        applyBaitFilters();
    }

    function collapseSpecies() {
        if (!panel) return;
        panel.hidden = true;
        panel.classList.remove('full-species-list--visible');
        if (expandBtn) { expandBtn.setAttribute('aria-expanded','false'); expandBtn.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg> View Full Species Forecast'; }
        if (viewAllBtn) viewAllBtn.setAttribute('aria-expanded','false');
    }

    if (expandBtn) expandBtn.addEventListener('click', function() {
        var isExpanded = this.getAttribute('aria-expanded') === 'true';
        isExpanded ? collapseSpecies() : expandSpecies();
    });
    if (viewAllBtn) viewAllBtn.addEventListener('click', function() {
        var isExpanded = this.getAttribute('aria-expanded') === 'true';
        isExpanded ? collapseSpecies() : expandSpecies();
    });
})();

/* ---- Regulations Modal ---- */
(function() {
    var modal = document.getElementById('reg-modal');
    var modalBody = document.getElementById('reg-modal-body');
    var closeBtn = document.getElementById('reg-modal-close');
    if (!modal) return;

    var _regPrevFocus = null;
    function openRegs(btn) {
        _regPrevFocus = document.activeElement || null;
        var card = btn.closest('[data-reg-min-size], [data-reg-bag-limit], [data-reg-season]');
        if (!card) return;
        var species = card.getAttribute('data-species-name') || 'Species';
        var minSize = card.getAttribute('data-reg-min-size') || '';
        var bagLimit = card.getAttribute('data-reg-bag-limit') || '';
        var season = card.getAttribute('data-reg-season') || '';
        var notes = card.getAttribute('data-reg-notes') || '';
        var gear = card.getAttribute('data-reg-gear') || '';
        var slot = card.getAttribute('data-reg-slot') || '';
        var regOfficialSource = card.getAttribute('data-reg-official-source') || '';
        var isStale = card.getAttribute('data-reg-is-stale') === 'true';
        var lastUpdated = card.getAttribute('data-reg-last-updated') || '';

        var html = '';
        if (isStale) {
            html += '<div class="reg-stale-alert"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> Regulation data may be outdated. Verify before fishing.</div>';
        }
        html += '<div class="reg-modal__grid">';
        if (minSize) html += '<div><span class="reg-label">Min. Size</span><span class="reg-value">' + esc(minSize) + '</span></div>';
        if (slot) html += '<div><span class="reg-label">Slot Limit</span><span class="reg-value">' + esc(slot) + '</span></div>';
        if (bagLimit) html += '<div><span class="reg-label">Bag Limit</span><span class="reg-value">' + esc(bagLimit) + '</span></div>';
        if (season) html += '<div><span class="reg-label">Season</span><span class="reg-value">' + esc(season) + '</span></div>';
        if (gear) html += '<div class="reg-modal__grid-full"><span class="reg-label">Gear Restrictions</span><span class="reg-value">' + esc(gear) + '</span></div>';
        if (notes) html += '<div class="reg-modal__grid-full"><span class="reg-label">Notes</span><span class="reg-value">' + esc(notes) + '</span></div>';
        html += '</div>';
        if (regOfficialSource) {
            html += '<div class="reg-source"><a href="' + esc(regOfficialSource) + '" target="_blank" rel="noopener noreferrer">Official Source &#x2197;</a></div>';
        }
        if (lastUpdated) {
            html += '<p class="reg-updated">Updated: ' + esc(lastUpdated) + '</p>';
        }

        document.getElementById('reg-modal-title').textContent = species + ' — Regulations';
        modalBody.innerHTML = html;
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        closeBtn.focus();
    }

    function closeRegs() {
        modal.hidden = true;
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        if (_regPrevFocus && typeof _regPrevFocus.focus === 'function') {
            _regPrevFocus.focus({ preventScroll: true });
            _regPrevFocus = null;
        }
    }

    if (closeBtn) closeBtn.addEventListener('click', closeRegs);
    modal.addEventListener('click', function(e) { if (e.target === modal) closeRegs(); });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && !modal.hidden) { closeRegs(); return; }
        if (e.key === 'Tab' && !modal.hidden) {
            var card = modal.querySelector('.reg-modal__card');
            if (!card) return;
            var focusable = Array.prototype.slice.call(card.querySelectorAll('button,a,[tabindex="0"]'));
            if (!focusable.length) return;
            var first = focusable[0], last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    });
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('[data-open-regs]');
        if (btn) { e.preventDefault(); openRegs(btn); }
    });
})();

/* ---- Rig Diagram Lightbox ---- */
(function() {
    var modal = document.getElementById('rig-img-modal');
    var img = document.getElementById('rig-img-modal-img');
    var title = document.getElementById('rig-img-modal-title');
    var closeBtn = document.getElementById('rig-img-modal-close');
    if (!modal || !img || !title || !closeBtn) return;

    var _prevFocus = null;
    function openRigImg(btn) {
        var src = btn.getAttribute('data-rig-img-src');
        var name = btn.getAttribute('data-rig-img-name') || 'Rig diagram';
        if (!src) return;
        _prevFocus = document.activeElement || null;
        img.src = src;
        img.alt = name + ' diagram';
        title.textContent = name;
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        closeBtn.focus();
    }

    function closeRigImg() {
        modal.hidden = true;
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        img.src = '';
        if (_prevFocus && typeof _prevFocus.focus === 'function') {
            _prevFocus.focus({ preventScroll: true });
            _prevFocus = null;
        }
    }

    closeBtn.addEventListener('click', closeRigImg);
    modal.addEventListener('click', function(e) { if (e.target === modal) closeRigImg(); });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && !modal.hidden) { closeRigImg(); return; }
        if (e.key === 'Tab' && !modal.hidden) {
            var card = modal.querySelector('.rig-img-modal__card');
            if (!card) return;
            var focusable = Array.prototype.slice.call(card.querySelectorAll('button,a,[tabindex="0"]'));
            if (!focusable.length) return;
            var first = focusable[0], last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    });
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('[data-open-rig-img]');
        if (btn) { e.preventDefault(); openRigImg(btn); }
    });
})();


// Render favorites bar now that DOM is ready
renderFavorites();
// Fire deferred syncs
syncToServer();
syncLogToServer();
