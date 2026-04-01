(function () {
    'use strict';

    // ─── Config ───────────────────────────────────────────────────────────────
    var API_URL = '/api/fishing-map';
    var LS_KEY  = 'fmap_filters_v2';   // localStorage persistence key

    var DEFAULT_CENTER = [37.5, -96.0];
    var DEFAULT_ZOOM   = 4;

    // Bounding boxes for coast auto-zoom [south, west, north, east]
    var COAST_BOUNDS = {
        east:   [[24.0, -98.0],  [47.5, -66.0]],
        west:   [[32.0, -125.0], [49.0, -116.0]],
        hawaii: [[18.5, -161.0], [22.5, -154.0]]
    };

    // Quick-pick species chips per coast
    var QUICK_SPECIES = {
        all: [
            'Striped bass', 'Red drum', 'Flounder', 'Bluefish',
            'Pompano', 'Speckled trout', 'Snook', 'Tarpon',
            'Pacific halibut', 'Rockfish'
        ],
        east: [
            'Striped bass', 'Red drum', 'Flounder', 'Bluefish',
            'Pompano', 'Speckled trout', 'Snook', 'Tarpon',
            'Spanish mackerel', 'Black drum'
        ],
        west: [
            'Pacific halibut', 'Lingcod', 'Rockfish', 'Surfperch',
            'Yellowtail', 'White seabass', 'Salmon', 'Cabezon'
        ],
        hawaii: [
            'Bonefish', 'Bluefin trevally', 'Papio (jack crevalle)',
            'Mahi-mahi', 'Ahi (yellowfin tuna)', 'Wahoo'
        ]
    };

    var MONTH_NAMES = [
        '', 'January','February','March','April','May','June',
        'July','August','September','October','November','December'
    ];

    var ACTIVITY = {
        peak: { color: '#22c55e', ring: 'rgba(34,197,94,0.35)',  label: 'Peak', size: 11 },
        good: { color: '#3b82f6', ring: 'rgba(59,130,246,0.30)', label: 'Good', size: 9  },
        fair: { color: '#f59e0b', ring: 'rgba(245,158,11,0.28)', label: 'Fair', size: 8  },
        slow: { color: '#ef4444', ring: 'rgba(239,68,68,0.20)',  label: 'Slow', size: 6  },
        none: { color: '#4b5563', ring: 'rgba(75,85,99,0.15)',   label: 'N/A',  size: 5  }
    };

    // ─── State ────────────────────────────────────────────────────────────────
    var map           = null;
    var mapReady      = false;
    var markers       = [];          // [{id, leaflet, data}]
    var allSpecies    = [];          // species name strings for autocomplete
    var currentData   = [];          // last API response locations
    var selectedId    = null;
    var fetchTimer    = null;
    var activeCoast   = 'all';
    var activeCat     = '';
    var activeSpecies = '';
    var activeMonth   = 0;           // 0 = current month (server default)
    var isFullscreen  = false;
    var monthlySummary = [];         // from last API response
    var userCoords    = null;        // {lat, lng} set after Near Me fires
    var sortByDist    = false;       // hotspot sort mode

    // ─── DOM refs ─────────────────────────────────────────────────────────────
    var els = {};

    // ─── Utilities ───────────────────────────────────────────────────────────
    function esc(s) {
        return String(s || '')
            .replace(/&/g,'&amp;').replace(/</g,'&lt;')
            .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function haversineMi(lat1, lng1, lat2, lng2) {
        var R = 3958.8; // Earth radius in miles
        var dLat = (lat2 - lat1) * Math.PI / 180;
        var dLng = (lng2 - lng1) * Math.PI / 180;
        var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
                Math.sin(dLng / 2) * Math.sin(dLng / 2);
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    // Simple Levenshtein distance for "Did you mean?" fuzzy matching
    function levenshtein(a, b) {
        var m = a.length, n = b.length;
        var dp = [];
        for (var i = 0; i <= m; i++) {
            dp[i] = [i];
            for (var j = 1; j <= n; j++) {
                dp[i][j] = i === 0 ? j :
                    a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] :
                    1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
            }
        }
        return dp[m][n];
    }

    // ─── Leaflet loader ───────────────────────────────────────────────────────
    function loadScript(src) {
        return new Promise(function (res, rej) {
            var ex = document.querySelector('script[src="' + src + '"]');
            if (ex) { if (window.L) { res(); return; }
                ex.addEventListener('load', res, { once: true });
                ex.addEventListener('error', rej, { once: true }); return; }
            var s = document.createElement('script');
            s.src = src; s.async = true; s.onload = res; s.onerror = rej;
            document.head.appendChild(s);
        });
    }
    function ensureLeafletCss() {
        if (document.querySelector('link[data-fmap-css]')) return;
        var l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css';
        l.setAttribute('data-fmap-css', '1');
        document.head.appendChild(l);
    }
    function ensureLeaflet() {
        if (window.L) return Promise.resolve();
        ensureLeafletCss();
        return loadScript('https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js')
            .catch(function () {
                return loadScript('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js');
            });
    }

    // ─── Map init ─────────────────────────────────────────────────────────────
    function initMap() {
        if (mapReady) return;
        mapReady = true;

        map = L.map(els.mapEl, { zoomControl: true }).setView(DEFAULT_CENTER, DEFAULT_ZOOM);

        var tiles = [
            {
                url: 'https://{s}.basemaps.cartocdn.com/dark_matter_no_labels/{z}/{x}/{y}{r}.png',
                opts: { attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap', subdomains: 'abcd', maxZoom: 19 }
            },
            {
                url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
                opts: { attribution: 'Tiles &copy; Esri', maxZoom: 16 }
            },
            {
                url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
                opts: { attribution: '&copy; OpenStreetMap contributors', maxZoom: 18 }
            }
        ];
        (function tryTile(i) {
            if (i >= tiles.length) return;
            var t = tiles[i];
            var layer = L.tileLayer(t.url, t.opts);
            layer.once('tileerror', function () { map.removeLayer(layer); tryTile(i + 1); });
            layer.once('load', function () { layer.off('tileerror'); });
            layer.addTo(map);
        }(0));

        setTimeout(function () { if (map) map.invalidateSize(); }, 350);
    }

    // ─── Map overlay controls ─────────────────────────────────────────────────
    function wireMapControls() {
        // Near Me
        var nearMeBtn = document.getElementById('fmap-near-me');
        if (nearMeBtn) {
            nearMeBtn.addEventListener('click', function () {
                if (!navigator.geolocation) {
                    showToast('Geolocation not supported by your browser.');
                    return;
                }
                nearMeBtn.classList.add('fmap-ctrl-btn--loading');
                navigator.geolocation.getCurrentPosition(
                    function (pos) {
                        nearMeBtn.classList.remove('fmap-ctrl-btn--loading');
                        if (!map) return;
                        userCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
                        map.flyTo([userCoords.lat, userCoords.lng], 7, { duration: 1 });

                        // Show sort-by-distance button now that we have coords
                        var sortBtn = document.getElementById('fmap-sort-dist');
                        if (sortBtn) { sortBtn.hidden = false; }

                        // Re-render hotspot panel with distances
                        renderHotspots(currentData);

                        // Find closest active location to user
                        var best = null, bestDist = Infinity;
                        currentData.forEach(function (loc) {
                            if (loc.activity === 'none') return;
                            var d = haversineMi(userCoords.lat, userCoords.lng, loc.lat, loc.lng);
                            if (d < bestDist) { bestDist = d; best = loc; }
                        });
                        if (best) {
                            setTimeout(function () { selectLocation(best); }, 900);
                        }
                    },
                    function () {
                        nearMeBtn.classList.remove('fmap-ctrl-btn--loading');
                        showToast('Could not get your location.');
                    },
                    { timeout: 8000, maximumAge: 120000 }
                );
            });
        }

        // Reset view
        var resetBtn = document.getElementById('fmap-reset-view');
        if (resetBtn) {
            resetBtn.addEventListener('click', function () {
                if (!map) return;
                if (activeCoast !== 'all' && COAST_BOUNDS[activeCoast]) {
                    map.flyToBounds(COAST_BOUNDS[activeCoast], { padding: [30, 30], duration: 0.8 });
                } else {
                    map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { duration: 0.8 });
                }
            });
        }

        // Sort by distance toggle (visible after Near Me fires)
        var sortBtn = document.getElementById('fmap-sort-dist');
        if (sortBtn) {
            sortBtn.addEventListener('click', function () {
                sortByDist = !sortByDist;
                sortBtn.classList.toggle('fmap-sort-dist-btn--active', sortByDist);
                renderHotspots(currentData);
            });
        }
    }

    // ─── Toast ────────────────────────────────────────────────────────────────
    function showToast(msg) {
        var t = document.createElement('div');
        t.className = 'fmap-toast';
        t.textContent = msg;
        document.body.appendChild(t);
        requestAnimationFrame(function () {
            requestAnimationFrame(function () { t.classList.add('fmap-toast--in'); });
        });
        setTimeout(function () {
            t.classList.remove('fmap-toast--in');
            setTimeout(function () { t.parentNode && t.parentNode.removeChild(t); }, 400);
        }, 3000);
    }

    // ─── Custom marker icon ───────────────────────────────────────────────────
    function makeIcon(activity, isSelected) {
        var cfg  = ACTIVITY[activity] || ACTIVITY.none;
        var size = cfg.size + (isSelected ? 3 : 0);
        var pulse = (activity === 'peak' || activity === 'good') && !isSelected;
        var border = isSelected ? '2.5px solid #fff' : '1.5px solid rgba(0,0,0,0.4)';
        var shadow = isSelected
            ? '0 0 0 3px rgba(255,255,255,0.3), 0 0 ' + size + 'px ' + cfg.ring
            : '0 0 ' + size + 'px ' + cfg.ring;
        var ring = pulse
            ? '<span class="fmap-pulse" style="background:' + cfg.ring +
              ';width:' + (size * 3.2) + 'px;height:' + (size * 3.2) + 'px' +
              ';margin-left:' + (-(size * 1.1)) + 'px;margin-top:' + (-(size * 1.1)) + 'px"></span>'
            : '';
        var html = ring +
            '<span class="fmap-dot" style="width:' + (size * 2) + 'px;height:' + (size * 2) +
            'px;background:' + cfg.color + ';border:' + border + ';box-shadow:' + shadow + '"></span>';
        return L.divIcon({
            className: 'fmap-marker-wrap',
            html: html,
            iconSize:    [size * 2, size * 2],
            iconAnchor:  [size, size],
            popupAnchor: [0, -size - 2]
        });
    }

    // ─── Render markers ───────────────────────────────────────────────────────
    function clearMarkers() {
        markers.forEach(function (m) { map.removeLayer(m.leaflet); });
        markers = [];
    }

    function drawMarkers(locations) {
        if (!map) return;
        clearMarkers();

        locations.forEach(function (loc) {
            var isSel = loc.id === selectedId;
            var m = L.marker([loc.lat, loc.lng], {
                icon:  makeIcon(loc.activity, isSel),
                title: loc.name + ', ' + loc.state
            }).addTo(map);

            m.on('click', function () { selectLocation(loc); });

            var tipLabel = (ACTIVITY[loc.activity] || ACTIVITY.none).label;
            m.bindTooltip(
                '<strong>' + esc(loc.name) + '</strong>, ' + esc(loc.state) +
                '<br><span class="fmap-tip-badge fmap-tip-' + esc(loc.activity) + '">' + tipLabel + '</span>',
                { direction: 'top', offset: [0, -6], className: 'fmap-tooltip' }
            );

            markers.push({ id: loc.id, leaflet: m, data: loc });
        });
    }

    // ─── Location selection ───────────────────────────────────────────────────
    function selectLocation(loc) {
        selectedId = loc.id;

        // Re-render markers — selected one gets highlighted icon
        markers.forEach(function (m) {
            m.leaflet.setIcon(makeIcon(m.data.activity, m.id === loc.id));
        });

        map.flyTo([loc.lat, loc.lng], Math.max(map.getZoom(), 7), { duration: 0.55 });

        // ── Detail drawer ──────────────────────────────────────────────────────
        var cfg = ACTIVITY[loc.activity] || ACTIVITY.none;

        els.detailName.textContent = loc.name + ', ' + loc.state;
        els.detailMeta.textContent =
            (loc.coast === 'east' ? 'East & Gulf Coast' :
             loc.coast === 'west' ? 'West Coast' : 'Hawaii');

        els.detailBadge.textContent = cfg.label;
        els.detailBadge.className = 'fmap-detail-badge fmap-detail-badge--' + loc.activity;

        // Build species rows — each has activity badge, name, expandable bait section
        var speciesHtml = '';
        var species = loc.top_species || [];
        if (species.length) {
            species.forEach(function (sp) {
                var spName = typeof sp === 'string' ? sp : (sp.name || '');
                var spBait  = typeof sp === 'object' ? (sp.bait || '') : '';
                var spRig   = typeof sp === 'object' ? (sp.rig  || '') : '';
                var spLure  = typeof sp === 'object' ? (sp.lures || '') : '';
                var spAct   = typeof sp === 'object' ? (sp.activity || loc.activity) : loc.activity;
                var spPeak  = typeof sp === 'object' ? (sp.peak_months || []) : [];
                var spGood  = typeof sp === 'object' ? (sp.good_months || []) : [];
                var spCfg   = ACTIVITY[spAct] || ACTIVITY.none;
                var hasInfo = spBait || spRig || spLure || spPeak.length;
                var uid = 'fmap-sp-' + spName.replace(/\W/g, '_');

                speciesHtml +=
                    '<div class="fmap-sp-row' + (hasInfo ? ' fmap-sp-row--expand' : '') + '"' +
                    (hasInfo ? ' onclick="(function(el){el.classList.toggle(\'open\')})(this)"' : '') + '>' +
                    '<span class="fmap-sp-act fmap-sp-act--' + spAct + '"></span>' +
                    '<span class="fmap-sp-name">' + esc(spName) + '</span>' +
                    (hasInfo ? '<svg class="fmap-sp-chevron" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>' : '') +
                    (hasInfo ?
                        '<div class="fmap-sp-info">' +
                        (spPeak.length ? '<div class="fmap-sp-info-row fmap-sp-info-row--spark"><span class="fmap-sp-info-lbl">Season</span><span class="fmap-sp-info-val">' + buildSparkline(spPeak, spGood) + '</span></div>' : '') +
                        (spBait ? '<div class="fmap-sp-info-row"><span class="fmap-sp-info-lbl">Bait</span><span class="fmap-sp-info-val">' + esc(spBait) + '</span></div>' : '') +
                        (spRig  ? '<div class="fmap-sp-info-row"><span class="fmap-sp-info-lbl">Rig</span><span class="fmap-sp-info-val">'  + esc(spRig)  + '</span></div>' : '') +
                        (spLure ? '<div class="fmap-sp-info-row"><span class="fmap-sp-info-lbl">Lures</span><span class="fmap-sp-info-val">' + esc(spLure) + '</span></div>' : '') +
                        '</div>'
                    : '') +
                    '</div>';
            });
        } else {
            speciesHtml = '<p class="fmap-no-species">No active species match for this month.</p>';
        }
        els.detailSpecies.innerHTML = speciesHtml;

        els.detailActions.innerHTML =
            '<a href="/f/' + esc(loc.id) + '" class="fmap-forecast-btn">' +
            '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>' +
            ' View Full Forecast</a>';

        els.detail.hidden = false;

        // Update the hotspot panel to mark selected
        renderHotspots(currentData);

        // Scroll detail into view on mobile
        if (window.innerWidth < 768) {
            setTimeout(function () {
                els.detail.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }, 200);
        }
    }

    function closeDetail() {
        selectedId = null;
        els.detail.hidden = true;
        markers.forEach(function (m) {
            m.leaflet.setIcon(makeIcon(m.data.activity, false));
        });
        renderHotspots(currentData);
    }

    // ─── Hotspots list ────────────────────────────────────────────────────────
    function renderHotspots(locations) {
        if (!els.hotspotsList) return;

        var active = locations.filter(function (l) { return l.activity !== 'none'; });

        if (sortByDist && userCoords) {
            active.sort(function (a, b) {
                var da = haversineMi(userCoords.lat, userCoords.lng, a.lat, a.lng);
                var db = haversineMi(userCoords.lat, userCoords.lng, b.lat, b.lng);
                return da - db;
            });
        } else {
            active.sort(function (a, b) { return b.score - a.score; });
        }

        var top = active.slice(0, 8);

        // Update count badge
        if (els.hotspotCount) {
            els.hotspotCount.textContent = top.length ? top.length : '';
            els.hotspotCount.style.display = top.length ? '' : 'none';
        }

        if (!top.length) {
            // Empty state: show fuzzy "did you mean?" when a species is searched
            var emptyHtml = '<li class="fmap-hotspot-empty">';
            if (activeSpecies && allSpecies.length) {
                var lower = activeSpecies.toLowerCase();
                var suggestions = allSpecies
                    .map(function (n) { return { name: n, dist: levenshtein(lower, n.toLowerCase().slice(0, lower.length + 3)) }; })
                    .filter(function (x) { return x.dist <= 3; })
                    .sort(function (a, b) { return a.dist - b.dist; })
                    .slice(0, 3)
                    .map(function (x) { return x.name; });
                if (suggestions.length) {
                    emptyHtml += 'No spots for \u201c' + esc(activeSpecies) + '\u201d';
                    emptyHtml += '<div class="fmap-did-you-mean">Did you mean: ';
                    emptyHtml += suggestions.map(function (s) {
                        return '<button type="button" class="fmap-dym-btn" data-sp="' + esc(s) + '">' + esc(s) + '</button>';
                    }).join(', ');
                    emptyHtml += '</div>';
                } else {
                    emptyHtml += 'No active spots for \u201c' + esc(activeSpecies) + '\u201d';
                }
            } else {
                emptyHtml += 'No active spots for this filter';
            }
            emptyHtml += '</li>';
            els.hotspotsList.innerHTML = emptyHtml;

            // Wire "did you mean" buttons
            els.hotspotsList.querySelectorAll('.fmap-dym-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var sp = btn.getAttribute('data-sp');
                    activeSpecies = sp;
                    if (els.speciesInput) els.speciesInput.value = sp;
                    if (els.searchClear) els.searchClear.hidden = false;
                    renderQuickChips();
                    scheduleFetch();
                });
            });
            return;
        }

        var html = '';
        top.forEach(function (loc, i) {
            var cfg   = ACTIVITY[loc.activity] || ACTIVITY.none;
            var sp    = loc.top_species && loc.top_species[0];
            var spName = sp ? (typeof sp === 'string' ? sp : sp.name || '') : '';
            var distHtml = '';
            if (userCoords) {
                var mi = haversineMi(userCoords.lat, userCoords.lng, loc.lat, loc.lng);
                distHtml = '<span class="fmap-hotspot-dist">' +
                    (mi < 10 ? mi.toFixed(1) : Math.round(mi)) + ' mi</span>';
            }
            html +=
                '<li class="fmap-hotspot-item' + (loc.id === selectedId ? ' fmap-hotspot-item--sel' : '') +
                '" data-loc-id="' + esc(loc.id) + '">' +
                '<span class="fmap-hotspot-rank">' + (i + 1) + '</span>' +
                '<span class="fmap-hotspot-dot" style="background:' + cfg.color + ';box-shadow:0 0 5px ' + cfg.ring + '"></span>' +
                '<span class="fmap-hotspot-info">' +
                  '<span class="fmap-hotspot-name">' + esc(loc.name) + ', ' + esc(loc.state) + '</span>' +
                  (spName ? '<span class="fmap-hotspot-sp">' + esc(spName) + '</span>' : '') +
                '</span>' +
                distHtml +
                '<span class="fmap-hotspot-badge fmap-hotspot-badge--' + loc.activity + '">' + cfg.label + '</span>' +
                '</li>';
        });
        els.hotspotsList.innerHTML = html;

        els.hotspotsList.querySelectorAll('.fmap-hotspot-item').forEach(function (li) {
            li.addEventListener('click', function () {
                var id  = li.getAttribute('data-loc-id');
                var loc = currentData.find(function (l) { return l.id === id; });
                if (loc) selectLocation(loc);
            });
        });
    }

    // ─── AI Insight text ──────────────────────────────────────────────────────
    function updateInsight(data) {
        if (!els.insight || !els.insightText) return;
        var month    = data.month;
        var locs     = data.locations || [];
        var peak     = locs.filter(function (l) { return l.activity === 'peak'; }).length;
        var good     = locs.filter(function (l) { return l.activity === 'good'; }).length;
        var total    = locs.length;
        var monthName = MONTH_NAMES[month] || '';

        var text = '';
        if (activeSpecies) {
            var spName = activeSpecies.charAt(0).toUpperCase() + activeSpecies.slice(1);
            if (peak > 0) {
                text = peak + ' location' + (peak !== 1 ? 's are' : ' is') +
                       ' showing peak ' + spName + ' activity in ' + monthName + '.';
                if (good > 0) text += ' ' + good + ' more at good level.';
            } else if (good > 0) {
                text = (peak + good) + ' location' + ((peak + good) !== 1 ? 's show' : ' shows') +
                       ' good ' + spName + ' fishing this month.';
            } else if (total > 0) {
                text = spName + ' activity is slow for ' + monthName +
                       '. Try searching for a species with spring or fall peak months.';
            } else {
                text = 'No locations found for \u201c' + activeSpecies + '\u201d. Try a partial name search.';
            }
        } else {
            var active = peak + good;
            text = monthName + ': ' + active + ' of ' + total +
                   ' locations showing good or peak activity.';
            if (peak > 0) text += ' ' + peak + ' at peak — tap a green pin for details.';
        }

        els.insightText.textContent = text;
        els.insight.hidden = false;
    }

    // ─── Trending Now chips ───────────────────────────────────────────────────
    function renderTrendingChips(names) {
        var wrap  = document.getElementById('fmap-trending');
        var chips = document.getElementById('fmap-trending-chips');
        if (!wrap || !chips) return;

        // Hide when user has already set a species filter (trending is irrelevant)
        if (!names || !names.length || activeSpecies) {
            wrap.hidden = true;
            return;
        }

        var html = '';
        names.forEach(function (sp) {
            var active = activeSpecies && activeSpecies.toLowerCase() === sp.toLowerCase();
            html += '<button type="button" class="fmap-chip fmap-chip--trending' +
                    (active ? ' fmap-chip--active' : '') +
                    '" data-sp="' + esc(sp) + '">' + esc(sp) + '</button>';
        });
        chips.innerHTML = html;
        wrap.hidden = false;

        chips.querySelectorAll('.fmap-chip').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var sp = btn.getAttribute('data-sp');
                if (activeSpecies && activeSpecies.toLowerCase() === sp.toLowerCase()) {
                    activeSpecies = '';
                    if (els.speciesInput) els.speciesInput.value = '';
                    if (els.searchClear) els.searchClear.hidden = true;
                } else {
                    activeSpecies = sp;
                    if (els.speciesInput) els.speciesInput.value = sp;
                    if (els.searchClear) els.searchClear.hidden = false;
                }
                renderQuickChips();
                scheduleFetch();
            });
        });
    }

    // ─── Quick species chips ──────────────────────────────────────────────────
    function renderQuickChips() {
        if (!els.chips) return;
        var list = QUICK_SPECIES[activeCoast] || QUICK_SPECIES.all;
        var html = '';
        list.forEach(function (sp) {
            var active = activeSpecies && activeSpecies.toLowerCase() === sp.toLowerCase();
            html += '<button type="button" class="fmap-chip' + (active ? ' fmap-chip--active' : '') +
                    '" data-sp="' + esc(sp) + '">' + esc(sp) + '</button>';
        });
        els.chips.innerHTML = html;
        els.chips.querySelectorAll('.fmap-chip').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var sp = btn.getAttribute('data-sp');
                if (activeSpecies && activeSpecies.toLowerCase() === sp.toLowerCase()) {
                    activeSpecies = '';
                    if (els.speciesInput)  { els.speciesInput.value = ''; }
                    if (els.searchClear) els.searchClear.hidden = true;
                } else {
                    activeSpecies = sp;
                    if (els.speciesInput)  { els.speciesInput.value = sp; }
                    if (els.searchClear) els.searchClear.hidden = false;
                }
                renderQuickChips();
                scheduleFetch();
            });
        });
    }

    // ─── Autocomplete ─────────────────────────────────────────────────────────
    function showSuggestions(q) {
        if (!els.suggestions || !q || q.length < 2) { hideSuggestions(); return; }
        var lower = q.toLowerCase();
        var hits  = allSpecies.filter(function (n) {
            return n.toLowerCase().indexOf(lower) !== -1;
        }).slice(0, 10);
        if (!hits.length) { hideSuggestions(); return; }
        var html = '';
        hits.forEach(function (n) {
            var idx    = n.toLowerCase().indexOf(lower);
            var before = esc(n.slice(0, idx));
            var match  = esc(n.slice(idx, idx + q.length));
            var after  = esc(n.slice(idx + q.length));
            html += '<li role="option" tabindex="-1">' + before + '<mark>' + match + '</mark>' + after + '</li>';
        });
        els.suggestions.innerHTML = html;
        els.suggestions.hidden = false;
        if (els.speciesInput) els.speciesInput.setAttribute('aria-expanded', 'true');

        els.suggestions.querySelectorAll('li').forEach(function (li) {
            li.addEventListener('mousedown', function (e) {
                e.preventDefault();
                var text = li.textContent;
                activeSpecies = text;
                if (els.speciesInput) els.speciesInput.value = text;
                if (els.searchClear) els.searchClear.hidden = false;
                renderQuickChips();
                hideSuggestions();
                scheduleFetch();
            });
        });
    }
    function hideSuggestions() {
        if (els.suggestions) els.suggestions.hidden = true;
        if (els.speciesInput) els.speciesInput.setAttribute('aria-expanded', 'false');
    }

    // ─── localStorage persistence ─────────────────────────────────────────────
    function saveFilters() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                species: activeSpecies,
                coast:   activeCoast,
                cat:     activeCat
            }));
        } catch (e) {}
    }

    function loadFilters() {
        try {
            var raw = localStorage.getItem(LS_KEY);
            if (!raw) return;
            var f = JSON.parse(raw);
            if (f.species) {
                activeSpecies = f.species;
                if (els.speciesInput)  els.speciesInput.value = f.species;
                if (els.searchClear) els.searchClear.hidden = false;
            }
            if (f.coast && f.coast !== 'all') {
                activeCoast = f.coast;
                document.querySelectorAll('.fmap-pill--coast').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-coast') === f.coast);
                });
            }
            if (f.cat) {
                activeCat = f.cat;
                document.querySelectorAll('.fmap-pill--cat').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-cat') === f.cat);
                });
            }
        } catch (e) {}
    }

    // ─── Auto-center on saved location ───────────────────────────────────────
    var hasAutoZoomed = false;
    function autoZoomToSavedLocation(locations) {
        if (hasAutoZoomed) return;
        var locId = (typeof CURRENT_LOC_ID !== 'undefined') ? CURRENT_LOC_ID : '';
        if (!locId || !map) return;
        var match = locations.find(function (l) { return l.id === locId; });
        if (match) {
            hasAutoZoomed = true;
            map.setView([match.lat, match.lng], 9, { animate: false });
        }
    }

    // ─── Fetch & render ───────────────────────────────────────────────────────
    function fetchAndRender() {
        if (!map) return;
        saveFilters();

        var params = new URLSearchParams();
        if (activeSpecies) params.set('species', activeSpecies);
        if (activeCoast && activeCoast !== 'all') params.set('coast', activeCoast);
        if (activeCat) params.set('category', activeCat);
        if (activeMonth) params.set('month', String(activeMonth));

        var url = API_URL + (params.toString() ? '?' + params.toString() : '');

        if (els.loading) { els.loading.style.opacity = '1'; els.loading.style.pointerEvents = 'auto'; }

        fetch(url)
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (data) {
                if (els.loading) {
                    els.loading.style.opacity = '0';
                    setTimeout(function () { if (els.loading) els.loading.style.pointerEvents = 'none'; }, 300);
                }

                currentData = data.locations || [];

                if (allSpecies.length === 0 && data.species_names) {
                    allSpecies = data.species_names;
                }

                monthlySummary = data.monthly_summary || [];
                drawMarkers(currentData);
                autoZoomToSavedLocation(currentData);
                renderHotspots(currentData);
                renderMonthPlanner(monthlySummary, data.month);
                renderTrendingChips(data.trending_species || []);
                updateInsight(data);
            })
            .catch(function (err) {
                if (els.loading) {
                    els.loading.style.opacity = '0';
                    setTimeout(function () { if (els.loading) els.loading.style.pointerEvents = 'none'; }, 300);
                }
                console.error('[fishing-map] fetch error:', err);
            });
    }

    function scheduleFetch() {
        clearTimeout(fetchTimer);
        fetchTimer = setTimeout(fetchAndRender, 280);
    }

    // ─── Filter wiring ────────────────────────────────────────────────────────
    function wireFilters() {
        if (els.speciesInput) {
            els.speciesInput.addEventListener('input', function () {
                activeSpecies = els.speciesInput.value.trim();
                if (els.searchClear) els.searchClear.hidden = !activeSpecies;
                showSuggestions(activeSpecies);
                renderQuickChips();
                scheduleFetch();
            });
            els.speciesInput.addEventListener('change', function () {
                activeSpecies = els.speciesInput.value.trim();
                renderQuickChips();
                scheduleFetch();
            });
            els.speciesInput.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') hideSuggestions();
                if (e.key === 'Enter')  { hideSuggestions(); scheduleFetch(); }
            });
            els.speciesInput.addEventListener('blur', function () {
                setTimeout(hideSuggestions, 160);
            });
        }

        if (els.searchClear) {
            els.searchClear.addEventListener('click', function () {
                activeSpecies = '';
                if (els.speciesInput) els.speciesInput.value = '';
                els.searchClear.hidden = true;
                renderQuickChips();
                scheduleFetch();
            });
        }

        // Detail drawer close button
        var closeBtn = document.getElementById('fmap-detail-close');
        if (closeBtn) closeBtn.addEventListener('click', closeDetail);
    }

    // ─── Boot ─────────────────────────────────────────────────────────────────
    function boot() {
        ensureLeaflet()
            .then(function () {
                if (!window.L) throw new Error('Leaflet unavailable');
                initMap();
                restoreFromHash();
                loadFilters();
                renderQuickChips();
                wireFilters();
                wireMapControls();
                wireFullscreen();
                wireShareBtn();
                fetchAndRender();
            })
            .catch(function (err) {
                console.error('[fishing-map] boot error:', err);
                if (els.loading) els.loading.textContent = 'Map could not be loaded.';
            });
    }

    // ─── Month Planner ────────────────────────────────────────────────────────
    var MONTH_SHORT = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

    function renderMonthPlanner(summary, currentM) {
        var planner = document.getElementById('fmap-planner');
        var container = document.getElementById('fmap-planner-months');
        if (!planner || !container || !summary || !summary.length) return;

        // Find max combined active count for normalising bar heights
        var maxActive = 1;
        summary.forEach(function (m) {
            var total = m.peak + m.good + m.fair;
            if (total > maxActive) maxActive = total;
        });

        var html = '';
        summary.forEach(function (m) {
            var isCurrent = m.month === currentM;
            var isSelected = m.month === activeMonth;
            var total = m.peak + m.good + m.fair;
            var heightPct = Math.round((total / maxActive) * 100);
            var actClass = m.peak > 0 ? 'peak' : m.good > 0 ? 'good' : m.fair > 0 ? 'fair' : 'slow';

            html +=
                '<button type="button" class="fmap-month-cell' +
                (isCurrent  ? ' fmap-month-cell--current'  : '') +
                (isSelected ? ' fmap-month-cell--selected' : '') +
                '" data-month="' + m.month + '" title="' + MONTH_NAMES[m.month] +
                ': ' + m.peak + ' peak, ' + m.good + ' good, ' + m.fair + ' fair">' +
                '<div class="fmap-month-bar-wrap">' +
                  '<div class="fmap-month-bar fmap-month-bar--' + actClass +
                  '" style="height:' + Math.max(4, heightPct) + '%"></div>' +
                '</div>' +
                '<span class="fmap-month-label">' + MONTH_SHORT[m.month] + '</span>' +
                (isCurrent ? '<span class="fmap-month-now-dot"></span>' : '') +
                '</button>';
        });

        container.innerHTML = html;
        planner.hidden = false;

        container.querySelectorAll('.fmap-month-cell').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var m = parseInt(btn.getAttribute('data-month'), 10);
                activeMonth = (activeMonth === m) ? 0 : m;  // toggle off if same
                renderMonthPlanner(monthlySummary, currentM);
                fetchAndRender();
            });
        });
    }

    // ─── Sparkline helper (12-bar mini chart for species rows) ────────────────
    // Returns an SVG string showing peak months (dark), good (medium), other (faint)
    function buildSparkline(peakMonths, goodMonths) {
        var bars = '';
        for (var m = 1; m <= 12; m++) {
            var isPeak = peakMonths.indexOf(m) !== -1;
            var isGood = goodMonths.indexOf(m) !== -1;
            var fill = isPeak ? '#22c55e' : isGood ? '#3b82f6' : 'rgba(255,255,255,0.1)';
            var h    = isPeak ? 10 : isGood ? 7 : 3;
            var x    = (m - 1) * 5;
            var y    = 10 - h;
            bars += '<rect x="' + x + '" y="' + y + '" width="3.5" height="' + h + '" rx="1" fill="' + fill + '"/>';
        }
        return '<svg class="fmap-sparkline" viewBox="0 0 60 10" width="60" height="10" aria-hidden="true">' + bars + '</svg>';
    }

    // ─── Fullscreen map toggle ─────────────────────────────────────────────────
    function wireFullscreen() {
        var mapWrap = document.querySelector('.fmap-map-wrap');
        var fsMapBtn = document.getElementById('fmap-map-fullscreen');
        var fsToolbarBtn = document.getElementById('fmap-fullscreen-btn');
        var fsLabel = document.getElementById('fmap-fs-label');
        var fsIconExpand = document.getElementById('fmap-fs-icon-expand');
        var fsIconShrink = document.getElementById('fmap-fs-icon-shrink');

        function toggle() {
            isFullscreen = !isFullscreen;
            if (mapWrap) mapWrap.classList.toggle('fmap-map-wrap--fullscreen', isFullscreen);
            if (fsLabel) fsLabel.textContent = isFullscreen ? 'Shrink Map' : 'Expand Map';
            if (fsIconExpand) fsIconExpand.style.display = isFullscreen ? 'none' : '';
            if (fsIconShrink) fsIconShrink.style.display = isFullscreen ? '' : 'none';
            // Lock/unlock body scroll and update map size
            document.body.style.overflow = isFullscreen ? 'hidden' : '';
            setTimeout(function () { if (map) map.invalidateSize(); }, 300);
        }

        if (fsMapBtn) fsMapBtn.addEventListener('click', toggle);
        if (fsToolbarBtn) fsToolbarBtn.addEventListener('click', toggle);

        // Escape key exits fullscreen
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && isFullscreen) toggle();
        });
    }

    // ─── Share filter URL ─────────────────────────────────────────────────────
    function wireShareBtn() {
        var btn = document.getElementById('fmap-share-btn');
        if (!btn) return;
        btn.addEventListener('click', function () {
            var base = window.location.origin + window.location.pathname;
            var params = new URLSearchParams(window.location.search);
            // Encode current fishing map state into URL hash
            var hashParts = [];
            if (activeSpecies) hashParts.push('species=' + encodeURIComponent(activeSpecies));
            if (activeCoast && activeCoast !== 'all') hashParts.push('coast=' + activeCoast);
            if (activeCat)   hashParts.push('cat=' + activeCat);
            if (activeMonth) hashParts.push('month=' + activeMonth);
            var url = base + (params.toString() ? '?' + params.toString() : '') +
                      (hashParts.length ? '#fmap=' + hashParts.join('&') : '');
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(url)
                    .then(function () { showToast('Map link copied!'); })
                    .catch(function () { showToast('Could not copy: ' + url); });
            } else {
                showToast('Link: ' + url);
            }
        });
    }

    // ─── Restore state from URL hash (#fmap=...) ──────────────────────────────
    function restoreFromHash() {
        var hash = window.location.hash;
        if (!hash || hash.indexOf('#fmap=') !== 0) return;
        var raw = hash.slice(6); // strip '#fmap='
        raw.split('&').forEach(function (part) {
            var eq = part.indexOf('=');
            if (eq === -1) return;
            var k = part.slice(0, eq);
            var v = decodeURIComponent(part.slice(eq + 1));
            if (k === 'species' && v) {
                activeSpecies = v;
                if (els.speciesInput) els.speciesInput.value = v;
                if (els.searchClear) els.searchClear.hidden = false;
            }
            if (k === 'coast' && v) {
                activeCoast = v;
                document.querySelectorAll('.fmap-pill--coast').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-coast') === v);
                });
            }
            if (k === 'cat' && v) {
                activeCat = v;
                document.querySelectorAll('.fmap-pill--cat').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-cat') === v);
                });
            }
            if (k === 'month' && v) {
                activeMonth = parseInt(v, 10) || 0;
            }
        });
    }

    // ─── Init ─────────────────────────────────────────────────────────────────
    function init() {
        var root = document.getElementById('fmap-root');
        if (!root) return;

        els.mapEl         = document.getElementById('fishing-map-el');
        els.loading       = document.getElementById('fmap-loading');
        els.detail        = document.getElementById('fmap-detail');
        els.detailName    = document.getElementById('fmap-detail-name');
        els.detailMeta    = document.getElementById('fmap-detail-meta');
        els.detailBadge   = document.getElementById('fmap-detail-badge');
        els.detailSpecies = document.getElementById('fmap-detail-species');
        els.detailActions = document.getElementById('fmap-detail-actions');
        els.hotspotsList  = document.getElementById('fmap-hotspots-list');
        els.hotspotCount  = document.getElementById('fmap-hotspot-count');
        els.chips         = document.getElementById('fmap-chips');
        els.speciesInput  = document.getElementById('fmap-species-input');
        els.searchClear   = document.getElementById('fmap-search-clear');
        els.suggestions   = document.getElementById('fmap-suggestions');
        els.insight       = document.getElementById('fmap-insight');
        els.insightText   = document.getElementById('fmap-insight-text');

        if (!els.mapEl) return;
        boot();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
