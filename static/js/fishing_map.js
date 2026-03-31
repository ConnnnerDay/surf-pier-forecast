(function () {
    'use strict';

    // ── Constants ─────────────────────────────────────────────────────────────
    var API_URL = '/api/fishing-map';
    var DEFAULT_LAT = 35.0;
    var DEFAULT_LNG = -90.0;
    var DEFAULT_ZOOM = 4;

    var ACTIVITY_COLORS = {
        peak: { fill: '#22c55e', stroke: '#15803d' },
        good: { fill: '#3b82f6', stroke: '#1d4ed8' },
        fair: { fill: '#f59e0b', stroke: '#b45309' },
        slow: { fill: '#ef4444', stroke: '#b91c1c' },
        none: { fill: '#6b7280', stroke: '#374151' }
    };

    var TILE_PROVIDERS = [
        {
            url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
            options: {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/">CARTO</a>',
                subdomains: 'abcd',
                maxZoom: 18
            }
        },
        {
            url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            options: {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                maxZoom: 18
            }
        },
        {
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
            options: {
                attribution: 'Tiles &copy; Esri',
                maxZoom: 16
            }
        }
    ];

    // ── State ─────────────────────────────────────────────────────────────────
    var map = null;
    var markers = [];
    var allSpeciesNames = [];
    var fetchTimer = null;
    var currentLocations = [];
    var mapInitialised = false;

    // ── DOM refs (populated in init) ──────────────────────────────────────────
    var mapEl, loadingEl, statusEl, speciesInput, speciesSuggestions,
        coastSelect, categorySelect, clearBtn;

    // ── Leaflet loader (same pattern as setup_map.js) ─────────────────────────
    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            var existing = document.querySelector('script[src="' + src + '"]');
            if (existing) {
                if (window.L) { resolve(); return; }
                existing.addEventListener('load', resolve, { once: true });
                existing.addEventListener('error', reject, { once: true });
                return;
            }
            var s = document.createElement('script');
            s.src = src; s.async = true;
            s.onload = resolve; s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    function ensureLeafletCss() {
        if (document.querySelector('link[data-fmap-leaflet="1"]')) return;
        var link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css';
        link.setAttribute('data-fmap-leaflet', '1');
        document.head.appendChild(link);
    }

    function ensureLeaflet() {
        if (window.L) return Promise.resolve();
        ensureLeafletCss();
        return loadScript('https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js')
            .catch(function () {
                return loadScript('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js');
            });
    }

    // ── Tile layer with fallback ───────────────────────────────────────────────
    function addTileLayer(mapObj) {
        var idx = 0;
        function tryNext() {
            if (idx >= TILE_PROVIDERS.length) return;
            var p = TILE_PROVIDERS[idx++];
            var layer = L.tileLayer(p.url, p.options);
            layer.once('tileerror', function () {
                mapObj.removeLayer(layer);
                tryNext();
            });
            layer.once('load', function () { layer.off('tileerror'); });
            layer.addTo(mapObj);
        }
        tryNext();
    }

    // ── HTML escaping ─────────────────────────────────────────────────────────
    function esc(str) {
        return String(str || '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── Map initialisation ────────────────────────────────────────────────────
    function initMap() {
        if (mapInitialised) return;
        mapInitialised = true;

        map = L.map(mapEl, { zoomControl: true }).setView([DEFAULT_LAT, DEFAULT_LNG], DEFAULT_ZOOM);
        addTileLayer(map);

        // Make map aware of container size after CSS transition completes
        setTimeout(function () { map.invalidateSize(); }, 300);
    }

    // ── Marker management ─────────────────────────────────────────────────────
    function clearMarkers() {
        markers.forEach(function (m) { map.removeLayer(m); });
        markers = [];
    }

    function activityLabel(activity) {
        var labels = { peak: 'Peak', good: 'Good', fair: 'Fair', slow: 'Slow', none: 'N/A' };
        return labels[activity] || activity;
    }

    function buildPopupHtml(loc) {
        var color = ACTIVITY_COLORS[loc.activity] || ACTIVITY_COLORS.none;
        var badge = '<span class="fmap-popup-badge fmap-popup-badge--' + esc(loc.activity) + '">' +
                    activityLabel(loc.activity) + '</span>';
        var html = '<div class="fmap-popup">' +
            '<div class="fmap-popup-title">' + esc(loc.name) + ', ' + esc(loc.state) + ' ' + badge + '</div>';

        if (loc.top_species && loc.top_species.length) {
            html += '<div class="fmap-popup-species-hd">Fishing now:</div><ul class="fmap-popup-species">';
            loc.top_species.forEach(function (sp) {
                html += '<li>' + esc(sp) + '</li>';
            });
            html += '</ul>';
        } else {
            html += '<p class="fmap-popup-none">No active species match for this month.</p>';
        }

        html += '<a href="/f/' + esc(loc.id) + '" class="fmap-popup-link">View forecast &rarr;</a>';
        html += '</div>';
        return html;
    }

    function drawMarkers(locations) {
        if (!map) return;
        clearMarkers();
        currentLocations = locations;

        locations.forEach(function (loc) {
            var c = ACTIVITY_COLORS[loc.activity] || ACTIVITY_COLORS.none;
            var radius = loc.activity === 'peak' ? 9 :
                         loc.activity === 'good' ? 7 : 6;

            var marker = L.circleMarker([loc.lat, loc.lng], {
                radius: radius,
                color: c.stroke,
                fillColor: c.fill,
                fillOpacity: 0.85,
                weight: 1.5
            });

            marker.bindPopup(buildPopupHtml(loc), {
                maxWidth: 240,
                className: 'fmap-leaflet-popup'
            });

            marker.bindTooltip(esc(loc.name) + ', ' + esc(loc.state), {
                direction: 'top',
                offset: [0, -4]
            });

            marker.addTo(map);
            markers.push(marker);
        });
    }

    // ── API fetch ─────────────────────────────────────────────────────────────
    function fetchMapData() {
        if (!map) return;

        var params = new URLSearchParams();
        var species = speciesInput ? speciesInput.value.trim() : '';
        var coast = coastSelect ? coastSelect.value : 'all';
        var category = categorySelect ? categorySelect.value : '';

        if (species) params.set('species', species);
        if (coast && coast !== 'all') params.set('coast', coast);
        if (category) params.set('category', category);

        var url = API_URL + (params.toString() ? '?' + params.toString() : '');

        if (loadingEl) loadingEl.style.display = 'flex';
        if (statusEl) statusEl.textContent = '';

        fetch(url)
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) {
                if (loadingEl) loadingEl.style.display = 'none';
                drawMarkers(data.locations || []);

                // Populate species autocomplete on first load
                if (allSpeciesNames.length === 0 && data.species_names) {
                    allSpeciesNames = data.species_names;
                }

                var shown = (data.locations || []).length;
                var peak = (data.locations || []).filter(function (l) { return l.activity === 'peak'; }).length;
                var good = (data.locations || []).filter(function (l) { return l.activity === 'good'; }).length;

                var msg = shown + ' location' + (shown !== 1 ? 's' : '');
                if (species) {
                    msg += ' for "' + esc(species) + '"';
                }
                if (peak || good) {
                    msg += ' &mdash; ' + (peak + good) + ' with active fishing';
                }
                if (statusEl) statusEl.innerHTML = msg;
            })
            .catch(function (err) {
                if (loadingEl) loadingEl.style.display = 'none';
                if (statusEl) statusEl.textContent = 'Could not load map data. Try refreshing.';
                console.error('fishing-map fetch error:', err);
            });
    }

    function scheduleFetch() {
        clearTimeout(fetchTimer);
        fetchTimer = setTimeout(fetchMapData, 300);
    }

    // ── Autocomplete ──────────────────────────────────────────────────────────
    function showSuggestions(query) {
        if (!speciesSuggestions || !query || query.length < 2) {
            hideSuggestions();
            return;
        }
        var q = query.toLowerCase();
        var matches = allSpeciesNames.filter(function (n) {
            return n.toLowerCase().indexOf(q) !== -1;
        }).slice(0, 8);

        if (!matches.length) { hideSuggestions(); return; }

        speciesSuggestions.innerHTML = '';
        matches.forEach(function (name) {
            var li = document.createElement('li');
            li.setAttribute('role', 'option');
            li.textContent = name;
            li.addEventListener('mousedown', function (e) {
                e.preventDefault();
                speciesInput.value = name;
                hideSuggestions();
                scheduleFetch();
            });
            speciesSuggestions.appendChild(li);
        });
        speciesSuggestions.hidden = false;
        speciesInput.setAttribute('aria-expanded', 'true');
    }

    function hideSuggestions() {
        if (speciesSuggestions) {
            speciesSuggestions.hidden = true;
            speciesSuggestions.innerHTML = '';
        }
        if (speciesInput) speciesInput.setAttribute('aria-expanded', 'false');
    }

    // ── Intersection Observer — lazy-init the map ─────────────────────────────
    function lazyInitMap() {
        ensureLeaflet()
            .then(function () {
                if (!window.L) return;
                initMap();
                fetchMapData();
            })
            .catch(function (err) {
                console.error('Leaflet load error:', err);
                if (loadingEl) loadingEl.textContent = 'Map libraries could not be loaded.';
            });
    }

    function observeSection(sectionEl) {
        if (!('IntersectionObserver' in window)) {
            // Fallback: init immediately
            lazyInitMap();
            return;
        }
        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    observer.disconnect();
                    lazyInitMap();
                }
            });
        }, { threshold: 0.05 });
        observer.observe(sectionEl);
    }

    // ── Init ──────────────────────────────────────────────────────────────────
    function init() {
        var section = document.querySelector('.fishing-map-section');
        if (!section) return;

        mapEl             = document.getElementById('fishing-map-el');
        loadingEl         = document.getElementById('fmap-loading');
        statusEl          = document.getElementById('fmap-status');
        speciesInput      = document.getElementById('fmap-species-input');
        speciesSuggestions = document.getElementById('fmap-species-suggestions');
        coastSelect       = document.getElementById('fmap-coast-select');
        categorySelect    = document.getElementById('fmap-category-select');
        clearBtn          = document.getElementById('fmap-clear-btn');

        if (!mapEl) return;

        // Filter change handlers
        if (speciesInput) {
            speciesInput.addEventListener('input', function () {
                showSuggestions(speciesInput.value.trim());
                scheduleFetch();
            });
            speciesInput.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') hideSuggestions();
            });
            speciesInput.addEventListener('blur', function () {
                // Delay to allow mousedown on suggestion to fire first
                setTimeout(hideSuggestions, 150);
            });
        }

        if (coastSelect) {
            coastSelect.addEventListener('change', scheduleFetch);
        }
        if (categorySelect) {
            categorySelect.addEventListener('change', scheduleFetch);
        }
        if (clearBtn) {
            clearBtn.addEventListener('click', function () {
                if (speciesInput) speciesInput.value = '';
                if (coastSelect) coastSelect.value = 'all';
                if (categorySelect) categorySelect.value = '';
                hideSuggestions();
                scheduleFetch();
            });
        }

        // Lazy-load map when the section scrolls into view
        observeSection(section);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
