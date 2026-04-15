/**
 * geo_layers.js — Satellite & Map Layers + Water Quality UI
 * ==========================================================
 * Handles the new "Satellite & Map Layers" and "Water Quality & Environmental
 * Data" sections added to the forecast dashboard (index.html).
 *
 * Responsibilities:
 *   1. Load Leaflet dynamically (same CDN strategy as fishing_map.js)
 *   2. Initialise a Leaflet map centred on the forecast location
 *   3. Wire base-layer radio buttons (OSM Standard, OSM Humanitarian, Esri)
 *   4. Wire overlay checkboxes (NASA GIBS SST, Chl-A, True Color, NE coastlines)
 *   5. Wire feature-layer checkboxes (Esri piers/beaches/parks, OAM imagery)
 *   6. Fetch and render the water-quality card via /api/v1/geo/environmental
 *   7. Fetch FAO zone + HDX datasets via /api/v1/geo/hdx-fao
 *   8. Provide graceful offline fallback: if the map fails to load, the
 *      static attribution text remains visible and no JS errors are thrown.
 *
 * The map is only initialised when the parent <details> element is opened
 * (Intersection Observer), so it does not slow down the initial page load.
 *
 * No API keys required — all tile services are public.
 * Works without third-party frameworks beyond Leaflet.
 */

