(function () {
    'use strict';

    var DEFAULT_LAT = 35.0;
    var DEFAULT_LNG = -77.0;
    var DEFAULT_ZOOM = 5;

    var BASE_LAYERS = {
        'Street': {
            url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            options: {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                maxZoom: 18
            }
        },
        'Satellite': {
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            options: {
                attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, GeoEye, Earthstar Geographics, CNES/Airbus DS, USDA, USGS, AeroGRID, IGN',
                maxZoom: 18
            }
        },
        'Topo': {
            url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
            options: {
                attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
                subdomains: 'abcd',
                maxZoom: 20
            }
        }
    };

    var NE_COASTLINES_URL = '/api/v1/geo/coastlines?res=110m';

    var NE_STYLE = {
        color: '#1a7fbf',
        weight: 1.2,
        opacity: 0.7,
        fillOpacity: 0
    };

    function parseLocations(mapEl) {
        var raw = mapEl.getAttribute('data-supported-locations') || '[]';
        try {
            var parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed.filter(function (loc) {
                return loc && Number.isFinite(Number(loc.lat)) && Number.isFinite(Number(loc.lng));
            }) : [];
        } catch (err) {
            return [];
        }
    }

    function findNearest(locations, lat, lng) {
        var nearest = null;
        var best = Infinity;
        locations.forEach(function (loc) {
            var dLat = Number(loc.lat) - lat;
            var dLng = Number(loc.lng) - lng;
            var score = (dLat * dLat) + (dLng * dLng);
            if (score < best) {
                best = score;
                nearest = loc;
            }
        });
        return nearest;
    }

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            var existing = document.querySelector('script[src="' + src + '"]');
            if (existing) {
                if (window.L) { resolve(); return; }
                existing.addEventListener('load', function () { resolve(); }, { once: true });
                existing.addEventListener('error', function () { reject(new Error('script load failed')); }, { once: true });
                return;
            }
            var script = document.createElement('script');
            script.src = src;
            script.async = true;
            script.onload = function () { resolve(); };
            script.onerror = function () { reject(new Error('script load failed')); };
            document.head.appendChild(script);
        });
    }

    function ensureLeafletCss() {
        if (document.querySelector('link[data-leaflet-fallback="1"]')) return;
        var link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css';
        link.setAttribute('data-leaflet-fallback', '1');
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

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function showMapError(mapEl, confirmEl) {
        mapEl.classList.add('map-fallback');
        mapEl.innerHTML =
            '<p><strong>Map unavailable.</strong> Could not load map libraries from CDN providers.</p>' +
            '<p>Use the search bar above to find your location.</p>';
        if (confirmEl) confirmEl.hidden = true;
    }

    /* Directly POST to /setup/select/{id} — same as clicking a search result */
    function submitLocation(locationId, csrfToken) {
        var form = document.createElement('form');
        form.method = 'post';
        form.action = '/setup/select/' + encodeURIComponent(locationId);
        var csrfField = document.createElement('input');
        csrfField.type = 'hidden';
        csrfField.name = 'csrf_token';
        csrfField.value = csrfToken;
        form.appendChild(csrfField);
        document.body.appendChild(form);
        form.submit();
    }

    function buildMap(mapEl, locations, csrfToken, confirmEl) {
        var map = L.map(mapEl).setView([DEFAULT_LAT, DEFAULT_LNG], DEFAULT_ZOOM);

        // Build named base layer instances
        var leafletBaseLayers = {};
        var firstLayer = null;
        Object.keys(BASE_LAYERS).forEach(function (name) {
            var cfg = BASE_LAYERS[name];
            var layer = L.tileLayer(cfg.url, cfg.options);
            leafletBaseLayers[name] = layer;
            if (!firstLayer) {
                firstLayer = layer;
                layer.addTo(map);
            }
        });

        // Fetch Natural Earth coastlines overlay from local API, then wire layer control
        fetch(NE_COASTLINES_URL)
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (geojson) {
                var overlays = {};
                if (geojson && geojson.features) {
                    overlays['Coastlines'] = L.geoJSON(geojson, { style: NE_STYLE });
                }
                L.control.layers(leafletBaseLayers, overlays, {
                    position: 'topright',
                    collapsed: false
                }).addTo(map);
            })
            .catch(function () {
                L.control.layers(leafletBaseLayers, {}, {
                    position: 'topright',
                    collapsed: false
                }).addTo(map);
            });

        var pendingLoc = null;

        /* Show the inline confirm bar below the map when clicking open ocean */
        function showConfirm(loc) {
            pendingLoc = loc;
            if (!confirmEl) return;
            confirmEl.hidden = false;
            var nameEl = confirmEl.querySelector('[data-map-confirm-name]');
            var stateEl = confirmEl.querySelector('[data-map-confirm-state]');
            if (nameEl) nameEl.textContent = loc.name;
            if (stateEl) stateEl.textContent = loc.state;
        }

        var bounds = [];
        locations.forEach(function (loc) {
            var lat = Number(loc.lat);
            var lng = Number(loc.lng);
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
            bounds.push([lat, lng]);

            var marker = L.circleMarker([lat, lng], {
                radius: 6,
                weight: 1.5,
                color: '#0e5f78',
                fillColor: '#1285a6',
                fillOpacity: 0.85
            }).addTo(map);

            /* Single click on a named marker = immediate selection, no extra step */
            marker.on('click', function (e) {
                L.DomEvent.stopPropagation(e);
                submitLocation(loc.id, csrfToken);
            });

            marker.bindTooltip(escapeHtml(loc.name) + ', ' + escapeHtml(loc.state), {
                direction: 'top',
                offset: [0, -4]
            });
        });

        if (bounds.length) {
            map.fitBounds(bounds, { padding: [20, 20] });
        }

        /* If geolocation is already permitted, silently center the map near the user
           so nearby markers are immediately visible without needing to pan */
        if (navigator.permissions) {
            navigator.permissions.query({ name: 'geolocation' }).then(function (result) {
                if (result.state !== 'granted') return;
                navigator.geolocation.getCurrentPosition(function (pos) {
                    map.setView([pos.coords.latitude, pos.coords.longitude], 8);
                }, null, { timeout: 5000, maximumAge: 300000 });
            }).catch(function () {});
        }

        /* Clicking open ocean (not a marker) — find nearest and show a confirm step */
        map.on('click', function (e) {
            var nearest = findNearest(locations, e.latlng.lat, e.latlng.lng);
            if (nearest) showConfirm(nearest);
        });

        if (confirmEl) {
            var confirmBtn = confirmEl.querySelector('[data-map-confirm-btn]');
            if (confirmBtn) {
                confirmBtn.addEventListener('click', function () {
                    if (pendingLoc) submitLocation(pendingLoc.id, csrfToken);
                });
            }
        }
    }

    function init() {
        var mapEl = document.getElementById('map');
        if (!mapEl) return;

        var locations = parseLocations(mapEl);
        var confirmEl = document.getElementById('map-confirm');
        var csrfToken = mapEl.getAttribute('data-csrf') || '';

        ensureLeaflet()
            .then(function () {
                if (!window.L) {
                    showMapError(mapEl, confirmEl);
                    return;
                }
                buildMap(mapEl, locations, csrfToken, confirmEl);
            })
            .catch(function () {
                showMapError(mapEl, confirmEl);
            });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
