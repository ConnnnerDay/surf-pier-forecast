(function () {
    'use strict';

    var DEFAULT_LAT = 35.0;
    var DEFAULT_LNG = -77.0;
    var DEFAULT_ZOOM = 5;

    var TILE_PROVIDERS = [
        {
            url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            options: {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                maxZoom: 18
            }
        },
        {
            url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
            options: {
                attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
                subdomains: 'abcd',
                maxZoom: 20
            }
        },
        {
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
            options: {
                attribution: 'Tiles &copy; Esri',
                maxZoom: 19
            }
        }
    ];

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
                if (window.L) {
                    resolve();
                    return;
                }
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

    function showMapError(mapEl, hint) {
        mapEl.classList.add('map-fallback');
        mapEl.innerHTML = '' +
            '<p><strong>Map unavailable.</strong> We could not load map libraries from free providers right now.</p>' +
            '<p>Please use zip search below and try again in a moment.</p>';
        if (hint) {
            hint.textContent = 'Map failed to load from external providers. Use zip search for now.';
        }
    }

    function addBestAvailableTileLayer(map) {
        var idx = 0;

        function tryProvider() {
            if (idx >= TILE_PROVIDERS.length) return;
            var provider = TILE_PROVIDERS[idx++];
            var layer = L.tileLayer(provider.url, provider.options);
            var onError = function () {
                map.removeLayer(layer);
                tryProvider();
            };
            layer.once('tileerror', onError);
            layer.once('load', function () {
                layer.off('tileerror', onError);
            });
            layer.addTo(map);
        }

        tryProvider();
    }

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function buildLocationPopup(loc) {
        return '' +
            '<div class="setup-map-popup">' +
            '<p class="setup-map-popup__title">' + escapeHtml(loc.name) + ', ' + escapeHtml(loc.state) + '</p>' +
            '<button type="button" class="setup-map-popup__select" data-map-select-location="' + escapeHtml(loc.id) + '">Select this location</button>' +
            '</div>';
    }

    function buildMap(mapEl, locations, latInput, lonInput, submitBtn, hint) {
        var map = L.map(mapEl).setView([DEFAULT_LAT, DEFAULT_LNG], DEFAULT_ZOOM);
        addBestAvailableTileLayer(map);

        var selectedMarker = null;

        function setHiddenCoords(lat, lng) {
            latInput.value = lat.toFixed(6);
            lonInput.value = lng.toFixed(6);
            submitBtn.disabled = false;
        }

        function selectLocation(loc) {
            var lat = Number(loc.lat);
            var lng = Number(loc.lng);
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;

            if (selectedMarker) {
                selectedMarker.setLatLng([lat, lng]);
            } else {
                selectedMarker = L.marker([lat, lng]).addTo(map);
            }
            setHiddenCoords(lat, lng);
            if (hint) hint.textContent = 'Selected ' + loc.name + ', ' + loc.state + '. Press "Find Nearest Location" to continue.';
        }

        var bounds = [];
        var byId = {};
        locations.forEach(function (loc) {
            var lat = Number(loc.lat);
            var lng = Number(loc.lng);
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;

            byId[String(loc.id)] = loc;
            bounds.push([lat, lng]);
            var marker = L.circleMarker([lat, lng], {
                radius: 5,
                weight: 1,
                color: '#0e5f78',
                fillColor: '#1285a6',
                fillOpacity: 0.85
            }).addTo(map);
            marker.bindPopup(buildLocationPopup(loc));
            marker.on('click', function () { selectLocation(loc); });
        });

        map.on('popupopen', function (event) {
            var popupEl = event.popup && event.popup.getElement();
            if (!popupEl) return;
            var selectBtn = popupEl.querySelector('[data-map-select-location]');
            if (!selectBtn) return;
            selectBtn.addEventListener('click', function () {
                var selectedLoc = byId[selectBtn.getAttribute('data-map-select-location')];
                if (!selectedLoc) return;
                selectLocation(selectedLoc);
                map.closePopup(event.popup);
            }, { once: true });
        });

        if (bounds.length) {
            map.fitBounds(bounds, { padding: [20, 20] });
        }

        map.on('click', function (e) {
            var nearest = findNearest(locations, e.latlng.lat, e.latlng.lng);
            if (nearest) selectLocation(nearest);
        });

        if (navigator.geolocation && locations.length) {
            navigator.geolocation.getCurrentPosition(
                function (pos) {
                    var nearest = findNearest(locations, pos.coords.latitude, pos.coords.longitude);
                    if (!nearest) return;
                    map.setView([Number(nearest.lat), Number(nearest.lng)], 7);
                },
                function () { /* no-op */ }
            );
        }
    }

    function init() {
        var mapEl = document.getElementById('map');
        if (!mapEl) return;

        var locations = parseLocations(mapEl);
        var latInput = document.getElementById('location_lat');
        var lonInput = document.getElementById('location_lon');
        var submitBtn = document.getElementById('map-submit-btn');
        var hint = document.getElementById('map-hint');

        ensureLeaflet()
            .then(function () {
                if (!window.L) {
                    showMapError(mapEl, hint);
                    return;
                }
                buildMap(mapEl, locations, latInput, lonInput, submitBtn, hint);
            })
            .catch(function () {
                showMapError(mapEl, hint);
            });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