(function () {
    'use strict';

    // ── Configuration ────────────────────────────────────────────────────────

    var LEAFLET_CSS_URL = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css';
    var LEAFLET_JS_URL  = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js';
    var LEAFLET_JS_FB   = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js';

    var DEFAULT_CENTER = [37.5, -96.0];
    var DEFAULT_ZOOM   = 6;

    // Tile layer definitions (URL templates for Leaflet)
    var TILE_LAYERS = {
        osm_standard: {
            url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            opts: {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" rel="noopener">OpenStreetMap</a> contributors',
                maxZoom: 19,
                subdomains: ['a','b','c']
            }
        },
        osm_humanitarian: {
            url: 'https://tile-{s}.openstreetmap.fr/hot/{z}/{x}/{y}.png',
            opts: {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" rel="noopener">OpenStreetMap</a> contributors, HOT',
                maxZoom: 19,
                subdomains: ['a','b','c']
            }
        },
        esri_world_imagery: {
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            opts: {
                attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS',
                maxZoom: 19
            }
        }
    };

    // NASA GIBS overlay URLs (yesterday's imagery — accounts for NRT latency)
    var gibs_date = _gibs_date();

    var OVERLAY_LAYERS = {
        sst: {
            url: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GHRSST_L4_MUR_Sea_Surface_Temperature/' +
                 gibs_date + '/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png',
            opts: {
                attribution: 'NASA GIBS — MUR SST',
                maxZoom: 7, opacity: 0.75, tileSize: 256
            },
            label: 'Sea Surface Temp'
        },
        chla: {
            url: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_Chlorophyll_A/' +
                 gibs_date + '/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png',
            opts: {
                attribution: 'NASA GIBS — MODIS Chl-A',
                maxZoom: 7, opacity: 0.7, tileSize: 256
            },
            label: 'Chlorophyll-A'
        },
        truecolor: {
            url: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_TrueColor_375m/' +
                 gibs_date + '/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg',
            opts: {
                attribution: 'NASA GIBS — VIIRS True Color',
                maxZoom: 9, opacity: 1.0, tileSize: 256
            },
            label: 'True Color (VIIRS)'
        }
    };

    // Natural Earth coastlines are served as GeoJSON from our own API
    var NE_COASTLINES_ENDPOINT = '/api/v1/geo/coastlines?res=110m';

    // ── State ────────────────────────────────────────────────────────────────
    var map           = null;
    var mapReady      = false;
    var activeBase    = null;   // current L.tileLayer for base map
    var activeBaseId  = 'osm_standard';
    var overlayLayers = {};     // layerId → L.tileLayer (null if not loaded)
    var neCoastlineLayer = null; // L.geoJSON for Natural Earth coastlines
    var featureLayers = {};     // layerId → L.layerGroup
    var initAttempted = false;

    // ── DOM references ───────────────────────────────────────────────────────
    var elMapContainer  = null;
    var elDateBar       = null;
    var elImageryDate   = null;
    var elActiveLabel   = null;
    var elWQSection     = null;

    // ── Helpers ──────────────────────────────────────────────────────────────

    function _gibs_date() {
        // Use yesterday to account for NASA NRT latency (~3-24 h)
        var d = new Date();
        d.setDate(d.getDate() - 1);
        return d.toISOString().slice(0, 10);
    }

    function _locationData() {
        var el = document.getElementById('geo-location-data');
        if (!el) return { lat: DEFAULT_CENTER[0], lng: DEFAULT_CENTER[1], location_name: '' };
        try { return JSON.parse(el.textContent || el.innerText); }
        catch (_) { return { lat: DEFAULT_CENTER[0], lng: DEFAULT_CENTER[1], location_name: '' }; }
    }

    function loadScript(url) {
        return new Promise(function (resolve, reject) {
            // If an identical <script> tag is already in the DOM, attach to it
            // instead of adding a duplicate — prevents double-loading Leaflet when
            // both geo_layers.js and fishing_map.js race to load the same CDN URL.
            var ex = document.querySelector('script[src="' + url + '"]');
            if (ex) {
                if (window.L) { resolve(); return; }
                ex.addEventListener('load',  resolve, { once: true });
                ex.addEventListener('error', reject,  { once: true });
                return;
            }
            var s = document.createElement('script');
            s.src = url;
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    function ensureLeafletCss() {
        if (document.querySelector('link[data-leaflet-geo]')) return;
        var l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = LEAFLET_CSS_URL;
        l.setAttribute('data-leaflet-geo', '1');
        document.head.appendChild(l);
    }

    function ensureLeaflet() {
        if (window.L) return Promise.resolve();
        ensureLeafletCss();
        return loadScript(LEAFLET_JS_URL)
            .catch(function () { return loadScript(LEAFLET_JS_FB); });
    }

    // ── Map initialisation ───────────────────────────────────────────────────

    function initMap() {
        if (mapReady || initAttempted) return;
        initAttempted = true;

        elMapContainer = document.getElementById('geo-map');
        elDateBar      = document.getElementById('geo-date-bar');
        elImageryDate  = document.getElementById('geo-imagery-date');
        elActiveLabel  = document.getElementById('geo-active-layer-label');

        if (!elMapContainer) return;

        var loc = _locationData();
        var center = (loc.lat && loc.lng) ? [loc.lat, loc.lng] : DEFAULT_CENTER;

        ensureLeaflet().then(function () {
            if (map) return; // guard against double-init

            map = L.map(elMapContainer, {
                zoomControl: true,
                preferCanvas: true,
                attributionControl: true
            }).setView(center, DEFAULT_ZOOM);

            // Default base layer
            activeBase = L.tileLayer(TILE_LAYERS.osm_standard.url, TILE_LAYERS.osm_standard.opts)
                          .addTo(map);
            activeBaseId = 'osm_standard';
            mapReady = true;

            // Wire controls after map is ready
            wireBaseControls();
            wireOverlayControls();
            wireFeatureControls();
            wireFeatureViewport();

            // Load Natural Earth coastlines (checked by default)
            var neCheck = document.getElementById('geo-overlay-coastlines');
            if (neCheck && neCheck.checked) {
                loadNeCoastlines();
            }

        }).catch(function (err) {
            console.warn('[geo_layers] Leaflet failed to load:', err);
            elMapContainer.textContent = 'Interactive map unavailable — check your connection.';
        });
    }

    // ── Base layer switcher ──────────────────────────────────────────────────

    function wireBaseControls() {
        var radios = document.querySelectorAll('input[name="geo-base"]');
        radios.forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (!mapReady || !map) return;
                var id = radio.value;
                var def = TILE_LAYERS[id];
                if (!def) return;
                if (activeBase) map.removeLayer(activeBase);
                activeBase = L.tileLayer(def.url, def.opts).addTo(map);
                activeBaseId = id;
            });
        });
    }

    // ── Overlay toggle controls ───────────────────────────────────────────────

    function wireOverlayControls() {
        var checkboxes = document.querySelectorAll('.geo-overlay-toggle');
        checkboxes.forEach(function (cb) {
            cb.addEventListener('change', function () {
                var layerId = cb.getAttribute('data-layer');
                if (cb.checked) {
                    enableOverlay(layerId);
                } else {
                    disableOverlay(layerId);
                }
            });
        });
    }

    function enableOverlay(layerId) {
        if (!mapReady || !map) return;

        if (layerId === 'ne_coastlines') {
            loadNeCoastlines();
            return;
        }

        var def = OVERLAY_LAYERS[layerId];
        if (!def) return;

        if (!overlayLayers[layerId]) {
            overlayLayers[layerId] = L.tileLayer(def.url, def.opts);
        }
        overlayLayers[layerId].addTo(map);

        // Show imagery date bar for NASA layers
        if (elDateBar && layerId !== 'ne_coastlines') {
            if (elImageryDate) elImageryDate.textContent = gibs_date;
            if (elActiveLabel) elActiveLabel.textContent = def.label;
            elDateBar.style.display = 'flex';
        }
    }

    function disableOverlay(layerId) {
        if (!mapReady || !map) return;

        if (layerId === 'ne_coastlines') {
            if (neCoastlineLayer) map.removeLayer(neCoastlineLayer);
            return;
        }

        var layer = overlayLayers[layerId];
        if (layer) map.removeLayer(layer);

        // Hide date bar if no NASA overlay is active
        var anyNasaActive = Object.keys(OVERLAY_LAYERS).some(function (k) {
            var l = overlayLayers[k];
            return l && map.hasLayer(l);
        });
        if (!anyNasaActive && elDateBar) elDateBar.style.display = 'none';
    }

    // ── Natural Earth GeoJSON coastlines ─────────────────────────────────────

    function loadNeCoastlines() {
        if (!mapReady || !map) return;
        if (neCoastlineLayer) {
            neCoastlineLayer.addTo(map);
            return;
        }

        // Fetch the full global 110m dataset (no bbox) — small enough (~300 KB)
        // that clipping by viewport would only hide coastlines after panning.
        fetch(NE_COASTLINES_ENDPOINT)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (geojson) {
                if (!geojson || !mapReady || !map) return;
                neCoastlineLayer = L.geoJSON(geojson, {
                    style: {
                        color: '#64b5f6',
                        weight: 1.2,
                        opacity: 0.7,
                        fill: false
                    }
                }).addTo(map);
            })
            .catch(function (err) {
                console.warn('[geo_layers] NE coastlines fetch failed:', err);
            });
    }

    // ── Feature layer controls (Esri piers, beaches, parks; OAM) ─────────────

    function wireFeatureControls() {
        var checkboxes = document.querySelectorAll('.geo-feat-toggle');
        checkboxes.forEach(function (cb) {
            cb.addEventListener('change', function () {
                var layerId = cb.getAttribute('data-layer');
                if (cb.checked) {
                    loadFeatureLayer(layerId);
                } else {
                    unloadFeatureLayer(layerId);
                }
            });
        });
    }

    function loadFeatureLayer(layerId) {
        if (!mapReady || !map) return;
        if (featureLayers[layerId]) {
            featureLayers[layerId].addTo(map);
            return;
        }

        var bounds = map.getBounds();
        var bboxParams = '?south=' + bounds.getSouth().toFixed(3) +
                         '&west='  + bounds.getWest().toFixed(3) +
                         '&north=' + bounds.getNorth().toFixed(3) +
                         '&east='  + bounds.getEast().toFixed(3);

        var endpoint = _featEndpoint(layerId, bboxParams);
        if (!endpoint) return;

        fetch(endpoint)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (resp) {
                if (!resp || !resp.ok || !mapReady || !map) return;
                var features = (resp.data || {}).features || (resp.data || {}).imagery || [];
                var group = L.layerGroup();
                features.forEach(function (f) {
                    if (f.lat == null || f.lng == null) return;
                    var marker = L.circleMarker([f.lat, f.lng], _featStyle(layerId))
                        .bindPopup(_featPopup(f, layerId));
                    group.addLayer(marker);
                });
                featureLayers[layerId] = group.addTo(map);
            })
            .catch(function (err) {
                console.warn('[geo_layers] feature layer', layerId, 'failed:', err);
            });
    }

    function unloadFeatureLayer(layerId) {
        var layer = featureLayers[layerId];
        if (layer && map) map.removeLayer(layer);
        featureLayers[layerId] = null;
    }

    var _featViewportTimer = null;

    function wireFeatureViewport() {
        if (!map) return;
        map.on('moveend zoomend', function () {
            clearTimeout(_featViewportTimer);
            _featViewportTimer = setTimeout(function () {
                Object.keys(featureLayers).forEach(function (layerId) {
                    var layer = featureLayers[layerId];
                    if (layer && map.hasLayer(layer)) {
                        map.removeLayer(layer);
                        featureLayers[layerId] = null;
                        loadFeatureLayer(layerId);
                    }
                });
            }, 500);
        });
    }

    function _featEndpoint(layerId, bboxParams) {
        var map_ep = {
            'esri_piers':   '/api/v1/geo/esri/piers',
            'esri_beaches': '/api/v1/geo/esri/beaches',
            'esri_parks':   '/api/v1/geo/esri/parks',
            'oam':          '/api/v1/geo/aerial/oam'
        };
        return (map_ep[layerId] || null) && (map_ep[layerId] + bboxParams);
    }

    function _featStyle(layerId) {
        var styles = {
            'esri_piers':   { radius: 6, fillColor: '#60a5fa', color: '#2563eb', weight: 1.5, fillOpacity: 0.8 },
            'esri_beaches': { radius: 6, fillColor: '#34d399', color: '#059669', weight: 1.5, fillOpacity: 0.8 },
            'esri_parks':   { radius: 7, fillColor: '#a3e635', color: '#65a30d', weight: 1.5, fillOpacity: 0.7 },
            'oam':          { radius: 8, fillColor: '#f59e0b', color: '#d97706', weight: 1.5, fillOpacity: 0.7 }
        };
        return styles[layerId] || { radius: 6, fillColor: '#94a3b8', color: '#64748b', weight: 1, fillOpacity: 0.8 };
    }

    function _featPopup(f, layerId) {
        var name = f.name || f.title || ('Feature ' + layerId);
        var type = f.type || '';
        var html = '<strong>' + _esc(name) + '</strong>';
        if (type) html += '<br><small>' + _esc(type) + '</small>';
        if (f.oam_url) {
            html += '<br><a href="' + _esc(f.oam_url) + '" target="_blank" rel="noopener">View on OAM &rarr;</a>';
        }
        return html;
    }

    function _esc(str) {
        return String(str || '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── Water Quality card population ────────────────────────────────────────

    function loadWaterQuality() {
        elWQSection = document.getElementById('wq-section');
        if (!elWQSection) return;

        var lat   = elWQSection.getAttribute('data-lat');
        var lng   = elWQSection.getAttribute('data-lng');
        var state = elWQSection.getAttribute('data-state');
        var species = elWQSection.getAttribute('data-species');

        if (!lat || !lng) {
            _wqUnavailable();
            return;
        }

        var wqUrl  = '/api/v1/geo/environmental?lat=' + lat + '&lng=' + lng +
                     (state ? '&state=' + encodeURIComponent(state) : '');
        var faoUrl = '/api/v1/geo/hdx-fao?lat=' + lat + '&lng=' + lng +
                     (species ? '&species=' + encodeURIComponent(species) : '');

        Promise.all([
            fetch(wqUrl).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
            fetch(faoUrl).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
        ]).then(function (results) {
            var wqResp  = results[0];
            var faoResp = results[1];
            _hideWQLoading();
            if (wqResp && wqResp.ok) {
                _renderWQMetrics(wqResp.data.water_quality);
            } else {
                _wqUnavailable();
            }
            if (faoResp && faoResp.ok) {
                _renderFAO(faoResp.data);
            }
        });
    }

    function _hideWQLoading() {
        var el = document.getElementById('wq-loading');
        if (el) el.hidden = true;
    }

    function _wqUnavailable() {
        _hideWQLoading();
        var el = document.getElementById('wq-unavailable');
        if (el) el.hidden = false;
    }

    function _renderWQMetrics(wq) {
        if (!wq || !wq.available) { _wqUnavailable(); return; }

        var grid = document.getElementById('wq-metrics-grid');
        if (!grid) return;

        var metrics = [
            { key: 'do_mg_l',          label: 'Dissolved O₂', unit: 'mg/L', icon: '💧', good_range: [6, 14],
              tip: 'Good fishing: >6 mg/L' },
            { key: 'temp_f',           label: 'Water Temp',    unit: '°F',   icon: '🌡️', good_range: null },
            { key: 'ph',               label: 'pH',            unit: '',     icon: '⚗️', good_range: [6.5, 8.5],
              tip: 'Healthy marine range: 7.5–8.5' },
            { key: 'salinity_ppt',     label: 'Salinity',      unit: 'ppt',  icon: '🧂', good_range: null },
            { key: 'turbidity_ntu',    label: 'Turbidity',     unit: 'NTU',  icon: '🌊', good_range: null,
              tip: 'Lower is clearer water' },
            { key: 'chlorophyll_a',    label: 'Chlorophyll-A', unit: 'µg/L', icon: '🌿', good_range: null,
              tip: 'High levels = productive bait zones' },
        ];

        var html = '';
        metrics.forEach(function (m) {
            var val = wq[m.key];
            if (val == null) return;
            var cls = 'wq-metric';
            html += '<div class="' + cls + '">' +
                    '<span class="wq-metric-icon" aria-hidden="true">' + m.icon + '</span>' +
                    '<span class="wq-metric-value">' + _esc(val) + '</span>' +
                    '<span class="wq-metric-unit">' + m.unit + '</span>' +
                    '<span class="wq-metric-label">' + m.label + '</span>' +
                    (m.tip ? '<span class="wq-metric-tip">' + _esc(m.tip) + '</span>' : '') +
                    '</div>';
        });

        if (html) {
            grid.innerHTML = html;
            grid.hidden = false;
        } else {
            _wqUnavailable();
            return;
        }

        // Enterococcus advisory
        var advisory = document.getElementById('wq-advisory');
        var advisoryText = document.getElementById('wq-advisory-text');
        if (advisory && advisoryText && wq.enterococcus_flag === 'advisory') {
            advisoryText.textContent =
                'Beach advisory: Enterococcus levels (' + wq.enterococcus_cfu_100ml +
                ' CFU/100mL) exceed EPA threshold (104). Check local advisories.';
            advisory.hidden = false;
        }

        // Station count note
        if (wq.station_count > 0) {
            var note = document.createElement('p');
            note.className = 'wq-station-note';
            note.textContent = 'Based on ' + wq.station_count + ' nearby monitoring station' +
                               (wq.station_count > 1 ? 's' : '') +
                               ' · Source: ' + (wq.source || 'EPA WQP');
            grid.parentNode.insertBefore(note, grid.nextSibling);
        }
    }

    function _renderFAO(data) {
        if (!data) return;

        // FAO zone
        var faoZone = data.fao_zone;
        if (faoZone && faoZone.area_code) {
            var elZone = document.getElementById('wq-fao-zone');
            var elName = document.getElementById('wq-fao-name');
            var elLink = document.getElementById('wq-fao-link');
            if (elZone && elName && elLink) {
                elName.textContent = faoZone.area_name + ' (Area ' + faoZone.area_code + ')';
                elLink.href = faoZone.fao_url || '#';
                elZone.hidden = false;
            }
        }

        // Species enrichment
        var species = data.species_enrichment || [];
        if (species.length) {
            var elEnrich = document.getElementById('wq-species-enrich');
            var elList   = document.getElementById('wq-species-list');
            if (elEnrich && elList) {
                var html = '';
                species.forEach(function (sp) {
                    html += '<li class="wq-species-item">' +
                            '<strong>' + _esc(sp.common_name) + '</strong>' +
                            (sp.scientific_name ? ' &mdash; <em>' + _esc(sp.scientific_name) + '</em>' : '') +
                            (sp.asfis_code ? ' <span class="wq-asfis-code">' + _esc(sp.asfis_code) + '</span>' : '') +
                            (sp.fao_url ? ' <a href="' + _esc(sp.fao_url) + '" target="_blank" rel="noopener">FAO &rarr;</a>' : '') +
                            '</li>';
                });
                elList.innerHTML = html;
                elEnrich.hidden = false;
            }
        }

        // HDX datasets
        var datasets = data.hdx_datasets || [];
        if (datasets.length) {
            var elHdx  = document.getElementById('wq-hdx-details');
            var elHdxL = document.getElementById('wq-hdx-list');
            if (elHdx && elHdxL) {
                var dhtml = '';
                datasets.forEach(function (ds) {
                    dhtml += '<li class="wq-hdx-item">' +
                             '<a href="' + _esc(ds.hdx_url) + '" target="_blank" rel="noopener">' +
                             _esc(ds.title) + '</a>' +
                             (ds.organization ? ' <span class="wq-hdx-org">' + _esc(ds.organization) + '</span>' : '') +
                             (ds.notes ? '<p class="wq-hdx-notes">' + _esc(ds.notes.slice(0, 120)) + '&hellip;</p>' : '') +
                             '</li>';
                });
                elHdxL.innerHTML = dhtml;
                elHdx.hidden = false;
            }
        }
    }

    // ── Observe collapsible open events to lazy-init ─────────────────────────

    function observeDetails() {
        // "Satellite & Map Layers" details element
        var detailsGeo = document.getElementById('satellite-layers-details');
        if (detailsGeo) {
            detailsGeo.addEventListener('toggle', function () {
                if (detailsGeo.open && !mapReady) initMap();
            });
            // Also trigger if it was opened before our script ran
            if (detailsGeo.open) initMap();
        }

        // "Water Quality" details element — load data on first open
        var detailsWQ = document.getElementById('water-quality-details');
        var wqLoaded = false;
        if (detailsWQ) {
            detailsWQ.addEventListener('toggle', function () {
                if (detailsWQ.open && !wqLoaded) {
                    wqLoaded = true;
                    loadWaterQuality();
                }
            });
            if (detailsWQ.open && !wqLoaded) {
                wqLoaded = true;
                loadWaterQuality();
            }
        }
    }

    // ── Entry point ──────────────────────────────────────────────────────────

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', observeDetails);
    } else {
        observeDetails();
    }

}());
