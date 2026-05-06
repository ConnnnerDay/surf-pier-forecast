(function () {
    'use strict';

    // ─── Config ───────────────────────────────────────────────────────────────
    var API_URL = '/api/fishing-map';

    var DEFAULT_CENTER = [37.5, -96.0];
    var DEFAULT_ZOOM   = 4;

    // ─── State ────────────────────────────────────────────────────────────────
    var map           = null;
    var mapReady      = false;
    var isFullscreen  = false;

    var fishingSpotLayer = null;     // L.layerGroup for structure markers
    var spotQueryTimer   = null;     // debounce timer for structure queries
    var spotCache        = {};       // bbox key → array of spot objects (all types)
    var _spotCacheKeys   = [];       // insertion-ordered keys for LRU eviction
    var _SPOT_CACHE_MAX  = 200;      // cap for localStorage budget (~10 MB typical limit)
    var _ssSaveTimer          = null; // debounce timer for localStorage writes
    var _lastRenderedSpotKey  = null; // cache key of the last renderFishingSpots() call
    var _elStructFiltersHint  = null; // cached DOM ref — fmap-struct-filters-hint
    var _elSpotTypesClear     = null; // cached DOM ref — fmap-spot-types-clear
    var _spotIconCache        = {};   // type → L.divIcon
    var _spotIconKeys        = [];   // insertion-ordered keys for LRU eviction
    var _SPOT_ICON_MAX       = 128;  // cap; evict oldest 32 when full
    // Adjacent-tile pre-fetch queue — background loads for N/S/E/W of the current view
    var _prefetchQueue      = [];    // {s,w,n,e,key} objects waiting for background fetch
    var _prefetchInFlight   = false; // whether a background fetch is running
    var _PREFETCH_DELAY     = 5000;  // ms between background fetches (Overpass rate-limit)
    var _PREFETCH_MAX_QUEUE = 8;     // max queued tiles (drops oldest if exceeded)
    // Shared cache for overlay icons (SST, METAR, buoy).
    // These layers clear and redraw on every fetch; caching the icon objects avoids
    // re-running L.divIcon() for every marker on every refresh.
    // Key format is layer-prefix + variant (e.g. 'sst|#22c55e|72').
    // Capped at 512 entries; when full the oldest 256 are evicted.
    var _overlayIconCache     = {};
    var _overlayIconKeys      = [];   // insertion-ordered keys for eviction
    var _elStructSpinner      = null; // cached DOM ref — fmap-struct-spinner
    var _elStructError        = null; // cached DOM ref — fmap-struct-error
    var _elStructErrorMsg     = null; // cached DOM ref — fmap-struct-error-msg
    var activeSpotTypes      = [];   // [] = all types; populated by type-filter pills
    var _spotTypeSaveTimer   = null; // debounce timer for persisting spotTypes
    var _structLoadingGens   = {};   // {reqGen: true} — active requests holding the spinner visible
    var _structReqGen        = 0;    // monotonic counter; stale completions are discarded
    var _structAbort         = null; // AbortController for the live structure fetch
    var _inflightStructKey   = null; // bbox key of the currently in-flight structures fetch
    var _communityAbort      = null; // AbortController for the in-flight /api/map/catches fetch
    var aiPickLayer      = null;     // L.layerGroup for AI habitat picks
    var aiQueryTimer     = null;     // debounce timer for AI habitat queries
    var aiCache          = {};       // bbox-key → array of habitat features
    var _aiCacheKeys     = [];       // insertion-ordered keys for LRU eviction
    var _AI_CACHE_MAX    = 64;       // cap so heavy sessions don't leak memory
    var _aiReqGen        = 0;        // monotonic counter; stale AI completions are discarded
    var _aiAbort         = null;     // AbortController for the live AI habitat fetch
    var _AI_LS_KEY       = 'fmap_ai_cache_v1';  // localStorage key for AI picks
    var _AI_LS_TTL       = 21600000;             // 6 hours in ms
    var _AI_POLY_CAP     = 150;      // max polygon/polyline overlays rendered by AI layer
    var _AI_POINT_CAP    = 60;       // max point-marker features rendered by AI layer

    // ─── Community / social state ─────────────────────────────────────────────
    var communityLayerOn  = false;   // whether community pins are visible
    var communityLayer    = null;    // L.layerGroup for community catch pins
    var communityData     = [];      // [{id,lat,lng,species,…}]
    var communityTimer    = null;    // debounce for community fetch on move
    var catchLogMode      = false;   // user is placing a catch pin
    var pendingCatchLatLng = null;   // {lat,lng} for the log modal
    var pendingCatchMarker = null;   // temporary L.marker shown before submit
    var IS_LOGGED_IN      = !!(window.IS_LOGGED_IN || false);

    // ─── Fishing-relevant Live Feeds state ───────────────────────────────────
    // Active overlays: marine warnings, storm tracker, recent storms, SST,
    // buoys, HF Radar, METAR, tropical outlook.
    var marineWarnOn      = false;   // marine warnings overlay active
    var marineWarnLayer   = null;    // L.layerGroup for warning polygons
    var marineWarnTimer   = null;    // debounce for viewport-based reload
    var stormTrackerOn    = false;   // storm tracker overlay active
    var stormTrackerLayer = null;    // L.layerGroup for storm graphics
    var recentStormsOn    = false;   // recent hurricane tracks overlay active
    var recentStormsLayer = null;    // L.layerGroup for seasonal storm tracks
    var sstLayerOn        = false;   // SST station overlay active
    var sstLayer          = null;    // L.layerGroup for SST markers
    var sstQueryTimer     = null;    // debounce for viewport reload
    var metarOn           = false;   // METAR surface obs overlay active
    var metarLayer        = null;    // L.layerGroup for METAR stations
    var metarTimer        = null;    // debounce for viewport reload
    var buoyOn            = false;   // NDBC buoy overlay active
    var buoyLayer         = null;    // L.layerGroup for buoy markers
    var buoyTimer         = null;    // debounce for viewport reload

    // ─── Basemap state ────────────────────────────────────────────────────────
    // Three-way toggle: satellite → nautical (OpenSeaMap) → street
    // Promoted to module scope so tileerror fallback can update the button.
    var _basemapMode    = 'satellite'; // 'satellite' | 'nautical' | 'street'
    var _isSatellite    = true;        // kept for backward compat checks
    var _nauticalLayer  = null;        // OpenSeaMap overlay (shown on nautical mode)

    // ─── Per-layer AbortControllers ───────────────────────────────────────────
    var sstAbort        = null;
    var metarAbort      = null;
    var marineWarnAbort = null;
    var buoyAbort       = null;
    var _catchDetailAbort = null;

    // ─── Tide chart / time-slider state ──────────────────────────────────────
    var _tideSliderHour    = new Date().getHours(); // 0-23 selected hour
    var _scoreData         = null;   // cached /api/v1/map/score response
    var _scoreAbort        = null;   // AbortController for score fetch
    var _tideChartTimer    = null;   // debounce for re-fetch on location change

    // ─── Spot detail panel state ──────────────────────────────────────────────
    var _activeSpotData    = null;   // currently shown spot object
    var _activeSpotMarker  = null;   // Leaflet marker for the currently open spot
    var _favoriteSpotKeys  = {};     // persisted in localStorage
    var _LABEL_TO_GRADE    = {Excellent: 'excellent', Good: 'good', Fair: 'fair', Slow: 'slow'};
    var _favSpotsLayer     = null;   // Leaflet layer for user's saved favorite pins

    function _fmtHour(h) {
        return (h % 12 || 12) + (h < 12 ? 'AM' : 'PM');
    }

    // Shared Tab-key focus trap used by all three dialogs.
    // Call from a keydown handler after confirming the dialog is open.
    var _FOCUSABLE_SEL = 'a[href],button:not([disabled]),input:not([disabled]),' +
                         'select:not([disabled]),textarea:not([disabled]),' +
                         '[tabindex]:not([tabindex="-1"])';
    function _trapFocusOnTab(container, e) {
        if (e.key !== 'Tab') return;
        var focusable = Array.prototype.slice.call(container.querySelectorAll(_FOCUSABLE_SEL));
        if (!focusable.length) return;
        var first = focusable[0], last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault(); last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault(); first.focus();
        }
    }

    // ─── Category filter tabs ────────────────────────────────────────────────
    // The flat pill list is replaced by 4 high-level category tabs.
    // Each tab shows/hides the spot types belonging to that category.
    var _CATEGORY_TYPES = {
        structures: ['pier', 'jetty', 'bridge', 'seawall', 'point', 'reef',
                     'wreck', 'shoal', 'buoy'],
        habitats:   ['grass_flat', 'tidal_flat', 'saltmarsh', 'mangrove',
                     'kelp', 'oyster_reef', 'inlet', 'beach'],
        amenities:  ['marina', 'boat_ramp', 'fishing_shop', 'dive_site'],
        my_spots:   ['fishing']    // custom / user-logged spots
    };
    var _activeCategory = null; // null = all categories visible

    // ─── DOM refs ─────────────────────────────────────────────────────────────
    var els = {};

    // ─── Utilities ───────────────────────────────────────────────────────────
    function esc(s) {
        return String(s || '')
            .replace(/&/g,'&amp;').replace(/</g,'&lt;')
            .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
        // Guard: skip if Leaflet CSS is already present (idempotent).
        if (document.querySelector('link[rel="stylesheet"][href*="leaflet"]')) return;
        var l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css';
        l.setAttribute('data-leaflet-css', '1');
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
    var TILE_SATELLITE = {
        url:  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        opts: { attribution: 'Tiles &copy; Esri &mdash; Source: Esri, USGS, NOAA', maxZoom: 19 }
    };
    var TILE_NAUTICAL = {
        url:  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        opts: { attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>', subdomains: 'abc', maxZoom: 19 }
    };
    // OpenSeaMap nautical overlay added on top of OSM street tiles
    var TILE_OPENSEAMAP = {
        url:  'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png',
        opts: { attribution: '&copy; <a href="https://www.openseamap.org">OpenSeaMap</a>', maxZoom: 18, opacity: 0.85 }
    };
    var TILE_STREET = {
        url:  'https://{s}.basemaps.cartocdn.com/dark_matter_no_labels/{z}/{x}/{y}{r}.png',
        opts: { attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap', subdomains: 'abcd', maxZoom: 19 }
    };
    var activeTileLayer = null;

    function initMap() {
        if (mapReady) return;
        mapReady = true;

        // If the server provided the saved location's coordinates, use them as the
        // starting view and seed savedLocationLatLng immediately — no need to wait
        // for the NOAA API to confirm the ID match before showing overlays.
        var serverLat = (typeof CURRENT_LOC_LAT !== 'undefined') ? CURRENT_LOC_LAT : 0;
        var serverLng = (typeof CURRENT_LOC_LNG !== 'undefined') ? CURRENT_LOC_LNG : 0;
        var startCenter = (serverLat && serverLng) ? [serverLat, serverLng] : DEFAULT_CENTER;
        var startZoom   = (serverLat && serverLng) ? 12 : DEFAULT_ZOOM;

        if (serverLat && serverLng) {
            savedLocationLatLng = { lat: serverLat, lng: serverLng };
            hasAutoZoomed = true;
            // Pre-warm the structure cache for the full home corridor so nearby
            // icons appear immediately and pan/zoom serve from cache.
            // 500 ms lets the tile request and first moveend query fire first,
            // while still dispatching while Overpass is busy on the initial fetch.
            setTimeout(prefetchHomeCorridorStructures, 500);
        }

        // preferCanvas: use the <canvas> renderer for vector layers by default.
        // This is ~3-5x faster than SVG for dense overlays (METAR, AQI, buoys,
        // gauges, HF Radar) because the GPU composites a single bitmap instead of
        // layout/paint for thousands of individual SVG DOM nodes.
        map = L.map(els.mapEl, { zoomControl: true, preferCanvas: true }).setView(startCenter, startZoom);

        // Default: satellite so users can visually see coastline, piers, structure
        activeTileLayer = L.tileLayer(TILE_SATELLITE.url, TILE_SATELLITE.opts);
        activeTileLayer.once('tileerror', function () {
            // Fall back to street tiles if ESRI is unavailable.
            // Also update module-level _isSatellite so the basemap button stays in sync.
            map.removeLayer(activeTileLayer);
            activeTileLayer = L.tileLayer(TILE_STREET.url, TILE_STREET.opts).addTo(map);
            _isSatellite = false;
            _syncBasemapBtn();
        });
        activeTileLayer.addTo(map);

        // Layer groups — render order: AI picks → OSM structures
        aiPickLayer      = L.layerGroup().addTo(map);
        _aiLsLoad();
        fishingSpotLayer = L.layerGroup().addTo(map);

        // Wire zoom/pan → refresh all layers (single handler — duplicate bindings
        // caused every event to fire twice, queuing double the debounced requests).
        map.on('moveend zoomend', function () {
            updateZoomHint();
            scheduleFishingSpotQuery();
            scheduleAIQuery();
        });

        setTimeout(function () { if (map) map.invalidateSize(); }, 350);

        // Update the zoom hint immediately so it reflects the starting zoom level
        // (hidden at zoom ≥ 8, i.e. when server coords are used)
        updateZoomHint();

        // Initialise the map legend control (shown when colour-coded layers are active)
        _initLegend();
    }

    // ─── Map overlay controls ─────────────────────────────────────────────────

    // Sync the basemap toggle button to _basemapMode.
    // Three-way cycle: satellite → nautical → street → satellite
    function _syncBasemapBtn() {
        var btn      = document.getElementById('fmap-basemap-btn');
        var iconSat  = document.getElementById('fmap-basemap-icon-sat');
        var iconNaut = document.getElementById('fmap-basemap-icon-naut');
        var iconMap  = document.getElementById('fmap-basemap-icon-map');
        if (!btn) return;
        _isSatellite = (_basemapMode === 'satellite');
        btn.classList.toggle('fmap-ctrl-btn--active', _basemapMode !== 'street');
        var labels = {
            satellite: { title: 'Satellite · click for Nautical', aria: 'Basemap: Satellite. Click for Nautical' },
            nautical:  { title: 'Nautical · click for Street',    aria: 'Basemap: Nautical. Click for Street' },
            street:    { title: 'Street map · click for Satellite', aria: 'Basemap: Street. Click for Satellite' }
        };
        var l = labels[_basemapMode] || labels.satellite;
        btn.title = l.title;
        btn.setAttribute('aria-label', l.aria);
        if (iconSat)  iconSat.hidden  = (_basemapMode !== 'satellite');
        if (iconNaut) iconNaut.hidden = (_basemapMode !== 'nautical');
        if (iconMap)  iconMap.hidden  = (_basemapMode !== 'street');
    }

    function wireMapControls() {
        // Near Me — snap to saved forecast location; GPS as fallback
        var nearMeBtn = document.getElementById('fmap-near-me');
        if (nearMeBtn) {
            nearMeBtn.addEventListener('click', function () {
                if (!map) return;
                // First choice: user's saved location from the current forecast
                if (savedLocationLatLng) {
                    map.flyTo([savedLocationLatLng.lat, savedLocationLatLng.lng], 12, { duration: 0.9 });
                    return;
                }
                // Fallback: device GPS
                if (!navigator.geolocation) {
                    showToast('No saved location — set one via the location bar.');
                    return;
                }
                nearMeBtn.classList.add('fmap-ctrl-btn--loading');
                navigator.geolocation.getCurrentPosition(
                    function (pos) {
                        nearMeBtn.classList.remove('fmap-ctrl-btn--loading');
                        if (!map) return;
                        map.flyTo([pos.coords.latitude, pos.coords.longitude], 12, { duration: 0.9 });
                    },
                    function () {
                        nearMeBtn.classList.remove('fmap-ctrl-btn--loading');
                        showToast('Could not get your location.');
                    },
                    { timeout: 8000, maximumAge: 120000 }
                );
            });
        }

        // Reset view — return to full US overview
        var resetBtn = document.getElementById('fmap-reset-view');
        if (resetBtn) {
            resetBtn.addEventListener('click', function () {
                if (!map) return;
                map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { duration: 0.8 });
            });
        }

        // Basemap toggle — three-way cycle: satellite → nautical → street
        var basemapBtn = document.getElementById('fmap-basemap-btn');
        if (basemapBtn) {
            basemapBtn.addEventListener('click', function () {
                if (!map) return;
                var modes = ['satellite', 'nautical', 'street'];
                var idx = modes.indexOf(_basemapMode);
                _basemapMode = modes[(idx + 1) % modes.length];

                var cfg = _basemapMode === 'satellite' ? TILE_SATELLITE :
                          _basemapMode === 'nautical'  ? TILE_NAUTICAL  : TILE_STREET;

                // Add new base layer before removing old to avoid blank-tile flash
                var newLayer = L.tileLayer(cfg.url, cfg.opts).addTo(map);
                map.removeLayer(activeTileLayer);
                activeTileLayer = newLayer;

                // OpenSeaMap seamark overlay — only shown in nautical mode
                if (_basemapMode === 'nautical') {
                    if (!_nauticalLayer) {
                        _nauticalLayer = L.tileLayer(TILE_OPENSEAMAP.url, TILE_OPENSEAMAP.opts);
                    }
                    if (!map.hasLayer(_nauticalLayer)) _nauticalLayer.addTo(map);
                } else {
                    if (_nauticalLayer && map.hasLayer(_nauticalLayer)) {
                        map.removeLayer(_nauticalLayer);
                    }
                }
                _syncBasemapBtn();
            });
        }
    }

    // ─── Toast ────────────────────────────────────────────────────────────────
    function showToast(msg) {
        var t = document.createElement('div');
        t.className = 'fmap-toast';
        t.setAttribute('role', 'status');
        t.setAttribute('aria-live', 'polite');
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

    // ─── Zoom hint ────────────────────────────────────────────────────────────
    function updateZoomHint() {
        var hint = document.getElementById('fmap-zoom-hint');
        if (!hint || !map) return;
        hint.classList.toggle('fmap-zoom-hint--hidden', map.getZoom() >= 8);
    }


    // ─── AI Habitat Spot Finder ───────────────────────────────────────────────
    //
    // Per-osmType colors that match the structure/habitat layer palette so
    // AI picks blend visually with the rest of the map legend.
    var AI_PICK_COLORS = {
        reef:      '#f59e0b',
        saltmarsh: '#34d399',
        seagrass:  '#22c55e',
        mangrove:  '#16a34a',
        channel:   '#38bdf8',
        shoal:     '#94a3b8',
        tidalflat: '#6ee7b7',
        beach:     '#fbbf24',
        wreck:     '#d97706',
        bay:       '#60a5fa',
        general:   '#a78bfa'
    };

    // Label + fishing tip shown in each AI pick tooltip.
    var AI_PICK_INFO = {
        reef:      { label: 'Reef',        tip: 'Reef edge — grouper, snapper, and bass stack on the upcurrent face. Work the drop with a jig or live bait.' },
        saltmarsh: { label: 'Saltmarsh',   tip: 'Marsh creek mouth — redfish and snook ambush bait washing out on the falling tide. Position at the exit.' },
        seagrass:  { label: 'Seagrass',    tip: 'Seagrass flat — trout, redfish, and flounder push shallow on the flood. Work the edges and any potholes.' },
        mangrove:  { label: 'Mangrove',    tip: 'Mangrove roots — snook, tarpon, and jack hold in the shadow line. Cast tight to the prop roots.' },
        channel:   { label: 'Channel',     tip: 'Tidal channel — bait funnels through on every tide change. Fish the current seam at the channel edge.' },
        shoal:     { label: 'Shoal',       tip: 'Shoal drop-off — fish hold on the seam between shallow and deep waiting for bait swept off the flat.' },
        tidalflat: { label: 'Tidal Flat',  tip: 'Tidal flat — fish push onto the flat as the tide floods and stack on the edges at low water.' },
        beach:     { label: 'Beach',       tip: 'Beach trough — look for rip cuts and gutters behind sandbars where drum and stripers feed.' },
        wreck:     { label: 'Wreck',       tip: 'Submerged wreck — acts as an artificial reef. Cast up-current and let bait drift into the structure shadow.' },
        bay:       { label: 'Bay',         tip: 'Bay or cove — sheltered water concentrates bait. Work points, channel edges, and any drop-off.' },
        general:   { label: 'Habitat',     tip: 'Fish concentrate where structure meets current — reef edges, channel bends, shoal drop-offs, marsh creek mouths.' }
    };

    // Map filter pill data-type → API habitat_type parameter.
    // Only habitat-relevant pill types are listed; structure-only types (pier,
    // buoy, marina, ramp…) have no AI habitat equivalent and are omitted.
    var _PILL_TO_HABITAT_TYPE = {
        'reef':       'reef',
        'oyster_reef':'estuary',
        'wreck':      'reef',
        'saltmarsh':  'estuary',
        'seagrass':   'grassflat',
        'grass_flat': 'grassflat',
        'mangrove':   'mangrove',
        'channel':    'estuary',
        'inlet':      'estuary',
        'shoal':      'bottom',
        'tidal_flat': 'bottom',
        'tidalflat':  'bottom',
        'beach':      'surf',
        'kelp':       'kelp',
        'bay':        'general',
    };

    // Map filter pill data-type → AI osmType used in renderAIHabitatSpots.
    var _PILL_TO_AI_OSMT = {
        'reef':       'reef',
        'oyster_reef':'reef',
        'wreck':      'wreck',
        'saltmarsh':  'saltmarsh',
        'seagrass':   'seagrass',
        'grass_flat': 'seagrass',
        'mangrove':   'mangrove',
        'channel':    'channel',
        'inlet':      'channel',
        'shoal':      'shoal',
        'tidal_flat': 'tidalflat',
        'tidalflat':  'tidalflat',
        'beach':      'beach',
        'kelp':       'seagrass',
        'bay':        'bay',
    };

    // Symbols for AI pick osmTypes that don't have SPOT_LABELS entries
    var _AI_OSMT_SYM = {
        seagrass:  '≋',  // matches grass_flat
        channel:   '⇢',  // matches inlet
        tidalflat: '⊟',  // matches tidal_flat
        bay:       '〜',
        general:   '✦',
    };

    function makeAIPickIcon(osmType) {
        var cacheKey = 'ai|' + osmType;
        if (_spotIconCache[cacheKey]) return _spotIconCache[cacheKey];
        var color = AI_PICK_COLORS[osmType] || AI_PICK_COLORS.general;
        var sym   = (SPOT_LABELS && SPOT_LABELS[osmType]) || _AI_OSMT_SYM[osmType] || '✦';
        var html  =
            '<span class="fmap-ai-dot" style="--ai-c:' + color + ';' +
            'display:flex;align-items:center;justify-content:center;' +
            'font-family:system-ui,\'Segoe UI Symbol\',\'Apple Symbols\',sans-serif;' +
            'font-size:10px;line-height:1">' + sym + '</span>';
        var icon = L.divIcon({ className: 'fmap-ai-wrap', html: html, iconSize: [18, 18], iconAnchor: [9, 9] });
        _spotIconCache[cacheKey] = icon;
        return icon;
    }

    // Render AI habitat picks, filtering to osmTypes that match active filter pills.
    // When no pills are active, renders everything from the general query.
    function renderAIHabitatSpots(features) {
        if (!aiPickLayer) return;
        aiPickLayer.clearLayers();

        // Build the set of AI osmTypes wanted by the active filters.
        var wantedOsmTypes = null;  // null = show all
        if (activeSpotTypes.length) {
            wantedOsmTypes = {};
            var hasHabitatPill = false;
            activeSpotTypes.forEach(function (t) {
                var ot = _PILL_TO_AI_OSMT[t];
                if (ot) { wantedOsmTypes[ot] = true; hasHabitatPill = true; }
            });
            // Only structure pills active (pier/buoy/etc) — AI has nothing to show.
            if (!hasHabitatPill) { return; }
        }

        // Filter to wanted osmTypes; partition into polygon ways and point nodes.
        var _PROX   = 0.002;
        var polys   = [];
        var rawPts  = [];
        features.forEach(function (f) {
            if (!f.lat || !f.lng) return;
            if (wantedOsmTypes && !wantedOsmTypes[f.osmType || 'general']) return;
            if (f.geometry && f.geometry.length >= 3) {
                polys.push(f);
            } else {
                rawPts.push(f);
            }
        });

        // Point-vs-point proximity dedup (adjacent polygon patches are distinct so
        // they skip this; each is already a separate OSM way).
        var dedupedPts = [];
        rawPts.forEach(function (f) {
            for (var i = 0; i < dedupedPts.length; i++) {
                if (Math.abs(dedupedPts[i].lat - f.lat) < _PROX &&
                    Math.abs(dedupedPts[i].lng - f.lng) < _PROX) return;
            }
            dedupedPts.push(f);
        });

        // Render polygon/polyline overlays first, up to _AI_POLY_CAP.
        // Pre-compute viewport span for the large-feature fill check below.
        var _vBounds     = map ? map.getBounds() : null;
        var _viewLatSpan = _vBounds ? (_vBounds.getNorth() - _vBounds.getSouth()) : 1;
        var _viewLngSpan = _vBounds ? (_vBounds.getEast()  - _vBounds.getWest())  : 1;

        var renderPolys = polys.slice(0, _AI_POLY_CAP);
        renderPolys.forEach(function (f) {
            var osmType = f.osmType || 'general';
            var info    = AI_PICK_INFO[osmType] || AI_PICK_INFO.general;
            var name    = f.name ? '<strong>' + esc(f.name) + '</strong><br>' : '';
            var color   = AI_PICK_COLORS[osmType] || AI_PICK_COLORS.general;
            var geom    = f.geometry;
            var first   = geom[0];
            var last    = geom[geom.length - 1];
            var closed  = Math.abs(first[0] - last[0]) < 0.00002 &&
                          Math.abs(first[1] - last[1]) < 0.00002;

            // Compute the polygon's lat/lng extent to decide fill opacity.
            // Very large features (bay, coastline segment) that span ≥60% of the
            // viewport in either axis get outline-only treatment so they don't
            // wash out the underlying tiles.
            var minLat = Infinity, maxLat = -Infinity;
            var minLng = Infinity, maxLng = -Infinity;
            geom.forEach(function (c) {
                if (c[0] < minLat) minLat = c[0]; if (c[0] > maxLat) maxLat = c[0];
                if (c[1] < minLng) minLng = c[1]; if (c[1] > maxLng) maxLng = c[1];
            });
            var isHuge = (maxLat - minLat) > _viewLatSpan * 0.6 ||
                         (maxLng - minLng) > _viewLngSpan * 0.6;

            var poly    = (closed && !isHuge)
                ? L.polygon(geom, {
                    color: color, weight: 2, opacity: 0.85,
                    fillColor: color, fillOpacity: 0.25,
                    className: 'fmap-habitat-poly'
                  })
                : L.polyline(geom, {
                    color: color, weight: 3, opacity: 0.75,
                    className: 'fmap-habitat-poly'
                  });
            poly.bindTooltip(
                '<span class="fmap-ai-tip-label">' + esc(info.label) + '</span>' + name +
                '<span class="fmap-tooltip-sub">' + esc(info.tip) + '</span>',
                { className: 'fmap-tooltip fmap-ai-tooltip', sticky: true }
            );
            aiPickLayer.addLayer(poly);
        });

        // Render point markers, up to _AI_POINT_CAP, skipping any point whose
        // lat/lng sits within _PROX of a polygon centroid already rendered
        // (avoids a dot marker stacked on top of its own polygon outline).
        var pointCount = 0;
        dedupedPts.forEach(function (f) {
            if (pointCount >= _AI_POINT_CAP) return;
            for (var j = 0; j < renderPolys.length; j++) {
                if (Math.abs(renderPolys[j].lat - f.lat) < _PROX &&
                    Math.abs(renderPolys[j].lng - f.lng) < _PROX) return;
            }
            var osmType = f.osmType || 'general';
            var info    = AI_PICK_INFO[osmType] || AI_PICK_INFO.general;
            var name    = f.name ? '<strong>' + esc(f.name) + '</strong><br>' : '';
            var m = L.marker([f.lat, f.lng], { icon: makeAIPickIcon(osmType) });
            m.bindTooltip(
                '<span class="fmap-ai-tip-label">' + esc(info.label) + '</span>' + name +
                '<span class="fmap-tooltip-sub">' + esc(info.tip) + '</span>',
                { className: 'fmap-tooltip fmap-ai-tooltip', direction: 'top', offset: [0, -7] }
            );
            aiPickLayer.addLayer(m);
            pointCount++;
        });
    }

    // Which API habitat_type strings to fetch, based on active filter pills.
    // No pills → query 'general' (all common habitat types in one OSM query).
    // Habitat pills → query the matching type(s).
    // Only structure pills → null (nothing for AI to show).
    function _activeHabitatTypes() {
        if (!activeSpotTypes.length) return ['general'];
        var seen = {};
        var types = [];
        activeSpotTypes.forEach(function (t) {
            var ht = _PILL_TO_HABITAT_TYPE[t];
            if (ht && !seen[ht]) { seen[ht] = true; types.push(ht); }
        });
        return types.length ? types : null;
    }

    function queryAIHabitatSpots() {
        if (!map || !aiPickLayer) return;

        if (map.getZoom() < 10) {
            aiPickLayer.clearLayers();
            return;
        }

        var habitatTypes = _activeHabitatTypes();
        if (!habitatTypes) {
            // Only structure-type pills active — nothing for AI to show
            aiPickLayer.clearLayers();
            return;
        }

        var b   = map.getBounds();
        var s   = Math.floor(b.getSouth() * 4) / 4;
        var w   = Math.floor(b.getWest()  * 4) / 4;
        var n   = Math.ceil(b.getNorth()  * 4) / 4;
        var e   = Math.ceil(b.getEast()   * 4) / 4;
        // Cache key includes the habitat types so changing filters busts the cache.
        var key = s + ',' + w + ',' + n + ',' + e + ':' + habitatTypes.slice().sort().join(',');

        if (aiCache[key]) { renderAIHabitatSpots(aiCache[key]); return; }

        // Abort any in-flight AI habitat fetch before starting the new one.
        if (_aiAbort) _aiAbort.abort();
        _aiAbort = new AbortController();
        var thisAiGen = ++_aiReqGen;
        var thisKey   = key;

        // Single consolidated request: /api/v1/map/habitats handles parallel
        // per-type fetching and deduplication server-side.
        var bboxParams = 'south=' + s + '&west=' + w + '&north=' + n + '&east=' + e;
        var url = '/api/v1/map/habitats?' + bboxParams;
        // If a subset of types is active, tell the server — saves work on both ends.
        if (habitatTypes && habitatTypes.length < 10) {
            url += '&types=' + habitatTypes.join(',');
        }

        fetch(url, { signal: _aiAbort.signal })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (resp) {
            if (thisAiGen !== _aiReqGen) return;
            if (!map || map.getZoom() < 10) { aiPickLayer.clearLayers(); return; }
            var raw = (resp && resp.ok && resp.data && resp.data.features) || [];
            var features = raw.map(function (f) {
                return {
                    lat:      f.lat,
                    lng:      f.lng,
                    name:     f.name || '',
                    osmType:  f.osm_type || f.osmType || 'general',
                    score:    f.score || 0,
                    geometry: f.geometry || null,
                };
            });
            features.sort(function (a, b) { return (b.score || 0) - (a.score || 0); });
            _aiCachePut(thisKey, features);
            renderAIHabitatSpots(features);
        })
        .catch(function (err) {
            if (err.name !== 'AbortError') {
                console.warn('[fishing-map] AI habitat query failed:', err);
            }
        });
    }

    function scheduleAIQuery() {
        clearTimeout(aiQueryTimer);
        aiQueryTimer = setTimeout(queryAIHabitatSpots, 600);
    }

    // LRU-bounded write for aiCache — mirrors _spotCachePut().
    function _aiCachePut(key, data) {
        if (!Object.prototype.hasOwnProperty.call(aiCache, key)) {
            if (_aiCacheKeys.length >= _AI_CACHE_MAX) {
                delete aiCache[_aiCacheKeys.shift()];
            }
            _aiCacheKeys.push(key);
        }
        aiCache[key] = data;
        // Persist to localStorage (best-effort, skip if quota exceeded)
        try {
            var raw = localStorage.getItem(_AI_LS_KEY);
            var store = (raw ? JSON.parse(raw) : null) || {};
            store[key] = { ts: Date.now(), d: data };
            // Evict oldest entries to stay under quota
            var keys = Object.keys(store);
            if (keys.length > _AI_CACHE_MAX) {
                keys.sort(function (a, b) { return (store[a].ts || 0) - (store[b].ts || 0); });
                keys.slice(0, keys.length - _AI_CACHE_MAX).forEach(function (k) { delete store[k]; });
            }
            localStorage.setItem(_AI_LS_KEY, JSON.stringify(store));
        } catch (_e) {}
    }

    function _aiLsLoad() {
        try {
            var raw = localStorage.getItem(_AI_LS_KEY);
            if (!raw) return;
            var saved = JSON.parse(raw);
            if (!saved || typeof saved !== 'object') return;
            var now = Date.now();
            Object.keys(saved).forEach(function (k) {
                var entry = saved[k];
                if (entry && entry.ts && now - entry.ts < _AI_LS_TTL && Array.isArray(entry.d)) {
                    // Populate in-memory cache directly — skip localStorage write to avoid loop
                    if (!Object.prototype.hasOwnProperty.call(aiCache, k)) {
                        if (_aiCacheKeys.length >= _AI_CACHE_MAX) {
                            delete aiCache[_aiCacheKeys.shift()];
                        }
                        _aiCacheKeys.push(k);
                    }
                    aiCache[k] = entry.d;
                }
            });
        } catch (_e) {}
    }

    // Overlay icon cache — returns a cached L.divIcon, creating it on first use.
    // Evicts oldest 256 entries when the 512-entry cap is reached so long-running
    // sessions don't accumulate unbounded icon objects.
    function _cachedDivIcon(key, opts) {
        if (Object.prototype.hasOwnProperty.call(_overlayIconCache, key)) {
            return _overlayIconCache[key];
        }
        if (_overlayIconKeys.length >= 512) {
            var evict = _overlayIconKeys.splice(0, 256);
            for (var i = 0; i < evict.length; i++) delete _overlayIconCache[evict[i]];
        }
        var icon = L.divIcon(opts);
        _overlayIconCache[key] = icon;
        _overlayIconKeys.push(key);
        return icon;
    }

    // ─── OSM Fishing Spots (Overpass API) ─────────────────────────────────────
    var OVERPASS_URLS = [
        'https://overpass-api.de/api/interpreter',
        'https://overpass.kumi.systems/api/interpreter'
    ];
    var SPOT_TYPES = {
        pier:         { label: 'Pier',              color: '#a78bfa', habitat: false, minZoom: 8  },
        jetty:        { label: 'Jetty',             color: '#818cf8', habitat: false, minZoom: 8  },
        bridge:       { label: 'Bridge',            color: '#f97316', habitat: false, minZoom: 8  },
        reef:         { label: 'Reef',              color: '#f59e0b', habitat: false, minZoom: 8  },
        oyster_reef:  { label: 'Oyster Reef',       color: '#f59e0b', habitat: true,  minZoom: 9  },
        wreck:        { label: 'Wreck',             color: '#d97706', habitat: false, minZoom: 8  },
        inlet:        { label: 'Inlet / Channel',   color: '#38bdf8', habitat: true,  minZoom: 8  },
        marina:       { label: 'Marina / Harbor',   color: '#67e8f9', habitat: false, minZoom: 9  },
        shoal:        { label: 'Shoal',             color: '#94a3b8', habitat: false, minZoom: 9  },
        point:        { label: 'Point / Headland',  color: '#60a5fa', habitat: false, minZoom: 9  },
        beach:        { label: 'Beach / Surf Zone', color: '#fbbf24', habitat: false, minZoom: 9  },
        grass_flat:   { label: 'Grass Flat',        color: '#22c55e', habitat: true,  minZoom: 9  },
        tidal_flat:   { label: 'Tidal Flat',        color: '#6ee7b7', habitat: true,  minZoom: 9  },
        saltmarsh:    { label: 'Saltmarsh Edge',    color: '#34d399', habitat: true,  minZoom: 9  },
        mangrove:     { label: 'Mangrove',          color: '#16a34a', habitat: true,  minZoom: 9  },
        kelp:         { label: 'Kelp Forest',       color: '#4ade80', habitat: true,  minZoom: 9  },
        buoy:         { label: 'Navigation Buoy',   color: '#f43f5e', habitat: false, minZoom: 9  },
        fishing:      { label: 'Fishing Spot',      color: '#2dd4bf', habitat: false, minZoom: 9  },
        fishing_shop: { label: 'Bait & Tackle',     color: '#fb923c', habitat: false, minZoom: 9  },
        boat_ramp:    { label: 'Boat Ramp',         color: '#0ea5e9', habitat: false, minZoom: 9  },
        dive_site:    { label: 'Dive Site',         color: '#0284c7', habitat: false, minZoom: 10 },
        seawall:      { label: 'Seawall',           color: '#6b7280', habitat: false, minZoom: 9  }
    };

    // Habitat area types rendered as filled polygon overlays instead of point markers.
    // Must match POLYGON_HABITAT_TYPES in services/fish_structures.py.
    var POLYGON_HABITAT_TYPES = {
        saltmarsh: true, mangrove: true, tidal_flat: true,
        grass_flat: true, beach: true, oyster_reef: true, inlet: true, kelp: true
    };

    var _MAX_POLYGON_COORDS = 200;
    // Thin a coordinate ring to at most _MAX_POLYGON_COORDS points using
    // uniform Nth-point selection.  Always keeps first and last so closed
    // rings remain closed.  Mirrors _decimate_ring() in fish_structures.py.
    function _decimateRing(coords) {
        var n = coords.length;
        if (n <= _MAX_POLYGON_COORDS) return coords;
        var step = (n - 1) / (_MAX_POLYGON_COORDS - 1);
        var seen = {};
        var out = [];
        for (var i = 0; i < _MAX_POLYGON_COORDS; i++) {
            var idx = Math.round(i * step);
            if (!seen[idx]) { seen[idx] = true; out.push(coords[idx]); }
        }
        // Ensure last point is always included
        var last = coords[n - 1];
        var outLast = out[out.length - 1];
        if (outLast[0] !== last[0] || outLast[1] !== last[1]) out.push(last);
        return out;
    }

    // Symbols rendered inside structure markers — chosen to visually suggest the feature type.
    // These appear as text in the filter-pill labels; SVG icons are used inside the actual markers.
    var SPOT_LABELS = {
        pier:         '⊥',
        jetty:        '≡',
        bridge:       '∩',
        reef:         '≈',
        oyster_reef:  '◌',
        wreck:        '✕',
        inlet:        '⇢',
        marina:       '⚓',
        shoal:        '〜',
        point:        '△',
        beach:        '∿',
        buoy:         '◎',
        fishing:      '✦',
        fishing_shop: '⚙',
        boat_ramp:    '▽',
        dive_site:    '✚',
        seawall:      '▬',
        grass_flat:   '≋',
        tidal_flat:   '⊟',
        saltmarsh:    'Ψ',
        mangrove:     '⊕',
        kelp:         '⇑'
    };

    // Inline SVG path content rendered inside the 22px circular structure markers.
    // ViewBox is 0 0 14 14; paths use stroke/fill values relative to white @ full
    // opacity.  Habitat types (diamond icons) don't use these — they show no inner shape.
    var SPOT_SVGS = {
        // Pier: top platform bar + vertical walkway + two support pilings
        pier:
            '<line x1="7" y1="12" x2="7" y2="3" stroke-width="2"/>' +
            '<line x1="2.5" y1="3" x2="11.5" y2="3" stroke-width="2"/>' +
            '<line x1="4.5" y1="3" x2="4.5" y2="12" stroke-width="1.2" opacity="0.7"/>' +
            '<line x1="9.5" y1="3" x2="9.5" y2="12" stroke-width="1.2" opacity="0.7"/>',

        // Jetty: three horizontal lines = layered rock armour cross-section
        jetty:
            '<line x1="1.5" y1="4.5" x2="12.5" y2="4.5" stroke-width="1.7"/>' +
            '<line x1="1.5" y1="7.5" x2="12.5" y2="7.5" stroke-width="1.7"/>' +
            '<line x1="1.5" y1="10.5" x2="12.5" y2="10.5" stroke-width="1.7"/>',

        // Bridge: parabolic arch + two vertical abutments
        bridge:
            '<path d="M1.5 11.5 Q7 2.5 12.5 11.5" stroke-width="2" fill="none"/>' +
            '<line x1="1.5" y1="11.5" x2="1.5" y2="13.5" stroke-width="2"/>' +
            '<line x1="12.5" y1="11.5" x2="12.5" y2="13.5" stroke-width="2"/>',

        // Reef: double wave suggesting underwater hard-bottom relief
        reef:
            '<path d="M1 9 Q3.5 4.5 7 9 Q10.5 13.5 13 9" stroke-width="2" fill="none"/>' +
            '<path d="M1 5.5 Q3.5 2 7 5.5 Q10.5 9 13 5.5" stroke-width="1.2" fill="none" opacity="0.6"/>',

        // Wreck: charted-wreck X with a faint sunken-hull arc beneath
        wreck:
            '<line x1="3" y1="3" x2="11" y2="10.5" stroke-width="2"/>' +
            '<line x1="11" y1="3" x2="3" y2="10.5" stroke-width="2"/>' +
            '<path d="M2 12.5 Q7 10 12 12.5" stroke-width="1.2" fill="none" opacity="0.65"/>',

        // Marina: classic anchor (ring, stem, crossbar, flukes)
        marina:
            '<circle cx="7" cy="3.2" r="1.6" stroke-width="1.5" fill="none"/>' +
            '<line x1="7" y1="4.8" x2="7" y2="12.5" stroke-width="1.7"/>' +
            '<path d="M4.2 9.5 L7 12.5 L9.8 9.5" stroke-width="1.5" fill="none"/>' +
            '<line x1="3" y1="12.5" x2="11" y2="12.5" stroke-width="1.8"/>',

        // Shoal: S-curve wave = shallow, breaking water
        shoal:
            '<path d="M1 8.5 C3 5 5 12 7 8.5 C9 5 11 12 13 8.5" stroke-width="2" fill="none"/>',

        // Point: solid triangle pointing up = headland jutting seaward
        point:
            '<polygon points="7,2 13,12.5 1,12.5" fill="rgba(255,255,255,0.92)" stroke="none"/>',

        // Beach: shore-line arc + dashed vertical = surf break / waterline
        beach:
            '<path d="M1 11 Q7 6.5 13 11" stroke-width="2.2" fill="none"/>' +
            '<line x1="7" y1="3.5" x2="7" y2="11" stroke-width="1.5" stroke-dasharray="2,1.5"/>',

        // Buoy: bullseye = channel marker ring + centre dot
        buoy:
            '<circle cx="7" cy="7" r="4.5" stroke-width="1.8" fill="none"/>' +
            '<circle cx="7" cy="7" r="1.8" fill="rgba(255,255,255,0.92)" stroke="none"/>',

        // Fishing: 8-pointed asterisk = generic access / catch spot
        fishing:
            '<line x1="7" y1="1.5" x2="7" y2="12.5" stroke-width="1.5"/>' +
            '<line x1="1.5" y1="7" x2="12.5" y2="7" stroke-width="1.5"/>' +
            '<line x1="3" y1="3" x2="11" y2="11" stroke-width="1.5"/>' +
            '<line x1="11" y1="3" x2="3" y2="11" stroke-width="1.5"/>',

        // Fishing shop: shopping-bag silhouette with handle = bait & tackle
        fishing_shop:
            '<path d="M2.5 5.5 h9 l-1.5 7 h-6 z" fill="rgba(255,255,255,0.82)" stroke-width="1.3"/>' +
            '<path d="M5 5.5 V4 a2 2 0 0 1 4 0 V5.5" stroke-width="1.5" fill="none"/>',

        // Boat ramp: downward triangle = ramp slope entering water
        boat_ramp:
            '<polygon points="7,12 1.5,4.5 12.5,4.5" fill="rgba(255,255,255,0.92)" stroke="none"/>' +
            '<line x1="1" y1="13" x2="13" y2="13" stroke-width="2"/>',

        // Dive site: plus / cross = dive-flag crosspiece
        dive_site:
            '<line x1="7" y1="1.5" x2="7" y2="12.5" stroke-width="2.2"/>' +
            '<line x1="1.5" y1="7" x2="12.5" y2="7" stroke-width="2.2"/>',

        // Seawall: thick horizontal bar = wall face / revetment
        seawall:
            '<rect x="1.5" y="5" width="11" height="4" rx="0.5" fill="rgba(255,255,255,0.9)" stroke="none"/>',

        // Oyster reef: overlapping arc-circles suggesting clustered shell mounds
        oyster_reef:
            '<circle cx="4.5" cy="8" r="2.8" stroke-width="1.4" fill="none"/>' +
            '<circle cx="8.5" cy="6.5" r="2.5" stroke-width="1.4" fill="none" opacity="0.9"/>' +
            '<circle cx="7" cy="10.5" r="2.2" stroke-width="1.4" fill="none" opacity="0.8"/>' +
            '<circle cx="10.5" cy="9" r="2" stroke-width="1.3" fill="none" opacity="0.7"/>',

        // Inlet / Channel: two converging shoreline curves with a tidal-flow arrow
        inlet:
            '<path d="M1.5 1.5 Q3 6 1.5 12" stroke-width="1.8" fill="none"/>' +
            '<path d="M12.5 1.5 Q11 6 12.5 12" stroke-width="1.8" fill="none"/>' +
            '<path d="M4.5 7 L9.5 7" stroke-width="1.3" stroke-dasharray="1.5,1.5" opacity="0.8"/>' +
            '<path d="M8 5.5 L10 7 L8 8.5" stroke-width="1.3" fill="none"/>',

        // Grass flat: three sinuous seagrass blades rising from the seafloor
        grass_flat:
            '<path d="M3 13.5 Q2 10 3.5 6.5 Q5 3 3.5 1" stroke-width="1.5" fill="none"/>' +
            '<path d="M7 13.5 Q6 9.5 7.5 5.5 Q9 2 7.5 1" stroke-width="1.5" fill="none"/>' +
            '<path d="M11 13.5 Q10 10 11.5 6.5 Q13 3 11.5 1" stroke-width="1.5" fill="none"/>',

        // Tidal flat: concave basin arc (waterline edge) with stipple dots = exposed mud/sand
        tidal_flat:
            '<path d="M1.5 5 Q7 12 12.5 5" stroke-width="2" fill="none"/>' +
            '<circle cx="4.5" cy="10.5" r="0.8" fill="rgba(255,255,255,0.82)" stroke="none"/>' +
            '<circle cx="7" cy="12.5" r="0.8" fill="rgba(255,255,255,0.82)" stroke="none"/>' +
            '<circle cx="9.5" cy="10.5" r="0.8" fill="rgba(255,255,255,0.82)" stroke="none"/>',

        // Saltmarsh: three reed stems of varying heights above a wavy waterline
        saltmarsh:
            '<line x1="3.5" y1="12.5" x2="3.5" y2="5" stroke-width="1.6"/>' +
            '<line x1="7" y1="12.5" x2="7" y2="1.5" stroke-width="1.6"/>' +
            '<line x1="10.5" y1="12.5" x2="10.5" y2="4" stroke-width="1.6"/>' +
            '<path d="M1 12.5 Q4 11 7 12.5 Q10 14 13 12.5" stroke-width="1.2" fill="none" opacity="0.7"/>',

        // Mangrove: solid canopy disc + trunk + spreading prop roots
        mangrove:
            '<circle cx="7" cy="3.2" r="2.5" fill="rgba(255,255,255,0.85)" stroke="none"/>' +
            '<line x1="7" y1="5.7" x2="7" y2="8.5" stroke-width="1.5"/>' +
            '<path d="M7 8.5 L4 13" stroke-width="1.5" fill="none"/>' +
            '<path d="M7 8.5 L10 13" stroke-width="1.5" fill="none"/>' +
            '<path d="M7 9.5 L5.5 13" stroke-width="1" opacity="0.65" fill="none"/>' +
            '<path d="M7 9.5 L8.5 13" stroke-width="1" opacity="0.65" fill="none"/>',

        // Kelp forest: three sinuous stipes undulating from seafloor to surface
        kelp:
            '<path d="M4 13.5 Q3 10 4.5 7 Q6 4 4.5 1.5" stroke-width="1.5" fill="none"/>' +
            '<path d="M7.5 13.5 Q6.5 9.5 8 6.5 Q9.5 3.5 8 1.5" stroke-width="1.5" fill="none"/>' +
            '<path d="M11 13.5 Q10 10 11.5 7 Q13 4 11.5 1.5" stroke-width="1.5" fill="none"/>'
    };

    // Fishing context tip shown in each structure's tooltip
    var STRUCTURE_TIPS = {
        pier:         'Work the pilings and shadow lines — baitfish stack against current breaks at dawn and dusk.',
        jetty:        'Fish the tip on falling tides; predators ambush bait funneled through the gap. Work the rocks for sheepshead and black drum.',
        bridge:       'Bridge pilings concentrate bait and create current seams. Night fishing under bridge lights is especially productive.',
        reef:         'Hard bottom holds structure species — grouper, snapper, sheepshead. Work the upcurrent edge.',
        oyster_reef:  'Oyster reefs are magnets. Shrimp and crabs hide in the shell; redfish, flounder, and drum patrol the edges on every tide change.',
        wreck:        'Wrecks act as artificial reefs — they concentrate ambush predators. Cast up-current and let bait drift past the structure.',
        inlet:        'Tidal inlets and channels funnel bait on every tide change — one of the most consistent year-round spots. Fish the current seam at the channel edge.',
        marina:       'Marinas concentrate bait around dock pilings and channel edges. Work the shadow lines early morning and at last light.',
        shoal:        'Work the drop from shallow to deep — fish hold on the seam waiting for bait washing off the flat.',
        point:        'Current eddies form on the downcurrent side of headlands and points — predators stack here to ambush bait swept past the tip.',
        beach:        'Work the gutters, rip cuts, and troughs running parallel to shore. Cast beyond the first sandbar — pompano, drum, and stripers feed along the break.',
        grass_flat:   'Seagrass holds shrimp and baitfish. Redfish, speckled trout, and flounder push shallow on rising tides and drop to the flat edges at low.',
        tidal_flat:   'Fish move onto tidal flats as the tide floods, chasing crabs and shrimp into the shallows. Work the edges as the water begins falling.',
        saltmarsh:    'Marsh creek mouths and grass edges are ambush points — redfish and snook use incoming current to pick off bait washing out of the marsh.',
        mangrove:     'Work the mangrove root edges on rising tides; snook, redfish, and tarpon ambush prey along the shadow line.',
        buoy:         'Channel markers and buoys identify edges where deep water meets shallow structure — fish the up-current side.',
        fishing:      'Local fishing access point.',
        fishing_shop: 'Local bait & tackle — stop in for real-time bite reports.',
        kelp:         'Kelp forests hold some of the richest habitat on the Pacific coast — rockfish, lingcod, and kelp bass hold along the canopy edge and base of the fronds.',
        boat_ramp:    'Boat ramps draw early-morning activity. Cast along the ramp edges and nearby channel drops — baitfish concentrate where the bottom changes.',
        dive_site:    'Dive sites flag clear water over structure — the same reefs, ledges, and wrecks divers explore hold trophy fish. Work the upcurrent edge.',
        seawall:      'Seawalls create hard current edges and shadow lines — stripers, bluefish, snook, and tarpon patrol the base of the wall on tide changes. Night fishing near lit walls is consistently productive.'
    };

    function spotTypeLabel(type) {
        return (SPOT_TYPES[type] || {}).label || 'Fishing Spot';
    }
    function spotTypeColor(type) {
        return (SPOT_TYPES[type] || {}).color || '#2dd4bf';
    }

    function makeFishingSpotIcon(type) {
        // Icons are immutable — cache one L.divIcon per type so re-renders of
        // the same viewport (100–300 markers) skip the HTML string build and
        // L.divIcon() object creation entirely after the first render.
        if (_spotIconCache[type]) return _spotIconCache[type];
        var def       = SPOT_TYPES[type] || SPOT_TYPES.fishing;
        var color     = def.color;
        var isHabitat = def.habitat;
        // Habitat = rotating diamond with no inner graphic.
        // Structure = 22px filled circle with an inline SVG icon centred inside.
        var sz  = isHabitat ? 17 : 22;
        var br  = isHabitat ? '3px' : '50%';
        var rot = isHabitat ? 'transform:rotate(45deg)' : '';
        // Habitat diamonds counter-rotate inner content so icons stay upright.
        var innerRot = isHabitat ? 'transform:rotate(-45deg);' : '';
        var svgW = isHabitat ? 10 : 14;
        var inner = '';
        var svgPaths = SPOT_SVGS[type];
        if (svgPaths) {
            // Inline SVG: crisp at any DPI, no cross-platform Unicode variance.
            // stroke/fill inherited from the <svg> root; individual paths may
            // override fill for solid shapes.
            inner = '<svg viewBox="0 0 14 14" width="' + svgW + '" height="' + svgW + '"' +
                    ' stroke="rgba(255,255,255,0.95)" fill="none"' +
                    ' stroke-linecap="round" stroke-linejoin="round"' +
                    ' aria-hidden="true" class="fmap-spot-dot-svg"' + (innerRot ? ' style="' + innerRot + '"' : '') + '>' +
                    svgPaths + '</svg>';
        } else {
            // Fallback for any type not yet in SPOT_SVGS
            var lbl = SPOT_LABELS[type] || '';
            if (lbl) {
                inner = '<span class="fmap-spot-dot-lbl' + (isHabitat ? ' fmap-spot-dot-lbl--habitat' : '') + '"' +
                        (innerRot ? ' style="' + innerRot + '"' : '') + '>' + lbl + '</span>';
            }
        }
        var html = '<span class="fmap-spot-dot" style="background:' + color +
                   ';box-shadow:0 0 7px ' + color + '88;width:' + sz + 'px;height:' + sz + 'px' +
                   ';border-radius:' + br + ';flex-shrink:0;' + rot + '">' + inner + '</span>';
        var icon = L.divIcon({ className: 'fmap-spot-wrap', html: html,
                               iconSize:   [sz + 4, sz + 4],
                               iconAnchor: [Math.ceil((sz + 4) / 2), Math.ceil((sz + 4) / 2)] });
        if (_spotIconKeys.length >= _SPOT_ICON_MAX) {
            var evict = _spotIconKeys.splice(0, 32);
            for (var ei = 0; ei < evict.length; ei++) delete _spotIconCache[evict[ei]];
        }
        _spotIconCache[type] = icon;
        _spotIconKeys.push(type);
        return icon;
    }

    function renderFishingSpots(spots, cacheKey) {
        if (!fishingSpotLayer) return;

        // Build a render key that folds in viewport bounds (~5 km resolution)
        // and the active type filter so panning or toggling filter pills both
        // trigger a re-render even when the underlying cached data is unchanged.
        var vb = map ? map.getBounds() : null;
        var vbKey = vb
            ? (Math.floor(vb.getSouth() * 20) + ',' + Math.floor(vb.getWest()  * 20) + ',' +
               Math.ceil (vb.getNorth() * 20) + ',' + Math.ceil (vb.getEast()  * 20))
            : '';
        var currentZoom = map ? Math.floor(map.getZoom()) : 8;
        var typeKey  = activeSpotTypes.length ? ':f' + activeSpotTypes.slice().sort().join(',') : '';
        var admKey   = adminEditMode ? ':adm' : '';
        var renderKey = (cacheKey || '') + ':' + vbKey + ':z' + currentZoom + typeKey + admKey;

        if (renderKey === _lastRenderedSpotKey && fishingSpotLayer.getLayers().length) {
            return;
        }
        _lastRenderedSpotKey = renderKey;
        fishingSpotLayer.clearLayers();
        _customMarkers = [];  // will be repopulated by renderCustomMarkers below

        // Viewport bounds + 10 % padding for point-marker culling.
        // Polygon habitats (geometry array present) are always included because
        // their outlines may straddle the viewport boundary.
        var vS, vN, vW, vE, doCull = false;
        if (vb) {
            // Scale padding inversely with zoom: at high zoom (street level) the
            // viewport spans only ~1 km, so a fixed 10% ~= 100 m leaves almost no
            // buffer against pan pop-in.  At low zoom the viewport already spans
            // tens of km, so 10% would preload a huge invisible margin for nothing.
            // Cap at 0.025° (~2.8 km) to prevent unnecessary over-loading, and
            // floor at 5% so close-in views still get a meaningful look-ahead buffer.
            var latSpan = vb.getNorth() - vb.getSouth();
            var lngSpan = vb.getEast()  - vb.getWest();
            var latPad  = Math.min(latSpan * 0.05, 0.025);
            var lngPad  = Math.min(lngSpan * 0.05, 0.025);
            vS = vb.getSouth() - latPad;  vN = vb.getNorth() + latPad;
            vW = vb.getWest()  - lngPad;  vE = vb.getEast()  + lngPad;
            doCull = true;
        }

        // Track types whose minZoom exceeds the current zoom so we can hint.
        var _suppressedTypes = {};

        // Render OSM / NOAA spots first
        spots.filter(function (f) {
            if (f.custom) return false;
            // Client-side type filter: activeSpotTypes = [] means show all types.
            // The cache always holds all-types results; filtering here avoids a
            // server round-trip when the user toggles type pills.
            if (activeSpotTypes.length && activeSpotTypes.indexOf(f.type) === -1) return false;
            // Hide types that require a higher zoom level than current.
            // Skip suppression only if the user has *explicitly* enabled this
            // exact type via a filter pill — that signals intent to see it now.
            // When a different set of pills is active, or no pills at all, the
            // minZoom gate still applies so the hint bar stays accurate.
            var typeDef = SPOT_TYPES[f.type];
            var explicitlyChosen = activeSpotTypes.indexOf(f.type) !== -1;
            if (!explicitlyChosen && typeDef && typeDef.minZoom && currentZoom < typeDef.minZoom) {
                _suppressedTypes[f.type] = true;
                return false;
            }
            // Cull point markers that lie outside the padded viewport.
            // Features with a geometry array are polygon habitats — always keep.
            if (doCull && !f.geometry && f.lat && f.lng) {
                return f.lat >= vS && f.lat <= vN && f.lng >= vW && f.lng <= vE;
            }
            return true;
        }).forEach(function (f) {
            var name = f.name || spotTypeLabel(f.type);
            var tip  = f.tip || STRUCTURE_TIPS[f.type] || 'No details available';
            var srcLabel = f.source === 'noaa' ? 'NOAA ENC'
                        : f.source === 'esri' ? 'NOAA/USACE'
                        : 'OpenStreetMap';
            var coordStr = f.lat && f.lng
                ? (Math.round(f.lat * 10000) / 10000) + ', ' + (Math.round(f.lng * 10000) / 10000)
                : '';
            var sym = SPOT_LABELS[f.type] || '';
            var tooltipHtml =
                '<strong>' + esc(name) + '</strong>' +
                '<br><span class="fmap-tooltip-sub">' +
                (sym ? '<span class="fmap-tooltip-sym">' + sym + '</span>' : '') +
                esc(spotTypeLabel(f.type)) + '</span>' +
                (tip ? '<br><span class="fmap-struct-tip">' + esc(tip) + '</span>' : '') +
                '<br><span class="fmap-tooltip-meta">' +
                esc(srcLabel) + (coordStr ? ' · ' + coordStr : '') + '</span>';

            // Habitat area features with geometry → area overlay
            if (f.geometry && f.geometry.length >= 3 && POLYGON_HABITAT_TYPES[f.type]) {
                var color = spotTypeColor(f.type);
                var geom  = f.geometry;
                var layer;

                // Closed ring (OSM closed way): first ≈ last coord → filled polygon.
                // Open linestring (river, canal, tidal channel): coloured stroke only.
                // Use 0.0005° (~55 m) threshold so floating-point drift from server-side
                // coordinate processing or ring decimation never misclassifies a closed
                // polygon as an open stroke.  Force the last vertex to exactly equal the
                // first so Leaflet renders a clean fill without a micro-gap at the seam.
                var first = geom[0], last = geom[geom.length - 1];
                var isClosed = Math.abs(first[0] - last[0]) < 0.0005 &&
                               Math.abs(first[1] - last[1]) < 0.0005;
                if (isClosed) {
                    geom = geom.slice();
                    geom[geom.length - 1] = geom[0];
                }

                if (isClosed) {
                    layer = L.polygon(geom, {
                        color:       color,
                        weight:      2,
                        opacity:     0.85,
                        fillColor:   color,
                        fillOpacity: 0.30,
                        className:   'fmap-habitat-poly'
                    });
                } else {
                    // Open waterway (tidal channel, river, canal, stream) —
                    // draw as a coloured stroke so it traces the channel path
                    // without incorrectly closing the ring into a filled area.
                    layer = L.polyline(geom, {
                        color:     color,
                        weight:    3,
                        opacity:   0.75,
                        className: 'fmap-habitat-poly'
                    });
                }
                layer.bindTooltip(tooltipHtml,
                    { className: 'fmap-tooltip fmap-tooltip--struct', sticky: true });
                fishingSpotLayer.addLayer(layer);
                return;
            }

            // Point / structure features → icon marker (pier, buoy, wreck, etc.)
            if (!f.lat || !f.lng) return;
            var m = L.marker([f.lat, f.lng], { icon: makeFishingSpotIcon(f.type) });
            // Prefer the tip that came from the server; local table is the fallback
            m.bindTooltip(tooltipHtml,
                { className: 'fmap-tooltip fmap-tooltip--struct', direction: 'top', offset: [0, -5] }
            );
            // Click handler: admin mode gets spot actions; everyone else gets
            // the spot detail panel with strike score and best-time information.
            (function (spot, marker) {
                marker.on('click', function () {
                    if (adminEditMode) {
                        _openAdminSpotActions(spot, marker);
                        return;
                    }
                    if (typeof window._fmapShowSpotDetail === 'function') {
                        _activeSpotMarker = marker;
                        window._fmapShowSpotDetail(spot);
                    }
                });
            }(f, m));
            fishingSpotLayer.addLayer(m);
        });

        // Render admin-created custom markers with edit affordances
        renderCustomMarkers(spots);

        // Apply current strike-score tint to newly created markers
        if (_scoreData) {
            var _curHour = (_scoreData.hours || [])[_tideSliderHour] || {};
            _recolourSpotsByScore(_curHour.score || 0);
        }

        // Update the filter hint to surface any types hidden by minZoom.
        _updateZoomSuppressedHint(_suppressedTypes);
    }

    // Show a subtle hint when the current zoom hides some spot types.
    function _updateZoomSuppressedHint(suppressedTypes) {
        if (!_elStructFiltersHint) _elStructFiltersHint = document.getElementById('fmap-struct-filters-hint');
        if (!_elStructFiltersHint) return;
        var keys = Object.keys(suppressedTypes);
        if (!keys.length) {
            // No suppression; let the regular hint text stand.
            _updateSpotTypeHint();
            return;
        }
        var labels = keys.map(function (t) {
            return (SPOT_TYPES[t] || {}).label || t;
        });
        var shown = labels.slice(0, 2).join(', ');
        var extra = labels.length > 2 ? ' +' + (labels.length - 2) + ' more' : '';
        _elStructFiltersHint.textContent =
            'Zoom in to see: ' + shown + extra;
    }

    // ── Structure-query loading / error UI helpers ────────────────────────────
    // Track in-flight requests by gen ID (plain object used as a Set).
    // showStructLoading/hideStructLoading must be called with the same reqGen so
    // each request owns exactly one slot — no double-decrement or premature hide.

    // Register reqGen as holding the spinner open.
    function showStructLoading(reqGen) {
        _structLoadingGens[reqGen] = true;
        if (!_elStructSpinner) _elStructSpinner = document.getElementById('fmap-struct-spinner');
        if (_elStructSpinner) _elStructSpinner.hidden = false;
    }

    // Release reqGen's slot; hide spinner only when no active requests remain.
    function hideStructLoading(reqGen) {
        delete _structLoadingGens[reqGen];
        if (Object.keys(_structLoadingGens).length > 0) return;
        if (!_elStructSpinner) _elStructSpinner = document.getElementById('fmap-struct-spinner');
        if (_elStructSpinner) _elStructSpinner.hidden = true;
        _inflightStructKey = null;
    }

    // Show the dismissible error banner with a custom message.
    function showStructError(msg) {
        if (!_elStructError)    _elStructError    = document.getElementById('fmap-struct-error');
        if (!_elStructErrorMsg) _elStructErrorMsg = document.getElementById('fmap-struct-error-msg');
        if (!_elStructError) return;
        if (_elStructErrorMsg) _elStructErrorMsg.textContent = msg;
        _elStructError.hidden = false;
    }

    // Programmatically hide the error banner (called on next successful load).
    function hideStructError() {
        if (!_elStructError) _elStructError = document.getElementById('fmap-struct-error');
        if (_elStructError) _elStructError.hidden = true;
    }

    // ── Structure cache helpers ───────────────────────────────────────────────

    // Return a cached spot list whose bbox fully contains [s, w, n, e], or
    // null if none found.  All cache keys are bbox-only ("s,w,n,e"); client-
    // side type filtering is handled in renderFishingSpots().  Used to serve
    // viewport queries from a wider pre-fetched corridor without a new request.
    function _cachedSupersetOf(s, w, n, e) {
        for (var k in spotCache) {
            var coords = k.split(',');
            if (coords.length !== 4) continue;
            var cs = +coords[0], cw = +coords[1], cn = +coords[2], ce = +coords[3];
            if (cs <= s && cw <= w && cn >= n && ce >= e) return spotCache[k];
        }
        return null;
    }

    // Pre-fetch structures for a ±1° corridor around the user's saved home
    // location so the cache is warm when they first load the map.  Fires
    // once in the background — results land in spotCache and are served to
    // subsequent viewport queries via _cachedSupersetOf().
    function prefetchHomeCorridorStructures() {
        if (!savedLocationLatLng || !map) return;
        var lat = savedLocationLatLng.lat, lng = savedLocationLatLng.lng;
        var R   = 1.0;  // degrees ≈ 110 km each direction
        var s   = Math.floor((lat - R) * 2) / 2;
        var w   = Math.floor((lng - R) * 2) / 2;
        var n   = Math.ceil ((lat + R) * 2) / 2;
        var e   = Math.ceil ((lng + R) * 2) / 2;
        var key = s + ',' + w + ',' + n + ',' + e;
        if (spotCache[key]) return;  // already warm

        var url = '/api/map/structures?south=' + s + '&west=' + w + '&north=' + n + '&east=' + e;
        fetch(url)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (data && data.structures && !data.zoom_required) {
                    _spotCachePut(key, data.structures);
                    _ssSave();
                    // If the current viewport is already within this corridor,
                    // render immediately (avoids waiting for the next moveend).
                    if (map && map.getZoom() >= 8 && fishingSpotLayer) {
                        var b   = map.getBounds();
                        var vz  = map.getZoom();
                        var exp = Math.min(0.75, Math.max(0, (vz - 8) * 0.19));
                        var vs  = Math.floor((b.getSouth() - exp) * 2) / 2;
                        var vw  = Math.floor((b.getWest()  - exp) * 2) / 2;
                        var vn  = Math.ceil ((b.getNorth() + exp) * 2) / 2;
                        var ve  = Math.ceil ((b.getEast()  + exp) * 2) / 2;
                        // s/w/n/e here are the pre-fetch corridor bounds (closure)
                        if (s <= vs && w <= vw && n >= vn && e >= ve) {
                            var _vkey = vs + ',' + vw + ',' + vn + ',' + ve;
                            _spotCachePut(_vkey, data.structures);
                            renderFishingSpots(data.structures, _vkey);
                            // hint is updated inside renderFishingSpots via _updateZoomSuppressedHint
                        }
                    }
                }
            })
            .catch(function () {});  // silent — regular queryStructures() will still run
    }

    // ── Primary fetch: backend /api/map/structures ────────────────────────────
    // Always fetches all structure types and caches by bbox only.  Type filter
    // pills are applied client-side in renderFishingSpots() so toggling them
    // never triggers a new server request.  Falls back to Overpass on failure.
    function queryStructures() {
        if (!map || !fishingSpotLayer) return;
        var zoom = map.getZoom();
        if (zoom < 8) {
            _lastRenderedSpotKey = null;
            fishingSpotLayer.clearLayers();
            return;
        }

        var b = map.getBounds();

        // ── Zoom-adaptive expansion ───────────────────────────────────────────
        // Scale the look-ahead buffer with zoom level so zoomed-in views get
        // a wide coastal corridor pre-loaded while zoomed-out views (whose
        // viewport already spans a large area) aren't padded unnecessarily.
        //   zoom 12+ → 0.75° (~80 km, covers e.g. Jacksonville NC ↔ SC line)
        //   zoom 10  → 0.38° (~42 km)
        //   zoom 8   → 0°    (5° viewport already covers the region)
        var EXPAND = Math.min(0.75, Math.max(0, (zoom - 8) * 0.19));

        // Cap total span so we never exceed the backend Overpass limits
        // (8° lat / 12° lng).  This matters at low zoom where the viewport
        // itself is already several degrees wide.
        var rawS = b.getSouth() - EXPAND,  rawN = b.getNorth() + EXPAND;
        var rawW = b.getWest()  - EXPAND,  rawE = b.getEast()  + EXPAND;
        if (rawN - rawS > 6) {
            var midLat = (rawS + rawN) / 2;
            rawS = midLat - 3;  rawN = midLat + 3;
        }
        if (rawE - rawW > 9) {
            var midLng = (rawW + rawE) / 2;
            rawW = midLng - 4.5; rawE = midLng + 4.5;
        }

        // Round to 0.5° grid so small pans still hit the cache
        var s = Math.floor(rawS * 2) / 2;
        var w = Math.floor(rawW * 2) / 2;
        var n = Math.ceil (rawN * 2) / 2;
        var e = Math.ceil (rawE * 2) / 2;

        // Always use bbox-only cache key; type filtering is applied client-side
        // in renderFishingSpots() so toggling type pills never triggers a new fetch.
        var key = s + ',' + w + ',' + n + ',' + e;

        if (spotCache[key]) {
            renderFishingSpots(spotCache[key], key);
            return;
        }

        // Check whether a previously fetched (wider) bbox already contains
        // this viewport — e.g. the home-corridor pre-fetch covers zoom-12
        // viewport queries without a second Overpass trip.
        var superResult = _cachedSupersetOf(s, w, n, e);
        if (superResult) {
            _spotCachePut(key, superResult);  // alias so next pan hits directly
            renderFishingSpots(superResult, key);
            return;
        }

        // If this exact bbox is already being fetched (e.g. a filter pill was
        // toggled while the initial Overpass call was still in flight), skip
        // re-fetching.  renderFishingSpots() reads activeSpotTypes at render
        // time, so the current filter will be applied when the in-flight
        // request completes.
        if (_inflightStructKey === key) return;

        // Always fetch all structure types; renderFishingSpots() filters client-side.
        var url = '/api/map/structures' +
            '?south=' + s + '&west=' + w + '&north=' + n + '&east=' + e;

        // Abort any in-flight structure fetch so the previous stale request
        // stops consuming network / server resources immediately.
        if (_structAbort) _structAbort.abort();
        _structAbort = new AbortController();
        var thisGen = ++_structReqGen;
        _inflightStructKey = key;

        showStructLoading(thisGen);
        hideStructError();

        fetch(url, { signal: _structAbort.signal })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) {
                // A newer request has already started — drop this stale result.
                if (thisGen !== _structReqGen) { hideStructLoading(thisGen); return; }

                hideStructLoading(thisGen);
                hideStructError();

                // Server signals the viewport is too large — show hint, clear layers.
                if (data.zoom_required) {
                    _lastRenderedSpotKey = null;
                    fishingSpotLayer.clearLayers();
                    if (!_elStructFiltersHint) _elStructFiltersHint = document.getElementById('fmap-struct-filters-hint');
                    if (_elStructFiltersHint) _elStructFiltersHint.textContent = 'Zoom in further to see structure markers';
                    return;
                }

                var spots = data.structures || [];

                if (data.fetch_failed && spots.length === 0) {
                    showStructLoading(thisGen);
                    showStructError("Loading structure data from backup source…");
                    _queryFishingSpotsFallback(s, w, n, e, key, thisGen, _structAbort ? _structAbort.signal : null);
                    return;
                }

                _spotCachePut(key, spots);
                _ssSave();
                _lastRenderedSpotKey = null;  // force re-render even if key matches stale state
                renderFishingSpots(spots, key);
                // hint is updated inside renderFishingSpots via _updateZoomSuppressedHint
                _scheduleAdjacentPrefetch(s, w, n, e);
            })
            .catch(function (err) {
                if (err.name === 'AbortError') { hideStructLoading(thisGen); return; }
                // Drop response if superseded by a newer request.
                if (thisGen !== _structReqGen) { hideStructLoading(thisGen); return; }
                // Keep spinner visible while Overpass fallback runs;
                // hideStructLoading() is called inside _queryFishingSpotsFallback().
                console.warn('[fishing-map] backend structures failed, falling back to Overpass:', err);
                showStructError("Loading structure data from backup source\u2026");
                _queryFishingSpotsFallback(s, w, n, e, key, thisGen, _structAbort ? _structAbort.signal : null);
            });
    }

    // ── Overpass fallback (used when /api/map/structures is unreachable) ───────
    // Preserves the full tag-matching and dedup logic so the map stays useful
    // even if the server is temporarily down.

    // Map an OSM element's tag dict to a SPOT_TYPES key, or null to discard.
    // Mirrors _classify_osm_tags() in services/fish_structures.py — keep in sync.
    function _classifyOsmTags(tags) {
        var natural  = tags.natural  || '';
        var wetland  = tags.wetland  || '';
        var waterway = tags.waterway || '';
        var manMade  = tags.man_made || '';
        var seamark  = tags['seamark:type'] || '';

        if (natural === 'wetland') {
            if (wetland === 'seagrass' || wetland === 'seagrass_bed' || wetland === 'seagrass_meadow') return 'grass_flat';
            if (wetland === 'saltmarsh' || wetland === 'salt_marsh') return 'saltmarsh';
            if (wetland === 'mangrove' || wetland === 'mangrove_swamp') return 'mangrove';
            if (wetland === 'tidalflat' || wetland === 'tidal_flat' || wetland === 'tidal_flats' || wetland === 'mudflat') return 'tidal_flat';
            if (wetland === 'kelp') return 'kelp';
            return null;
        }
        if (natural === 'kelp' || natural === 'kelp_forest') return 'kelp';
        if (natural === 'seagrass')   return 'grass_flat';
        if (natural === 'mangrove')   return 'mangrove';
        if (natural === 'saltmarsh' || natural === 'salt_marsh') return 'saltmarsh';
        if (natural === 'mud')        return 'tidal_flat';
        if (natural === 'beach')      return 'beach';
        if (natural === 'bay')        return 'inlet';
        if (natural === 'estuary')    return 'inlet';
        if (natural === 'reef') {
            var reefSub = tags['reef:type'] || tags.reef || '';
            if (reefSub === 'oyster' || reefSub === 'oyster_reef') return 'oyster_reef';
            return 'reef';
        }
        if (natural === 'coral_reef' || natural === 'coral') return 'reef';
        if (natural === 'shoal' || natural === 'rock' || natural === 'sandbank') return 'shoal';
        if (natural === 'cape' || natural === 'headland' ||
            natural === 'peninsula' || natural === 'promontory') return 'point';
        if (natural === 'sand' && tags.access !== 'private' && tags.access !== 'no') return 'beach';

        if (tags.harbour === 'yes') return 'inlet';

        if (tags.landuse === 'aquaculture' &&
            (tags.produce === 'oyster' || tags.produce === 'oysters' ||
             tags.product === 'oyster' || tags.product === 'oysters' ||
             tags.aquaculture === 'oyster' || tags.aquaculture === 'oysters')) {
            return 'oyster_reef';
        }

        if (tags.historic === 'wreck' || seamark === 'wreck') return 'wreck';

        if (seamark === 'rock_awash' || seamark === 'underwater_rock' ||
            seamark === 'rock_submerged' || seamark === 'obstruction') return 'shoal';
        if (seamark === 'artificial_reef' || tags.landuse === 'artificial_reef') return 'reef';
        if (seamark === 'dive_site') return 'dive_site';
        if (seamark === 'kelp')      return 'kelp';

        if ((seamark && seamark.indexOf('beacon') === 0) ||
            seamark === 'light_major' || seamark === 'light_minor') return 'buoy';
        if (seamark === 'buoy_special_purpose' || seamark === 'buoy_installation' ||
            seamark === 'mooring' || seamark === 'beacon_special_purpose') return 'buoy';
        if (seamark && seamark.indexOf('buoy') === 0) return 'buoy';

        if (waterway === 'tidal_channel' || waterway === 'tidal_creek' ||
            waterway === 'river' || waterway === 'canal' || waterway === 'stream') return 'inlet';
        if (waterway === 'weir'      || waterway === 'dam'       ||
            waterway === 'waterfall' || waterway === 'rapids'    ||
            waterway === 'fish_pass' || waterway === 'lock') return 'jetty';
        if (waterway === 'dock')     return 'pier';
        if (waterway === 'boatyard') return 'marina';

        if (manMade === 'pier' || manMade === 'wharf' || manMade === 'fishing_platform' || tags.leisure === 'pier') {
            if (tags.access === 'private' || tags.access === 'no') return null;
            return 'pier';
        }
        if (manMade === 'jetty')                              return 'jetty';
        if (manMade === 'groyne' || manMade === 'breakwater') return 'jetty';
        if (manMade === 'seawall' || manMade === 'revetment') return 'seawall';
        if (manMade === 'lighthouse' || manMade === 'offshore_platform') return 'point';
        if (manMade === 'buoy')                               return 'buoy';

        if (tags.bridge === 'yes' && tags.highway) return 'bridge';

        var amenity = tags.amenity || '';
        var leisure = tags.leisure || '';
        var shop    = tags.shop    || '';
        var sport   = tags.sport   || '';

        if (amenity === 'marina' || amenity === 'harbour') return 'marina';
        if (amenity === 'boat_ramp')   return 'boat_ramp';
        if (amenity === 'fishing_pier') return 'pier';
        if (leisure === 'marina')      return 'marina';
        if (leisure === 'slipway')     return 'boat_ramp';
        if (leisure === 'fishing' || leisure === 'fishing_stand' || leisure === 'fishing_pond') return 'fishing';
        if (leisure === 'beach')       return 'beach';
        if (sport === 'scuba_diving' || sport === 'diving' || sport === 'underwater_diving') return 'dive_site';
        if (sport === 'fishing')       return 'fishing';
        if (tags.fishing === 'yes' && amenity !== 'boat_ramp' && leisure !== 'slipway') return 'fishing';
        if (shop === 'fishing' || shop === 'fishing_tackle' || shop === 'bait' ||
            shop === 'tackle'  || shop === 'bait_and_tackle' || shop === 'boat_chandler') return 'fishing_shop';
        if (amenity === 'fishing_shop' || amenity === 'fish_market') return 'fishing_shop';

        return null;
    }

    // Build a type-scoped Overpass QL query.
    // Pass an empty array for `types` to include all structure types.
    // Mirrors _build_overpass_query() in services/fish_structures.py — keep in sync.
    // Uses two named sets: .h (habitats) with `out geom;` for polygon rendering,
    // and .s (structures) with `out center;` to keep the response payload small.
    function _buildFallbackQuery(bbox, types) {
        var all  = !types.length;
        var has  = function (t) { return all || types.indexOf(t) !== -1; };
        var h    = [];   // habitat area statements  → out geom;
        var s    = [];   // structure point statements → out center;

        // ── Habitat area types (need full ring geometry) ──────────────────
        if (has('grass_flat')) {
            h.push('way["natural"="wetland"]["wetland"="seagrass"](' + bbox + ');',
                   'node["natural"="wetland"]["wetland"="seagrass"](' + bbox + ');',
                   'way["natural"="wetland"]["wetland"="seagrass_bed"](' + bbox + ');',
                   'way["natural"="seagrass"](' + bbox + ');',
                   'way["natural"="wetland"]["wetland"="seagrass_meadow"](' + bbox + ');');
        }
        if (has('saltmarsh')) {
            h.push('way["natural"="wetland"]["wetland"="saltmarsh"](' + bbox + ');',
                   'way["natural"="wetland"]["wetland"="salt_marsh"](' + bbox + ');',
                   'way["natural"="saltmarsh"](' + bbox + ');',
                   'way["natural"="salt_marsh"](' + bbox + ');');
        }
        if (has('mangrove')) {
            h.push('way["natural"="wetland"]["wetland"="mangrove"](' + bbox + ');',
                   'way["natural"="mangrove"](' + bbox + ');',
                   'way["natural"="wetland"]["wetland"="mangrove_swamp"](' + bbox + ');');
        }
        if (has('tidal_flat')) {
            h.push('way["natural"="wetland"]["wetland"="tidalflat"](' + bbox + ');',
                   'way["natural"="wetland"]["wetland"="tidal_flat"](' + bbox + ');',
                   'way["natural"="wetland"]["wetland"="tidal_flats"](' + bbox + ');',
                   'way["natural"="wetland"]["wetland"="mudflat"](' + bbox + ');',
                   'way["natural"="mud"](' + bbox + ');');
        }
        if (has('beach')) {
            h.push('way["natural"="beach"](' + bbox + ');',
                   'node["natural"="beach"](' + bbox + ');',
                   'way["leisure"="beach"](' + bbox + ');',
                   'node["leisure"="beach"](' + bbox + ');',
                   'way["natural"="sand"]["access"!="private"](' + bbox + ');');
        }
        if (has('oyster_reef')) {
            h.push('node["landuse"="aquaculture"]["produce"="oyster"](' + bbox + ');',
                   'way["landuse"="aquaculture"]["produce"="oyster"](' + bbox + ');',
                   'way["landuse"="aquaculture"]["produce"="oysters"](' + bbox + ');',
                   'way["landuse"="aquaculture"]["product"="oysters"](' + bbox + ');',
                   'way["landuse"="aquaculture"]["aquaculture"="oyster"](' + bbox + ');',
                   'way["natural"="reef"]["reef:type"="oyster"](' + bbox + ');',
                   'way["natural"="reef"]["reef"="oyster"](' + bbox + ');');
        }
        if (has('kelp')) {
            h.push('way["natural"="wetland"]["wetland"="kelp"](' + bbox + ');',
                   'way["natural"="kelp"](' + bbox + ');',
                   'way["natural"="kelp_forest"](' + bbox + ');',
                   'node["seamark:type"="kelp"](' + bbox + ');');
        }
        if (has('inlet')) {
            h.push('way["waterway"="tidal_channel"](' + bbox + ');',
                   'way["waterway"="tidal_creek"](' + bbox + ');',
                   'way["waterway"="river"](' + bbox + ');',
                   'way["waterway"="canal"](' + bbox + ');',
                   'node["waterway"="stream"](' + bbox + ');',
                   'way["waterway"="stream"](' + bbox + ');',
                   'node["harbour"="yes"](' + bbox + ');',
                   'way["harbour"="yes"](' + bbox + ');',
                   'node["natural"="bay"](' + bbox + ');',
                   'way["natural"="bay"](' + bbox + ');',
                   'node["natural"="estuary"](' + bbox + ');',
                   'way["natural"="estuary"](' + bbox + ');');
        }

        // ── Structure point/linear types (centroid only) ──────────────────
        if (has('reef')) {
            s.push('node["natural"="reef"](' + bbox + ');',
                   'way["natural"="reef"](' + bbox + ');',
                   'node["natural"="coral_reef"](' + bbox + ');',
                   'way["natural"="coral_reef"](' + bbox + ');',
                   'node["natural"="coral"](' + bbox + ');',
                   'way["natural"="coral"](' + bbox + ');',
                   'node["seamark:type"="artificial_reef"](' + bbox + ');',
                   'way["seamark:type"="artificial_reef"](' + bbox + ');',
                   'node["landuse"="artificial_reef"](' + bbox + ');',
                   'way["landuse"="artificial_reef"](' + bbox + ');');
        }
        if (has('wreck')) {
            s.push('node["historic"="wreck"](' + bbox + ');',
                   'way["historic"="wreck"](' + bbox + ');',
                   'node["seamark:type"="wreck"](' + bbox + ');',
                   'way["seamark:type"="wreck"](' + bbox + ');');
        }
        if (has('shoal')) {
            s.push('node["natural"="shoal"](' + bbox + ');',
                   'way["natural"="shoal"](' + bbox + ');',
                   'node["natural"="sandbank"](' + bbox + ');',
                   'way["natural"="sandbank"](' + bbox + ');',
                   'node["natural"="rock"](' + bbox + ');',
                   'node["seamark:type"="rock_awash"](' + bbox + ');',
                   'node["seamark:type"="underwater_rock"](' + bbox + ');',
                   'node["seamark:type"="rock_submerged"](' + bbox + ');',
                   'node["seamark:type"="obstruction"](' + bbox + ');');
        }
        if (has('pier')) {
            // Only publicly accessible piers — private docks are excluded
            s.push('node["man_made"="pier"]["access"!="private"]["access"!="no"](' + bbox + ');',
                   'way["man_made"="pier"]["access"!="private"]["access"!="no"](' + bbox + ');',
                   'node["man_made"="wharf"]["access"!="private"]["access"!="no"](' + bbox + ');',
                   'way["man_made"="wharf"]["access"!="private"]["access"!="no"](' + bbox + ');',
                   'node["man_made"="fishing_platform"](' + bbox + ');',
                   'way["man_made"="fishing_platform"](' + bbox + ');',
                   'node["leisure"="pier"]["access"!="private"]["access"!="no"](' + bbox + ');',
                   'way["leisure"="pier"]["access"!="private"]["access"!="no"](' + bbox + ');',
                   'node["waterway"="dock"](' + bbox + ');',
                   'way["waterway"="dock"](' + bbox + ');',
                   'node["amenity"="fishing_pier"](' + bbox + ');',
                   'way["amenity"="fishing_pier"](' + bbox + ');');
        }
        if (has('jetty')) {
            s.push('node["man_made"="jetty"](' + bbox + ');',
                   'way["man_made"="jetty"](' + bbox + ');',
                   'node["man_made"="groyne"](' + bbox + ');',
                   'way["man_made"="groyne"](' + bbox + ');',
                   'node["man_made"="breakwater"](' + bbox + ');',
                   'way["man_made"="breakwater"](' + bbox + ');',
                   'node["waterway"="weir"](' + bbox + ');',
                   'way["waterway"="weir"](' + bbox + ');',
                   'node["waterway"="dam"](' + bbox + ');',
                   'way["waterway"="dam"](' + bbox + ');',
                   'node["waterway"="waterfall"](' + bbox + ');',
                   'node["waterway"="rapids"](' + bbox + ');',
                   'way["waterway"="rapids"](' + bbox + ');',
                   'node["waterway"="fish_pass"](' + bbox + ');',
                   'way["waterway"="fish_pass"](' + bbox + ');',
                   'node["waterway"="lock"](' + bbox + ');');
        }
        if (has('bridge')) {
            s.push('way["bridge"="yes"]["highway"~"^(primary|secondary|tertiary|trunk|unclassified|residential|service)$"](' + bbox + ');',
                   'way["bridge"="yes"]["highway"="footway"](' + bbox + ');',
                   'way["bridge"="yes"]["highway"="path"](' + bbox + ');',
                   'way["bridge"="yes"]["highway"="pedestrian"](' + bbox + ');');
        }
        if (has('marina')) {
            s.push('node["amenity"="marina"](' + bbox + ');',
                   'way["amenity"="marina"](' + bbox + ');',
                   'node["amenity"="harbour"](' + bbox + ');',
                   'way["amenity"="harbour"](' + bbox + ');',
                   'node["leisure"="marina"](' + bbox + ');',
                   'way["leisure"="marina"](' + bbox + ');',
                   'relation["leisure"="marina"](' + bbox + ');',
                   'node["waterway"="boatyard"](' + bbox + ');',
                   'way["waterway"="boatyard"](' + bbox + ');');
        }
        if (has('point')) {
            s.push('node["natural"="cape"](' + bbox + ');',
                   'node["natural"="headland"](' + bbox + ');',
                   'way["natural"="headland"](' + bbox + ');',
                   'node["natural"="peninsula"](' + bbox + ');',
                   'node["natural"="promontory"](' + bbox + ');',
                   'node["man_made"="lighthouse"](' + bbox + ');',
                   'node["man_made"="offshore_platform"](' + bbox + ');');
        }
        if (has('fishing')) {
            s.push('node["leisure"="fishing"](' + bbox + ');',
                   'way["leisure"="fishing"](' + bbox + ');',
                   'node["leisure"="fishing_stand"](' + bbox + ');',
                   'way["leisure"="fishing_stand"](' + bbox + ');',
                   'node["leisure"="fishing_pond"](' + bbox + ');',
                   'way["leisure"="fishing_pond"](' + bbox + ');',
                   'node["fishing"="yes"]["leisure"!="slipway"]["amenity"!="boat_ramp"](' + bbox + ');',
                   'node["sport"="fishing"](' + bbox + ');');
        }
        if (has('buoy')) {
            s.push('node["seamark:type"="buoy_lateral"](' + bbox + ');',
                   'node["seamark:type"="buoy_cardinal"](' + bbox + ');',
                   'node["seamark:type"="buoy_safe_water"](' + bbox + ');',
                   'node["seamark:type"="buoy_isolated_danger"](' + bbox + ');',
                   'node["seamark:type"="buoy_special_purpose"](' + bbox + ');',
                   'node["seamark:type"="buoy_installation"](' + bbox + ');',
                   'node["seamark:type"="mooring"](' + bbox + ');',
                   'node["seamark:type"="beacon_lateral"](' + bbox + ');',
                   'node["seamark:type"="beacon_cardinal"](' + bbox + ');',
                   'node["seamark:type"="beacon_safe_water"](' + bbox + ');',
                   'node["seamark:type"="beacon_isolated_danger"](' + bbox + ');',
                   'node["seamark:type"="beacon_special_purpose"](' + bbox + ');',
                   'node["seamark:type"="light_major"](' + bbox + ');',
                   'node["seamark:type"="light_minor"](' + bbox + ');',
                   'node["man_made"="buoy"](' + bbox + ');');
        }
        if (has('fishing_shop')) {
            s.push('node["shop"="fishing"](' + bbox + ');',
                   'way["shop"="fishing"](' + bbox + ');',
                   'node["shop"="fishing_tackle"](' + bbox + ');',
                   'node["shop"="bait"](' + bbox + ');',
                   'node["shop"="tackle"](' + bbox + ');',
                   'node["shop"="bait_and_tackle"](' + bbox + ');',
                   'node["shop"="boat_chandler"](' + bbox + ');',
                   'way["shop"="boat_chandler"](' + bbox + ');',
                   'node["amenity"="fish_market"](' + bbox + ');',
                   'node["amenity"="fishing_shop"](' + bbox + ');');
        }
        if (has('boat_ramp')) {
            s.push('node["amenity"="boat_ramp"](' + bbox + ');',
                   'way["amenity"="boat_ramp"](' + bbox + ');',
                   'node["leisure"="slipway"](' + bbox + ');',
                   'way["leisure"="slipway"](' + bbox + ');');
        }
        if (has('dive_site')) {
            s.push('node["sport"="scuba_diving"](' + bbox + ');',
                   'node["sport"="diving"](' + bbox + ');',
                   'node["sport"="underwater_diving"](' + bbox + ');',
                   'way["sport"="scuba_diving"](' + bbox + ');',
                   'node["seamark:type"="dive_site"](' + bbox + ');');
        }
        if (has('seawall')) {
            s.push('node["man_made"="seawall"](' + bbox + ');',
                   'way["man_made"="seawall"](' + bbox + ');',
                   'node["man_made"="revetment"](' + bbox + ');',
                   'way["man_made"="revetment"](' + bbox + ');');
        }

        if (!h.length && !s.length) return '';
        var q = '[out:json][timeout:30];';
        if (h.length) q += '(' + h.join('') + ')->.h;';
        if (s.length) q += '(' + s.join('') + ')->.s;';
        if (h.length) q += '.h out geom;';
        if (s.length) q += '.s out center;';
        return q;
    }

    // gen: the _structReqGen value captured when the parent queryStructures() call
    // started.  If a newer call has since fired, we discard results rather than
    // rendering stale data over fresh markers.
    // signal: AbortSignal from the same AbortController as the parent fetch so
    // cancelling the structure request also cancels the Overpass fallback chain.
    function _queryFishingSpotsFallback(s, w, n, e, key, gen, signal) {
        var bbox = s + ',' + w + ',' + n + ',' + e;
        // Always fetch all types so the cached result serves every filter combination.
        var q = _buildFallbackQuery(bbox, []);
        if (!q) {
            hideStructLoading(gen);
            renderFishingSpots([]);
            return;
        }

        var overpassBody = 'data=' + encodeURIComponent(q);
        function tryOverpass(urlIndex) {
            var url = OVERPASS_URLS[urlIndex] || OVERPASS_URLS[0];
            var opts = {
                method:  'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body:    overpassBody,
            };
            if (signal) opts.signal = signal;
            return fetch(url, opts).then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            }).catch(function (err) {
                if (err.name === 'AbortError') throw err;  // propagate abort, don't retry
                if (urlIndex + 1 < OVERPASS_URLS.length) {
                    console.warn('[fishing-map] Overpass mirror ' + url + ' failed, trying next…');
                    return tryOverpass(urlIndex + 1);
                }
                throw err;
            });
        }

        tryOverpass(0)
        .then(function (data) {
            // Discard if a newer queryStructures() call has already fired.
            if (gen !== _structReqGen) { hideStructLoading(gen); return; }

            var spots = (data.elements || []).map(function (el) {
                var lat  = el.lat  || (el.center && el.center.lat);
                var lng  = el.lon  || (el.center && el.center.lon);
                // `out geom;` omits `center` for ways — compute centroid from geometry
                if (!lat && el.geometry && el.geometry.length) {
                    var sumLat = 0, sumLon = 0, cnt = 0;
                    el.geometry.forEach(function (g) {
                        if (g && g.lat != null && g.lon != null) { sumLat += g.lat; sumLon += g.lon; cnt++; }
                    });
                    if (cnt) { lat = sumLat / cnt; lng = sumLon / cnt; }
                }
                var tags = el.tags || {};
                var type = _classifyOsmTags(tags);
                if (!type) return null;
                var name = tags.name || tags['seamark:name'] || tags['seamark:buoy:colour'] ||
                           tags['addr:housename'] || '';
                var spot = { lat: lat, lng: lng, name: name, type: type };
                // Attach polygon geometry for habitat area types (out geom; response)
                if (el.type === 'way' && POLYGON_HABITAT_TYPES[type] && el.geometry) {
                    var coords = el.geometry
                        .filter(function (g) { return g && g.lat != null && g.lon != null; })
                        .map(function (g) { return [g.lat, g.lon]; });
                    if (coords.length >= 3) spot.geometry = _decimateRing(coords);
                }
                return spot;
            }).filter(function (f) {
                if (!f || !f.lat || !f.lng) return false;
                return f.lat >= s && f.lat <= n && f.lng >= w && f.lng <= e;
            });

            var deduped = deduplicateSpots(spots);
            _spotCachePut(key, deduped);
            _ssSave();
            hideStructLoading(gen); // request chain complete; drop spinner
            hideStructError();   // fallback succeeded — dismiss the error banner
            renderFishingSpots(deduped, key);
            // hint is updated inside renderFishingSpots via _updateZoomSuppressedHint
            _scheduleAdjacentPrefetch(s, w, n, e);
        })
        .catch(function (err) {
            hideStructLoading(gen); // both paths must release the spinner
            if (err && err.name === 'AbortError') return;
            console.error('[fishing-map] Overpass fallback error:', err);
            showStructError("Couldn\u2019t load structure data; showing basic map markers.");
            renderFishingSpots([]);
        });
    }

    // Collapse duplicate markers: same name → one, or same type within proximity threshold.
    // Polygon habitat types (beach, saltmarsh, mangrove, etc.) skip centroid-proximity
    // dedup entirely — adjacent polygon patches are distinct features.
    //
    // Uses an O(n) grid-cell approach (matching the backend): each spot is assigned to a
    // cell of size `thresh` and a 3×3 neighbourhood check replaces the previous O(n²)
    // `out.some(...)` scan.  For 200-500 spots per viewport this cuts ~40k-250k comparisons
    // down to ~1800-4500 hash lookups.
    function deduplicateSpots(spots) {
        // 0 = skip proximity dedup for polygon habitat types
        var PROX = { inlet: 0.005, marina: 0.004,
                     beach: 0, grass_flat: 0, saltmarsh: 0,
                     tidal_flat: 0, mangrove: 0, oyster_reef: 0,
                     _default: 0.002 };
        var namedSeen = {};  // "type|lowercaseName" → true
        var grid      = {};  // "type|gridLat|gridLng" → true
        var out = [];

        spots.forEach(function (spot) {
            // Deduplicate by name within type — e.g. multiple segments of the same bridge
            if (spot.name) {
                var nameKey = spot.type + '|' + spot.name.toLowerCase().trim();
                if (namedSeen[nameKey]) return;
                namedSeen[nameKey] = true;
            }
            // Proximity dedup via O(n) grid — skip for polygon area types (thresh === 0)
            var thresh = PROX.hasOwnProperty(spot.type) ? PROX[spot.type] : PROX._default;
            if (thresh > 0) {
                var gl = Math.floor(spot.lat / thresh);
                var gn = Math.floor(spot.lng / thresh);
                var t  = spot.type;
                var tooClose = false;
                outer: for (var dl = -1; dl <= 1; dl++) {
                    for (var dm = -1; dm <= 1; dm++) {
                        if (grid[t + '|' + (gl + dl) + '|' + (gn + dm)]) {
                            tooClose = true;
                            break outer;
                        }
                    }
                }
                if (tooClose) return;
                grid[t + '|' + gl + '|' + gn] = true;
            }
            out.push(spot);
        });
        return out;
    }

    function scheduleFishingSpotQuery() {
        clearTimeout(spotQueryTimer);
        // Use a short delay when the incoming bbox is already in cache so
        // panning through pre-fetched areas feels instant (~80 ms latency vs
        // the 300 ms debounce we keep for cold-cache fetches that hit Overpass).
        var delay = 300;
        if (map) {
            var _b = map.getBounds(), _z = map.getZoom();
            var _exp = Math.min(0.75, Math.max(0, (_z - 8) * 0.19));
            var _s = Math.floor((_b.getSouth() - _exp) * 2) / 2;
            var _w = Math.floor((_b.getWest()  - _exp) * 2) / 2;
            var _n = Math.ceil ((_b.getNorth() + _exp) * 2) / 2;
            var _e = Math.ceil ((_b.getEast()  + _exp) * 2) / 2;
            var _k = _s + ',' + _w + ',' + _n + ',' + _e;
            if (spotCache[_k] || _cachedSupersetOf(_s, _w, _n, _e)) delay = 80;
        }
        spotQueryTimer = setTimeout(queryStructures, delay);
    }

    // Re-render the fishing spot layer from whatever bbox data is already cached,
    // without triggering a new network request.  Used when filter pills are toggled
    // so the switch between spot types is instant.  If no cache exists yet the
    // in-flight moveend fetch will call renderFishingSpots() when it completes,
    // at which point activeSpotTypes will already reflect the new selection.
    function _renderFromCache() {
        if (!map || !fishingSpotLayer) return;
        var zoom = map.getZoom();
        if (zoom < 8) return;
        var b = map.getBounds();
        var EXPAND = Math.min(0.75, Math.max(0, (zoom - 8) * 0.19));
        var rawS = b.getSouth() - EXPAND,  rawN = b.getNorth() + EXPAND;
        var rawW = b.getWest()  - EXPAND,  rawE = b.getEast()  + EXPAND;
        if (rawN - rawS > 6) { var mLat = (rawS + rawN) / 2; rawS = mLat - 3; rawN = mLat + 3; }
        if (rawE - rawW > 9) { var mLng = (rawW + rawE) / 2; rawW = mLng - 4.5; rawE = mLng + 4.5; }
        var s = Math.floor(rawS * 2) / 2,  w = Math.floor(rawW * 2) / 2;
        var n = Math.ceil (rawN * 2) / 2,  e = Math.ceil (rawE * 2) / 2;
        var key = s + ',' + w + ',' + n + ',' + e;
        var cached = spotCache[key];
        if (!cached) {
            var sup = _cachedSupersetOf(s, w, n, e);
            if (sup) { _spotCachePut(key, sup); cached = sup; }
        }
        if (cached) renderFishingSpots(cached, key);
        // No cache → no-op; the background fetch will render when it lands.
        // Always re-evaluate the AI layer too — filters may have changed.
        scheduleAIQuery();
    }

    // ─── Adjacent-tile background pre-fetch ───────────────────────────────────
    // After each successful structure fetch, silently queue the 4 cardinal-
    // direction neighbours (N/S/E/W) so that panning into them is a cache hit.
    // At most one background request runs at a time with a 5 s gap to respect
    // Overpass rate limits.  The queue caps at _PREFETCH_MAX_QUEUE; excess entries
    // are dropped rather than stacking up from rapid map movement.

    function _enqueuePrefetch(s, w, n, e) {
        var key = s + ',' + w + ',' + n + ',' + e;
        if (spotCache[key]) return;
        if (_cachedSupersetOf(s, w, n, e)) return;
        // Don't enqueue the same bbox twice
        for (var i = 0; i < _prefetchQueue.length; i++) {
            if (_prefetchQueue[i].key === key) return;
        }
        if (_prefetchQueue.length >= _PREFETCH_MAX_QUEUE) {
            _prefetchQueue.shift();  // drop the oldest pending tile
        }
        _prefetchQueue.push({ s: s, w: w, n: n, e: e, key: key, ts: Date.now() });
    }

    var _PREFETCH_JOB_TTL = 120000; // drop jobs older than 2 minutes (user has moved on)

    function _drainPrefetchQueue() {
        if (_prefetchInFlight || _prefetchQueue.length === 0) return;
        var job = _prefetchQueue.shift();
        // Skip stale jobs: user has likely panned away since this was enqueued.
        if (Date.now() - job.ts > _PREFETCH_JOB_TTL) { _drainPrefetchQueue(); return; }
        // Skip if the tile was cached between enqueue and drain
        if (spotCache[job.key] || _cachedSupersetOf(job.s, job.w, job.n, job.e)) {
            _drainPrefetchQueue();
            return;
        }
        _prefetchInFlight = true;
        var url = '/api/map/structures?south=' + job.s + '&west=' + job.w +
                  '&north=' + job.n + '&east=' + job.e;
        fetch(url)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                _prefetchInFlight = false;
                if (data && data.structures && !data.zoom_required) {
                    _spotCachePut(job.key, data.structures);
                    _ssSave();
                }
                setTimeout(_drainPrefetchQueue, _PREFETCH_DELAY);
            })
            .catch(function () {
                _prefetchInFlight = false;
                setTimeout(_drainPrefetchQueue, _PREFETCH_DELAY);
            });
    }

    // Enqueue the 4 cardinal neighbours of the just-fetched bbox.
    // Step = half the bbox span so tiles overlap 50 %, ensuring smooth panning.
    function _scheduleAdjacentPrefetch(s, w, n, e) {
        var latSpan = n - s;
        var lngSpan = e - w;
        // Snap steps to the 0.5° cache grid to guarantee key-matching on pan
        var latStep = Math.round(latSpan * 0.5 * 2) / 2;
        var lngStep = Math.round(lngSpan * 0.5 * 2) / 2;
        if (latStep <= 0) latStep = 0.5;
        if (lngStep <= 0) lngStep = 0.5;

        _enqueuePrefetch(
            Math.floor((s         ) * 2) / 2, Math.floor((w + lngStep) * 2) / 2,
            Math.ceil ((n         ) * 2) / 2, Math.ceil ((e + lngStep) * 2) / 2
        ); // east
        _enqueuePrefetch(
            Math.floor((s         ) * 2) / 2, Math.floor((w - lngStep) * 2) / 2,
            Math.ceil ((n         ) * 2) / 2, Math.ceil ((e - lngStep) * 2) / 2
        ); // west
        _enqueuePrefetch(
            Math.floor((s + latStep) * 2) / 2, Math.floor(w * 2) / 2,
            Math.ceil ((n + latStep) * 2) / 2, Math.ceil (e * 2) / 2
        ); // north
        _enqueuePrefetch(
            Math.floor((s - latStep) * 2) / 2, Math.floor(w * 2) / 2,
            Math.ceil ((n - latStep) * 2) / 2, Math.ceil (e * 2) / 2
        ); // south

        // Start draining after a short delay — let the current render finish first
        setTimeout(_drainPrefetchQueue, 1500);
    }

    // ─── localStorage persistence for spotCache ───────────────────────────────
    // Persists structure data across browser sessions — fish structures (piers,
    // reefs, wrecks, marinas) don't move — a pier built in 1970 is still there.
    // 30-day TTL means a returning angler never waits for a re-fetch unless a
    // full month has passed since they last visited this part of the coast.
    // Quota errors are silently ignored — worst case the cache starts cold.
    var _SS_KEY = 'fmap_spot_cache_v4'; // bump version to drop old sessionStorage entries
    var _SS_TTL = 2592000000; // 30 days in ms

    function _ssLoad() {
        try {
            var raw = localStorage.getItem(_SS_KEY);
            if (!raw) return;
            var obj = JSON.parse(raw);
            var now = Date.now();
            Object.keys(obj).forEach(function (k) {
                if (k.indexOf('|') !== -1) return; // skip legacy type-keyed entries
                var e = obj[k];
                if (e && e.ts && (now - e.ts) < _SS_TTL && Array.isArray(e.data)) {
                    spotCache[k] = e.data;
                    _spotCacheKeys.push(k);
                }
            });
            while (_spotCacheKeys.length > _SPOT_CACHE_MAX) {
                delete spotCache[_spotCacheKeys.shift()];
            }
        } catch (e) { /* quota or parse error — start cold */ }
    }

    function _ssSaveNow() {
        try {
            var now = Date.now();
            var obj = {};
            Object.keys(spotCache).forEach(function (k) {
                obj[k] = { ts: now, data: spotCache[k] };
            });
            localStorage.setItem(_SS_KEY, JSON.stringify(obj));
        } catch (e) { /* quota exceeded — silently skip */ }
    }

    // Debounced save — coalesce rapid sequential fetches into one write.
    function _ssSave() {
        clearTimeout(_ssSaveTimer);
        _ssSaveTimer = setTimeout(_ssSaveNow, 1500);
    }

    // Write a new entry into spotCache with LRU eviction.
    // Keeps spotCache at most _SPOT_CACHE_MAX entries so sessionStorage
    // serialization stays bounded regardless of how much the user pans.
    function _spotCachePut(key, data) {
        if (!Object.prototype.hasOwnProperty.call(spotCache, key)) {
            if (_spotCacheKeys.length >= _SPOT_CACHE_MAX) {
                var evict = _spotCacheKeys.shift();
                delete spotCache[evict];
            }
            _spotCacheKeys.push(key);
        }
        spotCache[key] = data;
    }

    // ─── Autocomplete ─────────────────────────────────────────────────────────
    // ─── localStorage persistence ─────────────────────────────────────────────
    // Key is versioned — bump when adding incompatible fields so old saved data
    // is silently ignored rather than causing unexpected UI state for users.
    var LS_KEY = 'fmap_filters_v5';  // v5: species filter removed

    function saveFilters() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                spotTypes: activeSpotTypes.slice(),
                category:  _activeCategory || null
            }));
        } catch (e) {
            console.warn('[fishing-map] saveFilters failed:', e);
        }
    }

    function loadFilters() {
        try {
            var raw = localStorage.getItem(LS_KEY);
            if (!raw) return;
            var f = JSON.parse(raw);
            // Only restore from storage when restoreFromHash() hasn't already
            // applied types from the URL — the hash (shared link) wins.
            if (Array.isArray(f.spotTypes) && f.spotTypes.length && !activeSpotTypes.length) {
                var valid = f.spotTypes.filter(function (t) { return SPOT_TYPES[t]; });
                if (valid.length) _applySpotTypeUI(valid);
            }
            // Restore active category tab (deferred until wireCategoryFilterTabs runs)
            if (f.category) {
                var _tryRestoreCat = function () {
                    var tab = document.querySelector('.fmap-cat-tab[data-cat="' + f.category + '"]');
                    if (tab) tab.click();
                };
                // Tabs are wired in boot() shortly after loadFilters; defer one tick
                setTimeout(_tryRestoreCat, 0);
            }
            updateAdvBadge();
        } catch (e) {
            console.warn('[fishing-map] loadFilters failed:', e);
        }
    }

    // ─── Auto-center on saved location ───────────────────────────────────────
    var hasAutoZoomed = false;
    var savedLocationLatLng = null; // lat/lng of user's saved forecast location

    // ─── Loading indicator ────────────────────────────────────────────────────
    function _hideMainLoading() {
        if (!els.loading) return;
        els.loading.style.opacity = '0';
        setTimeout(function () { if (els.loading) els.loading.style.pointerEvents = 'none'; }, 300);
    }

    // ─── Utilities ────────────────────────────────────────────────────────────


    function timeAgo(dateStr) {
        if (!dateStr) return '';
        var then = new Date(dateStr.indexOf('Z') === -1 ? dateStr + 'Z' : dateStr);
        var now  = new Date();
        var secs = Math.floor((now - then) / 1000);
        if (secs < 60)    return 'just now';
        if (secs < 3600)  return Math.floor(secs / 60) + 'm ago';
        if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
        var days = Math.floor(secs / 86400);
        if (days < 7)   return days + 'd ago';
        if (days < 30)  return Math.floor(days / 7) + 'w ago';
        if (days < 365) return Math.floor(days / 30) + 'mo ago';
        return Math.floor(days / 365) + 'y ago';
    }

    // ─── Advanced filters ─────────────────────────────────────────────────────

    function updateAdvBadge() {
        var n = activeSpotTypes.length;
        var countEl = document.getElementById('fmap-sec-count-filters');
        if (countEl) {
            countEl.textContent = n + ' on';
            countEl.style.display = n > 0 ? '' : 'none';
        }
    }

    function setPillActive(selector, attrName, value) {
        document.querySelectorAll(selector).forEach(function (b) {
            b.classList.toggle('fmap-pill--active', b.getAttribute(attrName) === value);
        });
    }

    // ─── Structure type-filter pills ─────────────────────────────────────────
    // Wires .fmap-pill--spot-type[data-type] toggle buttons so that anglers can
    // restrict which structure types appear on the map.  Empty activeSpotTypes
    // means "show all" (the default state).
    //
    // The spot cache is bbox-keyed and always stores all types; filtering is
    // applied client-side in renderFishingSpots() so toggling pills never
    // triggers a new server round-trip when data is already cached.

    // Apply an array of type strings to activeSpotTypes and sync pill UI.
    // Passes an empty array to reset to "show all".
    function _applySpotTypeUI(types) {
        activeSpotTypes = types.slice();
        document.querySelectorAll('.fmap-pill--spot-type').forEach(function (btn) {
            var t      = btn.getAttribute('data-type');
            var active = activeSpotTypes.indexOf(t) !== -1;
            btn.classList.toggle('fmap-pill--active', active);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        _updateSpotTypeHint();
    }

    // Debounced save so rapid toggles don't spam localStorage.
    function _scheduleSpotTypeSave() {
        if (_spotTypeSaveTimer) clearTimeout(_spotTypeSaveTimer);
        _spotTypeSaveTimer = setTimeout(function () {
            _spotTypeSaveTimer = null;
            saveFilters();
        }, 400);
    }

    // Update the hint text and clear-button visibility to reflect the current
    // activeSpotTypes selection.  Called after every toggle and on clear.
    function _updateSpotTypeHint() {
        if (!_elStructFiltersHint) _elStructFiltersHint = document.getElementById('fmap-struct-filters-hint');
        if (!_elSpotTypesClear)    _elSpotTypesClear    = document.getElementById('fmap-spot-types-clear');
        var n     = activeSpotTypes.length;
        var total = Object.keys(SPOT_TYPES).length;
        if (_elStructFiltersHint) {
            _elStructFiltersHint.textContent = n === 0
                ? 'All types visible \u2014 tap to filter'
                : 'Showing ' + n + ' of ' + total + ' types \u2014 tap to adjust';
        }
        if (_elSpotTypesClear) _elSpotTypesClear.hidden = n === 0;
    }

    function wireSpotTypeFilters() {
        var pills = document.querySelectorAll('.fmap-pill--spot-type');

        pills.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var type = btn.getAttribute('data-type');
                if (!type) return;

                var idx     = activeSpotTypes.indexOf(type);
                var active  = idx === -1;   // will become active after this click
                if (active) {
                    activeSpotTypes.push(type);
                } else {
                    activeSpotTypes.splice(idx, 1);
                }

                // Visual + ARIA toggle state
                btn.classList.toggle('fmap-pill--active', active);
                btn.setAttribute('aria-pressed', active ? 'true' : 'false');

                _updateSpotTypeHint();
                updateAdvBadge();
                _scheduleSpotTypeSave();

                // Re-render instantly from the cached bbox data.
                // Never trigger a new network fetch — type filtering is client-side.
                _renderFromCache();
            });
        });

        // "Clear filter" button — resets all pills to inactive / show-all
        var clearBtn = document.getElementById('fmap-spot-types-clear');
        if (clearBtn) {
            clearBtn.addEventListener('click', function () {
                activeSpotTypes = [];
                pills.forEach(function (btn) {
                    btn.classList.remove('fmap-pill--active');
                    btn.setAttribute('aria-pressed', 'false');
                });
                _updateSpotTypeHint();
                updateAdvBadge();
                _scheduleSpotTypeSave();
                _renderFromCache();
            });
        }

        // Wire the error banner dismiss button so users can manually clear it.
        var dismissBtn = document.getElementById('fmap-struct-error-dismiss');
        if (dismissBtn) {
            dismissBtn.addEventListener('click', hideStructError);
        }
    }

    // ─── Community layer ──────────────────────────────────────────────────────

    function makeCommunityPin(isMine) {
        var cls = isMine ? 'fmap-community-pin--mine' : 'fmap-community-pin--public';
        return L.divIcon({
            className: 'fmap-community-pin-wrap',
            html: '<span class="fmap-community-pin ' + cls + '"></span>',
            iconSize:    [22, 28],
            iconAnchor:  [11, 26],
            popupAnchor: [0, -26]
        });
    }

    function loadCommunityPins() {
        if (!communityLayerOn || !map || !communityLayer) return;

        // Cancel any in-flight request so rapid panning doesn't pile up stale responses.
        if (_communityAbort) { _communityAbort.abort(); }
        _communityAbort = new AbortController();

        // Clear immediately so pins from the previous viewport don't linger
        // if the new fetch fails or takes a long time.
        communityLayer.clearLayers();

        var b    = map.getBounds();
        var sw   = b.getSouthWest();
        var ne   = b.getNorthEast();
        var url  = '/api/map/catches?sw_lat=' + Math.round(sw.lat * 100) / 100 +
                   '&sw_lng=' + Math.round(sw.lng * 100) / 100 +
                   '&ne_lat=' + Math.round(ne.lat * 100) / 100 +
                   '&ne_lng=' + Math.round(ne.lng * 100) / 100 +
                   '&limit=200';
        fetch(url, { signal: _communityAbort.signal })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function (data) {
                communityData = data.catches || [];
                communityData.forEach(function (c) {
                    if (!c.lat || !c.lng) return;
                    var m = L.marker([c.lat, c.lng], { icon: makeCommunityPin(c.mine) });
                    var _tapOrClick = window.matchMedia('(pointer: coarse)').matches ? 'Tap' : 'Click';
                    m.bindTooltip(
                        '<strong>' + esc(c.species) + '</strong><br>' +
                        '<span class="fmap-tooltip-sub">' + esc(c.angler_name) + ' &bull; ' + timeAgo(c.caught_at) + '</span>' +
                        '<span class="fmap-tooltip-meta">' + _tapOrClick + ' to view catch details</span>',
                        { className: 'fmap-tooltip', direction: 'top', offset: [0, -6] }
                    );
                    m.on('click', function () { openCatchDetail(c); });
                    communityLayer.addLayer(m);
                });
                // Update community tab badge
                var badge = document.getElementById('fmap-community-badge');
                if (badge) {
                    badge.textContent = communityData.length;
                    badge.style.display = communityData.length ? '' : 'none';
                }
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return; // superseded by newer fetch
                console.warn('[fishing-map] loadCommunityPins failed:', err);
                communityData = [];
                var badge = document.getElementById('fmap-community-badge');
                if (badge) badge.style.display = 'none';
            });
    }

    function scheduleCommunityLoad() {
        clearTimeout(communityTimer);
        communityTimer = setTimeout(loadCommunityPins, 700);
    }

    function wireCommunityLayer() {
        if (!map) return;
        communityLayer = L.layerGroup();

        // Escape key closes the catch detail drawer; Tab is trapped within while open
        document.addEventListener('keydown', function (e) {
            if (!els.catchDetail || els.catchDetail.hidden) return;
            if (e.key === 'Escape') { closeCatchDetail(); return; }
            _trapFocusOnTab(els.catchDetail, e);
        });

        var btn = document.getElementById('fmap-community-layer-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                communityLayerOn = !communityLayerOn;
                btn.classList.toggle('fmap-ctrl-btn--active', communityLayerOn);
                btn.setAttribute('aria-pressed', communityLayerOn ? 'true' : 'false');
                if (communityLayerOn) {
                    communityLayer.addTo(map);
                    loadCommunityPins();
                    map.on('moveend zoomend', scheduleCommunityLoad);
                } else {
                    map.removeLayer(communityLayer);
                    communityLayer.clearLayers();
                    map.off('moveend zoomend', scheduleCommunityLoad);
                }
            });
        }
    }

    // ─── Community catch detail drawer ────────────────────────────────────────

    var _catchDetailPrevFocus = null;

    function openCatchDetail(c) {
        if (!els.catchDetail) return;
        _catchDetailPrevFocus = document.activeElement || null;

        if (_catchDetailAbort) { try { _catchDetailAbort.abort(); } catch (e) {} }
        _catchDetailAbort = new AbortController();

        // Title: use explicit title if set, otherwise fall back to species name
        els.catchDetailTitle.textContent = c.title ? c.title : c.species;
        var dateStr = c.caught_at ? new Date(c.caught_at.indexOf('Z') === -1
            ? c.caught_at + 'Z' : c.caught_at).toLocaleDateString() : '';
        // Show species below the title when a custom title is present
        var speciesTag = (c.title && c.title !== c.species)
            ? ' \u2022 ' + esc(c.species) : '';
        els.catchDetailMeta.innerHTML = esc(c.angler_name) +
            (dateStr ? ' \u2022 ' + dateStr : '') + speciesTag;

        var bodyHtml = '';
        // Catch photo
        if (c.image_url) {
            var _photoAlt = c.species ? esc(c.species) + ' catch photo' : 'Catch photo';
            bodyHtml += '<div class="fmap-catch-photo-wrap">' +
                '<img src="' + esc(c.image_url) + '" class="fmap-catch-photo" alt="' + _photoAlt + '" ' +
                'loading="lazy" onerror="this.parentNode.style.display=\'none\'">' +
                '</div>';
        }
        if (c.weight_lb) bodyHtml += '<div class="fmap-catch-stat"><span class="fmap-catch-stat-label">Weight</span>' + parseFloat(c.weight_lb).toFixed(1) + ' lb</div>';
        if (c.length_in) bodyHtml += '<div class="fmap-catch-stat"><span class="fmap-catch-stat-label">Length</span>' + c.length_in + ' in</div>';
        if (c.bait)      bodyHtml += '<div class="fmap-catch-stat"><span class="fmap-catch-stat-label">Bait</span>' + esc(c.bait) + '</div>';
        if (c.notes)     bodyHtml += '<div class="fmap-catch-notes">' + esc(c.notes) + '</div>';
        els.catchDetailBody.innerHTML = bodyHtml;

        // Load comments
        var _myAbort = _catchDetailAbort;
        els.catchDetailComments.innerHTML = '<div class="fmap-catch-no-comments">Loading comments…</div>';
        fetch('/api/map/catches/' + c.id + '/comments', { signal: _myAbort.signal })
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (data) {
                var comments = data.comments || [];
                var html = '';
                comments.forEach(function (cm) {
                    html += '<div class="fmap-catch-comment">' +
                        '<div class="fmap-catch-comment-hdr">' +
                        '<span class="fmap-catch-comment-author">' + esc(cm.angler_name) + '</span>' +
                        '<span class="fmap-catch-comment-time">' + timeAgo(cm.created_at) + '</span>' +
                        '</div>' +
                        '<div class="fmap-catch-comment-body">' + esc(cm.body) + '</div>' +
                        '</div>';
                });
                if (!html) html = '<div class="fmap-catch-no-comments">No comments yet</div>';
                // Add comment form if logged in
                var commentForm = IS_LOGGED_IN
                    ? '<div class="fmap-catch-comment-form">' +
                      '<input type="text" class="fmap-catch-comment-input" placeholder="Add a comment…"' +
                      ' aria-label="Add a comment" maxlength="500" autocomplete="off" enterkeyhint="send">' +
                      '<button class="fmap-catch-comment-post" aria-label="Post comment" data-catch-id="' + c.id + '">Post</button></div>'
                    : '';
                els.catchDetailComments.innerHTML = html + commentForm;

                var postBtn = els.catchDetailComments.querySelector('.fmap-catch-comment-post');
                var commentInput = els.catchDetailComments.querySelector('.fmap-catch-comment-input');
                if (postBtn && commentInput) {
                    function _postComment() {
                        var body = commentInput.value.trim();
                        if (!body) return;
                        postBtn.disabled = true;
                        postBtn.setAttribute('aria-busy', 'true');
                        fetch('/api/map/catches/' + c.id + '/comments', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ body: body })
                        })
                        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                        .then(function () { openCatchDetail(c); })
                        .catch(function () {
                            postBtn.disabled = false;
                            postBtn.setAttribute('aria-busy', 'false');
                            showToast('Could not post comment.');
                        });
                    }
                    postBtn.addEventListener('click', _postComment);
                    commentInput.addEventListener('keydown', function (e) {
                        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _postComment(); }
                    });
                }
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return;
                els.catchDetailComments.innerHTML = '';
            });

        // Action buttons: like + delete (own catches)
        var _likeCt = c.likes_count || 0;
        var actionsHtml = '';
        actionsHtml += '<button class="fmap-catch-action-btn fmap-like-btn" data-catch-id="' + c.id + '"' +
            ' aria-label="Like this catch (' + _likeCt + ' like' + (_likeCt !== 1 ? 's' : '') + ')">' +
            '<span aria-hidden="true">\u2764\uFE0F</span> ' + _likeCt + ' like' + (_likeCt !== 1 ? 's' : '') + '</button>';
        if (c.mine) {
            actionsHtml += '<button class="fmap-catch-action-btn fmap-catch-action-btn--delete fmap-delete-btn" data-catch-id="' + c.id + '"' +
                ' aria-label="Delete this catch">' +
                '<span aria-hidden="true">\uD83D\uDDD1</span> Delete</button>';
        }
        els.catchDetailActions.innerHTML = actionsHtml;

        var likeBtn = els.catchDetailActions.querySelector('.fmap-like-btn');
        if (likeBtn) {
            likeBtn.addEventListener('click', function () {
                if (!IS_LOGGED_IN) { showToast('Sign in to like catches'); return; }
                likeBtn.disabled = true;
                likeBtn.setAttribute('aria-busy', 'true');
                fetch('/api/map/catches/' + c.id + '/like', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                })
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                .then(function (d) {
                    var _lc = d.likes_count;
                    likeBtn.innerHTML = '<span aria-hidden="true">\u2764\uFE0F</span> ' + _lc + ' like' + (_lc !== 1 ? 's' : '');
                    likeBtn.setAttribute('aria-label', 'Like this catch (' + _lc + ' like' + (_lc !== 1 ? 's' : '') + ')');
                    likeBtn.classList.toggle('fmap-catch-action-btn--liked', d.liked);
                    c.likes_count = _lc;
                    likeBtn.disabled = false;
                    likeBtn.setAttribute('aria-busy', 'false');
                })
                .catch(function () {
                    showToast('Could not update like.');
                    likeBtn.disabled = false;
                    likeBtn.setAttribute('aria-busy', 'false');
                });
            });
        }

        var deleteBtn = els.catchDetailActions.querySelector('.fmap-delete-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', function () {
                if (!confirm('Delete this catch?')) return;
                fetch('/api/map/catches/' + c.id, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' }
                })
                .then(function () {
                    showToast('Catch deleted.');
                    closeCatchDetail();
                    loadCommunityPins();
                })
                .catch(function () { showToast('Could not delete catch.'); });
            });
        }

        els.catchDetail.hidden = false;
        var _cdInner = els.catchDetail.querySelector('.fmap-detail-inner');
        if (_cdInner) _cdInner.scrollTop = 0;

        var closeBtn = document.getElementById('fmap-catch-detail-close');
        if (closeBtn) {
            closeBtn.onclick = closeCatchDetail;
            setTimeout(function () { closeBtn.focus(); }, 50);
        }
    }

    function closeCatchDetail() {
        if (els.catchDetail) els.catchDetail.hidden = true;
        if (_catchDetailPrevFocus && typeof _catchDetailPrevFocus.focus === 'function') {
            _catchDetailPrevFocus.focus({ preventScroll: true });
            _catchDetailPrevFocus = null;
        }
    }

    // ─── Log Catch mode ───────────────────────────────────────────────────────

    function _updateLogFab(active) {
        var btn   = document.getElementById('fmap-log-catch-btn');
        if (!btn) return;
        var plus  = btn.querySelector('.fmap-log-fab-plus');
        var x     = btn.querySelector('.fmap-log-fab-x');
        var label = btn.querySelector('.fmap-log-fab-label');
        btn.classList.toggle('fmap-log-fab--active', active);
        btn.setAttribute('aria-label', active ? 'Cancel log mode' : 'Log a catch on the map');
        if (plus)  plus.hidden  = active;
        if (x)     x.hidden     = !active;
        if (label) label.textContent = active ? 'Cancel' : 'Log Catch';
    }

    function enterLogMode() {
        if (!IS_LOGGED_IN) {
            showToast('Sign in to log catches on the map');
            return;
        }
        catchLogMode = true;
        _updateLogFab(true);
        var wrap = document.querySelector('.fmap-map-wrap');
        if (wrap) wrap.classList.add('fmap-log-mode-active');
        // Banner on map
        if (!document.getElementById('fmap-log-banner')) {
            var banner = document.createElement('div');
            banner.id = 'fmap-log-banner';
            banner.className = 'fmap-log-mode-banner';
            banner.setAttribute('role', 'alert');
            banner.setAttribute('aria-live', 'assertive');
            var _tc = window.matchMedia('(pointer: coarse)').matches ? 'Tap' : 'Click';
            banner.textContent = _tc + ' the map to place your catch pin \u2014 ' + _tc.toLowerCase() + ' again to cancel';
            if (wrap) wrap.appendChild(banner);
        }
    }

    function exitLogMode() {
        catchLogMode = false;
        _updateLogFab(false);
        var wrap = document.querySelector('.fmap-map-wrap');
        if (wrap) wrap.classList.remove('fmap-log-mode-active');
        var banner = document.getElementById('fmap-log-banner');
        if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
        if (pendingCatchMarker && map) {
            map.removeLayer(pendingCatchMarker);
            pendingCatchMarker = null;
        }
        pendingCatchLatLng = null;
    }

    var _logModalPrevFocus = null;
    function openLogModal(lat, lng) {
        _logModalPrevFocus = document.activeElement || null;
        pendingCatchLatLng = { lat: lat, lng: lng };
        if (els.logCoords) {
            var _coordStr = lat.toFixed(5) + ', ' + lng.toFixed(5);
            els.logCoords.textContent = _coordStr;
            els.logCoords.setAttribute('aria-label', 'Logging catch at coordinates ' + _coordStr);
        }
        if (els.logForm) els.logForm.reset();
        if (els.logPublic) els.logPublic.checked = true;
        // Pre-fill caught_at with current local datetime
        if (els.logCaughtAt) {
            var now = new Date();
            var pad = function (n) { return String(n).padStart(2, '0'); };
            els.logCaughtAt.value =
                now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) +
                'T' + pad(now.getHours()) + ':' + pad(now.getMinutes());
        }
        if (els.logModal) {
            els.logModal.hidden = false;
            var _lmInner = els.logModal.querySelector('.fmap-modal-inner');
            if (_lmInner) _lmInner.scrollTop = 0;
        }
        if (els.logSpecies) els.logSpecies.focus();
        if (els.logError) els.logError.hidden = true;
    }

    function closeLogModal() {
        if (els.logModal) els.logModal.hidden = true;
        if (els.logSubmit) { els.logSubmit.disabled = false; els.logSubmit.setAttribute('aria-busy', 'false'); }
        if (els.logError)  els.logError.hidden  = true;
        exitLogMode();
        if (_logModalPrevFocus && typeof _logModalPrevFocus.focus === 'function') {
            _logModalPrevFocus.focus({ preventScroll: true });
            _logModalPrevFocus = null;
        }
    }

    function wireLogCatch() {
        var logBtn = document.getElementById('fmap-log-catch-btn');
        if (logBtn) logBtn.addEventListener('click', function () {
            if (catchLogMode) { exitLogMode(); return; }
            enterLogMode();
        });

        var closeBtn = document.getElementById('fmap-log-modal-close');
        if (closeBtn) closeBtn.addEventListener('click', closeLogModal);
        var cancelBtn = document.getElementById('fmap-log-cancel');
        if (cancelBtn) cancelBtn.addEventListener('click', closeLogModal);

        // Escape closes the log modal; Tab is trapped within while open
        document.addEventListener('keydown', function (e) {
            if (!els.logModal || els.logModal.hidden) return;
            if (e.key === 'Escape') { closeLogModal(); return; }
            _trapFocusOnTab(els.logModal, e);
        });

        // Map click in log mode — place pin
        if (map) {
            map.on('click', function (e) {
                if (!catchLogMode) return;
                var lat = e.latlng.lat;
                var lng = e.latlng.lng;

                // Remove previous temp marker
                if (pendingCatchMarker) map.removeLayer(pendingCatchMarker);
                pendingCatchMarker = L.marker([lat, lng], {
                    icon: L.divIcon({
                        className: 'fmap-community-pin-wrap',
                        html: '<span class="fmap-community-pin fmap-community-pin--mine"></span>',
                        iconSize: [22, 28], iconAnchor: [11, 26]
                    })
                }).addTo(map);

                openLogModal(lat, lng);
            });
        }

        // Form submit
        if (els.logForm) {
            els.logForm.addEventListener('submit', function (e) {
                e.preventDefault();
                if (!pendingCatchLatLng) { closeLogModal(); return; }

                var species = els.logSpecies ? els.logSpecies.value.trim() : '';
                if (!species) {
                    if (els.logError) { els.logError.textContent = 'Species is required.'; els.logError.hidden = false; }
                    return;
                }

                var payload = {
                    lat:       pendingCatchLatLng.lat,
                    lng:       pendingCatchLatLng.lng,
                    species:   species,
                    title:     els.logTitle    ? els.logTitle.value.trim()    : '',
                    bait:      els.logBait     ? els.logBait.value.trim()     : '',
                    notes:     els.logNotes    ? els.logNotes.value.trim()    : '',
                    image_url: els.logImageUrl ? els.logImageUrl.value.trim() : '',
                    is_public: els.logPublic   ? els.logPublic.checked        : true
                };
                if (els.logWeight && els.logWeight.value) payload.weight_lb = parseFloat(els.logWeight.value);
                if (els.logLength && els.logLength.value) payload.length_in = parseFloat(els.logLength.value);
                // caught_at: convert datetime-local value ("YYYY-MM-DDTHH:MM") to ISO string
                if (els.logCaughtAt && els.logCaughtAt.value) {
                    payload.caught_at = els.logCaughtAt.value + ':00';
                }

                if (els.logSubmit) { els.logSubmit.disabled = true; els.logSubmit.setAttribute('aria-busy', 'true'); }

                fetch('/api/map/catches', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                })
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                .then(function (data) {
                    if (data.error) {
                        if (els.logError) { els.logError.textContent = data.error; els.logError.hidden = false; }
                        if (els.logSubmit) { els.logSubmit.disabled = false; els.logSubmit.setAttribute('aria-busy', 'false'); }
                        return;
                    }
                    showToast('Catch logged! \uD83C\uDFAF');
                    closeLogModal();
                    if (communityLayerOn) loadCommunityPins();
                })
                .catch(function () {
                    if (els.logError) { els.logError.textContent = 'Could not save catch. Please try again.'; els.logError.hidden = false; }
                    if (els.logSubmit) { els.logSubmit.disabled = false; els.logSubmit.setAttribute('aria-busy', 'false'); }
                });
            });
        }
    }

    // ─── Admin marker editing ─────────────────────────────────────────────────
    // Only active when MAP_IS_ADMIN is true (the 'Conner' account).
    // Renders custom markers returned by /api/map/structures as draggable,
    // and wires a toolbar button + modal for add / edit / delete.

    var adminEditMode    = false;
    var _customMarkers   = [];  // [{id, leaflet, data}] — live custom marker state
    var _adminPreviewPin = null; // temporary L.marker shown while the add modal is open

    function _customMarkerIcon(type, editMode) {
        if (!editMode) return makeFishingSpotIcon(type);
        var color = (SPOT_TYPES[type] || SPOT_TYPES.fishing).color;
        return L.divIcon({
            className: 'fmap-spot-wrap',
            html: '<span style="display:flex;align-items:center;justify-content:center;' +
                  'width:22px;height:22px;border-radius:50%;background:' + color + ';' +
                  'border:2.5px dashed #fff;box-shadow:0 0 8px ' + color + '88;' +
                  'font-size:9px;font-weight:800;color:rgba(255,255,255,0.95);' +
                  'font-family:system-ui,sans-serif;cursor:pointer">✎</span>',
            iconSize:   [22, 22],
            iconAnchor: [11, 11],
        });
    }

    // Render (or re-render) custom markers on the fishing spot layer.
    // Called after queryStructures() resolves — spots with custom:true get
    // draggable markers when adminEditMode is on.
    function renderCustomMarkers(spots) {
        // Remove existing custom markers
        _customMarkers.forEach(function (cm) {
            if (fishingSpotLayer) fishingSpotLayer.removeLayer(cm.leaflet);
        });
        _customMarkers = [];

        if (!map || !fishingSpotLayer) return;

        spots.filter(function (s) { return s.custom; }).forEach(function (spot) {
            var m = L.marker([spot.lat, spot.lng], {
                icon:      _customMarkerIcon(spot.type, adminEditMode),
                draggable: adminEditMode,
                title:     spot.name || spotTypeLabel(spot.type),
            });

            m.on('dragend', function () {
                var ll = m.getLatLng();
                var prevLat = spot.lat, prevLng = spot.lng;
                spot.lat = ll.lat;
                spot.lng = ll.lng;

                fetch('/api/map/custom-markers/' + spot.id, {
                    method:  'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ lat: ll.lat, lng: ll.lng }),
                })
                .then(function (r) {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    // Invalidate cache so panning away and back shows the new position
                    spotCache = {}; _spotCacheKeys = [];
                    try { localStorage.removeItem(_SS_KEY); } catch (_e) {}
                    _showAdminToast('Position saved');
                })
                .catch(function (err) {
                    console.warn('[admin] drag-save failed:', err);
                    // Revert marker and data to the original position
                    spot.lat = prevLat;
                    spot.lng = prevLng;
                    m.setLatLng([prevLat, prevLng]);
                    _showAdminToast('Save failed — ' + err.message, true);
                });
            });

            m.on('click', function () {
                if (adminEditMode) {
                    _openAdminEditPanel(spot);
                } else {
                    var tip = spot.description || STRUCTURE_TIPS[spot.type] || '';
                    m.bindPopup(
                        '<strong>' + esc(spot.name || spotTypeLabel(spot.type)) + '</strong>' +
                        (tip ? '<br><span class="fmap-tooltip-sub">' + esc(tip) + '</span>' : '')
                    ).openPopup();
                }
            });

            m.bindTooltip(
                '<strong>' + esc(spot.name || spotTypeLabel(spot.type)) + '</strong>' +
                '<br><span class="fmap-tooltip-sub">' + esc(spotTypeLabel(spot.type)) + '</span>' +
                (adminEditMode ? '<br><em class="fmap-tooltip-sub">click to edit</em>' : ''),
                { direction: 'top', offset: [0, -8], className: 'fmap-tooltip' }
            );

            fishingSpotLayer.addLayer(m);
            _customMarkers.push({ id: spot.id, leaflet: m, data: spot });
        });
    }

    // ── Admin modal ──────────────────────────────────────────────────────────

    function _removeAdminPreviewPin() {
        if (_adminPreviewPin && map) {
            map.removeLayer(_adminPreviewPin);
            _adminPreviewPin = null;
        }
    }

    function _openAdminAddPanel(lat, lng) {
        if (!document.getElementById('fmap-admin-modal')) return;
        document.getElementById('fmap-admin-modal-title').textContent = 'Add Marker';
        document.getElementById('fmap-admin-lat').value  = lat.toFixed(6);
        document.getElementById('fmap-admin-lng').value  = lng.toFixed(6);
        document.getElementById('fmap-admin-name').value = '';
        document.getElementById('fmap-admin-type').value = 'fishing';
        document.getElementById('fmap-admin-desc').value = '';
        document.getElementById('fmap-admin-delete').hidden = true;
        document.getElementById('fmap-admin-save').dataset.markerId = '';

        // Show a temporary pin so the admin can see exactly where the marker will land
        _removeAdminPreviewPin();
        _adminPreviewPin = L.marker([lat, lng], {
            icon: L.divIcon({
                className: 'fmap-spot-wrap',
                html: '<span style="display:flex;align-items:center;justify-content:center;' +
                      'width:26px;height:26px;border-radius:50%;background:#2563eb;' +
                      'border:3px solid #fff;box-shadow:0 0 12px #2563eb99;' +
                      'font-size:15px;animation:fmap-pulse 1s ease-in-out infinite alternate">📍</span>',
                iconSize: [26, 26], iconAnchor: [13, 26],
            }),
            interactive: false,
        }).addTo(map);

        _openAdminModal();
    }

    function _openAdminEditPanel(spot) {
        if (!document.getElementById('fmap-admin-modal')) return;
        document.getElementById('fmap-admin-modal-title').textContent = 'Edit Marker';
        document.getElementById('fmap-admin-lat').value  = spot.lat.toFixed(6);
        document.getElementById('fmap-admin-lng').value  = spot.lng.toFixed(6);
        document.getElementById('fmap-admin-name').value = spot.name || '';
        document.getElementById('fmap-admin-type').value = spot.type || 'fishing';
        document.getElementById('fmap-admin-desc').value = spot.description || '';
        document.getElementById('fmap-admin-save').dataset.markerId = spot.id;
        var delBtn = document.getElementById('fmap-admin-delete');
        delBtn.hidden = false;
        delBtn.dataset.markerId = spot.id;
        _openAdminModal();
    }

    function _openAdminModal() {
        var modal    = document.getElementById('fmap-admin-modal');
        var backdrop = document.getElementById('fmap-admin-backdrop');
        if (modal)    modal.hidden    = false;
        if (backdrop) backdrop.hidden = false;
        var statusEl = document.getElementById('fmap-admin-status');
        if (statusEl) { statusEl.textContent = ''; statusEl.style.color = ''; }
    }

    function _closeAdminModal() {
        var modal    = document.getElementById('fmap-admin-modal');
        var backdrop = document.getElementById('fmap-admin-backdrop');
        if (modal)    modal.hidden    = true;
        if (backdrop) backdrop.hidden = true;
        _removeAdminPreviewPin();
    }

    // Brief non-blocking toast shown after drag-saves and other silent actions.
    function _showAdminToast(msg, isError) {
        var t = document.getElementById('fmap-admin-toast');
        if (!t) {
            t = document.createElement('div');
            t.id = 'fmap-admin-toast';
            t.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);' +
                'z-index:4000;padding:8px 18px;border-radius:8px;font-size:.85rem;font-weight:600;' +
                'color:#fff;pointer-events:none;transition:opacity .3s;white-space:nowrap';
            document.body.appendChild(t);
        }
        t.textContent = msg;
        t.style.background = isError ? '#dc2626' : '#16a34a';
        t.style.opacity = '1';
        clearTimeout(t._hideTimer);
        t._hideTimer = setTimeout(function () { t.style.opacity = '0'; }, 2200);
    }

    // ── Admin spot-suppression helpers ──────────────────────────────────────

    // Open a compact popup on an OSM/NOAA/ESRI spot offering "Hide" and
    // "Override" actions.  Only shown when adminEditMode is true.
    function _openAdminSpotActions(spot, marker) {
        var spotKey = spot.id || (spot.type + ':' + spot.lat + ':' + spot.lng);
        var name    = esc(spot.name || spotTypeLabel(spot.type));

        var html =
            '<div class="fmap-admin-action-popup">' +
            '<strong class="fmap-admin-action-title">' + name + '</strong>' +
            '<div class="fmap-admin-action-btns">' +
            '<button id="fmap-spot-hide-btn" class="fmap-admin-action-btn fmap-admin-action-btn--hide">Hide</button>' +
            '<button id="fmap-spot-override-btn" class="fmap-admin-action-btn fmap-admin-action-btn--override">Override</button>' +
            '</div>' +
            '<div id="fmap-spot-action-status" class="fmap-admin-action-status"></div>' +
            '</div>';

        marker.bindPopup(html, { closeButton: true, maxWidth: 220 }).openPopup();

        // Wire buttons after the popup DOM is inserted
        marker.once('popupopen', function () {
            var hideBtn     = document.getElementById('fmap-spot-hide-btn');
            var overrideBtn = document.getElementById('fmap-spot-override-btn');
            var statusEl    = document.getElementById('fmap-spot-action-status');

            if (hideBtn) {
                hideBtn.addEventListener('click', function () {
                    hideBtn.disabled = true;
                    hideBtn.textContent = '…';
                    _suppressSpot(spot, spotKey, function (err) {
                        if (err) {
                            if (statusEl) statusEl.textContent = 'Failed: ' + err;
                            hideBtn.disabled = false;
                            hideBtn.textContent = 'Hide';
                        } else {
                            marker.closePopup();
                            _showAdminToast('Spot hidden');
                        }
                    });
                });
            }

            if (overrideBtn) {
                overrideBtn.addEventListener('click', function () {
                    marker.closePopup();
                    // Pre-fill the add panel at the spot's location so the admin
                    // can drop a precisely placed custom marker that replaces it.
                    _openAdminAddPanel(spot.lat, spot.lng);
                    var nameEl = document.getElementById('fmap-admin-name');
                    var typeEl = document.getElementById('fmap-admin-type');
                    if (nameEl) nameEl.value = spot.name || spotTypeLabel(spot.type);
                    if (typeEl) typeEl.value = spot.type || 'fishing';
                    // Also suppress the original so there's no duplicate
                    _suppressSpot(spot, spotKey, function () {});
                });
            }
        });
    }

    // Call POST /api/map/suppress-spot for a spot; cb(null) on success, cb(errMsg) on failure.
    function _suppressSpot(spot, spotKey, cb) {
        fetch('/api/map/suppress-spot', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                spot_key: spotKey,
                lat:      spot.lat,
                lng:      spot.lng,
                type:     spot.type || '',
                name:     spot.name || '',
            }),
        })
        .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            // Bust the local spot cache so the suppressed spot disappears on next render
            spotCache = {}; _spotCacheKeys = [];
            try { localStorage.removeItem(_SS_KEY); } catch (_e) {}
            scheduleFishingSpotQuery();
            cb(null);
        })
        .catch(function (err) {
            console.warn('[admin] suppress spot failed:', err);
            cb(err.message);
        });
    }

    function wireAdminMode() {
        if (typeof MAP_IS_ADMIN === 'undefined' || !MAP_IS_ADMIN) return;

        // Escape closes modal; Tab trapped within while visible
        var _adminModalEl = document.getElementById('fmap-admin-modal');
        document.addEventListener('keydown', function (e) {
            if (!_adminModalEl || _adminModalEl.hidden) {
                if (e.key === 'Escape') _closeAdminModal();
                return;
            }
            if (e.key === 'Escape') { _closeAdminModal(); return; }
            _trapFocusOnTab(_adminModalEl, e);
        });

        // ── Toggle button ────────────────────────────────────────────────────
        var btn = document.getElementById('fmap-admin-edit-btn');
        if (btn) {
            btn.hidden = false;
            btn.addEventListener('click', function () {
                adminEditMode = !adminEditMode;
                btn.classList.toggle('fmap-ctrl-btn--active', adminEditMode);
                btn.setAttribute('aria-pressed', adminEditMode ? 'true' : 'false');
                btn.title = adminEditMode ? 'Exit edit mode' : 'Edit markers (admin)';

                // Swap icon style and draggability on live custom markers
                _customMarkers.forEach(function (cm) {
                    cm.leaflet.setIcon(_customMarkerIcon(cm.data.type, adminEditMode));
                    if (adminEditMode) {
                        cm.leaflet.dragging.enable();
                    } else {
                        cm.leaflet.dragging.disable();
                    }
                });

                // Force re-render so OSM/NOAA spots get/lose click handlers
                _lastRenderedSpotKey = null;
                _renderFromCache();

                // Click-to-add on map
                if (adminEditMode) {
                    map.on('click', _onAdminMapClick);
                    map.getContainer().style.cursor = 'crosshair';
                } else {
                    map.off('click', _onAdminMapClick);
                    map.getContainer().style.cursor = '';
                    _closeAdminModal();
                }
            });
        }

        // ── Map click → add new marker ────────────────────────────────────────
        function _onAdminMapClick(e) {
            _openAdminAddPanel(e.latlng.lat, e.latlng.lng);
        }

        // ── Modal save ────────────────────────────────────────────────────────
        var saveBtn = document.getElementById('fmap-admin-save');
        if (saveBtn) {
            saveBtn.addEventListener('click', function () {
                var markerId  = saveBtn.dataset.markerId;
                var statusEl  = document.getElementById('fmap-admin-status');
                var payload   = {
                    lat:         parseFloat(document.getElementById('fmap-admin-lat').value),
                    lng:         parseFloat(document.getElementById('fmap-admin-lng').value),
                    name:        document.getElementById('fmap-admin-name').value.trim(),
                    type:        document.getElementById('fmap-admin-type').value,
                    description: document.getElementById('fmap-admin-desc').value.trim(),
                };
                var url    = markerId ? '/api/map/custom-markers/' + markerId : '/api/map/custom-markers';
                var method = markerId ? 'PUT' : 'POST';

                if (statusEl) { statusEl.textContent = ''; statusEl.style.color = ''; }
                saveBtn.disabled    = true;
                saveBtn.textContent = 'Saving…';

                fetch(url, {
                    method:  method,
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(payload),
                })
                .then(function (r) {
                    if (!r.ok) throw new Error('Server error ' + r.status);
                    return r.json();
                })
                .then(function () {
                    saveBtn.disabled    = false;
                    saveBtn.textContent = 'Save';
                    _closeAdminModal();
                    spotCache = {}; _spotCacheKeys = [];
                    try { localStorage.removeItem(_SS_KEY); } catch (_e) {}
                    _showAdminToast(markerId ? 'Marker updated' : 'Marker added');
                    scheduleFishingSpotQuery();
                })
                .catch(function (e) {
                    console.error('[admin] save marker failed:', e);
                    saveBtn.disabled    = false;
                    saveBtn.textContent = 'Save';
                    if (statusEl) {
                        statusEl.style.color = '#f87171';
                        statusEl.textContent = 'Save failed — ' + e.message;
                    }
                });
            });
        }

        // ── Modal delete (two-tap confirmation — no blocking confirm dialog) ──
        var delBtn = document.getElementById('fmap-admin-delete');
        if (delBtn) {
            var _delConfirmPending = false;
            var _delConfirmTimer   = null;

            delBtn.addEventListener('click', function () {
                var markerId = delBtn.dataset.markerId;
                if (!markerId) return;

                // First tap: ask for confirmation inline
                if (!_delConfirmPending) {
                    _delConfirmPending = true;
                    delBtn.textContent = 'Confirm delete?';
                    delBtn.style.background = '#991b1b';
                    // Auto-reset after 3 s if they don't confirm
                    _delConfirmTimer = setTimeout(function () {
                        _delConfirmPending = false;
                        delBtn.textContent = 'Delete';
                        delBtn.style.background = '#dc2626';
                    }, 3000);
                    return;
                }

                // Second tap: execute delete
                clearTimeout(_delConfirmTimer);
                _delConfirmPending = false;
                delBtn.textContent = 'Delete';
                delBtn.style.background = '#dc2626';

                var statusEl = document.getElementById('fmap-admin-status');
                if (statusEl) { statusEl.textContent = ''; statusEl.style.color = ''; }
                delBtn.disabled = true;

                fetch('/api/map/custom-markers/' + markerId, { method: 'DELETE' })
                .then(function (r) {
                    if (!r.ok) throw new Error('Server error ' + r.status);
                    return r.json();
                })
                .then(function () {
                    delBtn.disabled = false;
                    _closeAdminModal();
                    spotCache = {}; _spotCacheKeys = [];
                    try { localStorage.removeItem(_SS_KEY); } catch (_e) {}
                    _showAdminToast('Marker deleted');
                    scheduleFishingSpotQuery();
                })
                .catch(function (e) {
                    console.error('[admin] delete marker failed:', e);
                    delBtn.disabled = false;
                    if (statusEl) {
                        statusEl.style.color = '#f87171';
                        statusEl.textContent = 'Delete failed — ' + e.message;
                    }
                });
            });

            // Reset confirmation state when modal closes
            var _origCloseAdminModal = _closeAdminModal;
            _closeAdminModal = function () {
                _delConfirmPending = false;
                clearTimeout(_delConfirmTimer);
                delBtn.textContent = 'Delete';
                delBtn.style.background = '#dc2626';
                _origCloseAdminModal();
            };
        }

        // ── Modal close / backdrop ────────────────────────────────────────────
        var closeBtn = document.getElementById('fmap-admin-modal-close');
        if (closeBtn) closeBtn.addEventListener('click', _closeAdminModal);

        var backdrop = document.getElementById('fmap-admin-backdrop');
        if (backdrop) backdrop.addEventListener('click', _closeAdminModal);
    }

    // ─── SST Stations overlay (ArcGIS Live Feeds / NOAA CoRIS) ──────────────

    function onSstViewport() { scheduleSstQuery(); }

    function wireSstLayer() {
        if (!map) return;
        sstLayer = L.layerGroup();

        var btn = document.getElementById('fmap-sst-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                sstLayerOn = !sstLayerOn;
                btn.classList.toggle('fmap-ctrl-btn--active', sstLayerOn);
                btn.setAttribute('aria-pressed', sstLayerOn ? 'true' : 'false');
                if (sstLayerOn) {
                    sstLayer.addTo(map);
                    scheduleSstQuery();
                    map.on('moveend zoomend', onSstViewport);
                } else {
                    map.removeLayer(sstLayer);
                    sstLayer.clearLayers();
                    map.off('moveend zoomend', onSstViewport);
                }
            });
        }
    }

    function scheduleSstQuery() {
        clearTimeout(sstQueryTimer);
        sstQueryTimer = setTimeout(doFetchSstStations, 700);
    }

    function doFetchSstStations() {
        if (!sstLayerOn || !map) return;
        if (sstAbort) { try { sstAbort.abort(); } catch (e) {} }
        sstAbort = new AbortController();
        var b  = map.getBounds();
        var sw = b.getSouthWest();
        var ne = b.getNorthEast();
        var url = '/api/map/sst-stations?south=' + sw.lat.toFixed(3) +
                  '&west='  + sw.lng.toFixed(3) +
                  '&north=' + ne.lat.toFixed(3) +
                  '&east='  + ne.lng.toFixed(3);

        fetch(url, { signal: sstAbort.signal })
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (data) {
                if (!sstLayerOn || !map) return;
                sstLayer.clearLayers();
                (data.stations || []).forEach(function (s) {
                    var color = _sstColor(s.sst_f);
                    var tempLabel = s.sst_f != null ? Math.round(s.sst_f) + '°' : '?';
                    var icon = _cachedDivIcon('sst|' + color + '|' + tempLabel, {
                        className: '',
                        html: '<div class="fmap-sst-dot" style="background:' + color + '">' +
                              tempLabel + '</div>',
                        iconSize:    [34, 34],
                        iconAnchor:  [17, 17],
                        popupAnchor: [0, -18],
                    });
                    L.marker([s.lat, s.lng], { icon: icon })
                        .bindPopup(_sstPopup(s), { maxWidth: 260 })
                        .addTo(sstLayer);
                });
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return;
                console.warn('[fishing-map] SST stations fetch failed:', err);
            });
    }

    function _sstColor(f) {
        if (f == null) return '#94a3b8';
        if (f >= 86) return '#ef4444';   // very warm ≥30°C
        if (f >= 80) return '#f97316';   // warm 27–30°C
        if (f >= 74) return '#eab308';   // comfortable 23–27°C
        if (f >= 65) return '#22c55e';   // cool 18–23°C
        if (f >= 55) return '#3b82f6';   // cold 13–18°C
        return '#a5b4fc';                // very cold <13°C
    }

    function _sstPopup(s) {
        var temp = s.sst_f != null
            ? s.sst_f + '°F (' + s.sst_c + '°C)'
            : 'N/A';
        var anomaly = s.ssta != null
            ? (s.ssta >= 0 ? '+' : '') + s.ssta + '°C vs. normal'
            : '';
        var dhw = s.dhw ? 'DHW: ' + s.dhw + ' °C-weeks' : '';
        return (
            '<div class="fmap-sst-popup">' +
            '<strong>' + esc(s.name || 'SST Station') + '</strong>' +
            '<br><span class="fmap-sst-temp">' + temp + '</span>' +
            (anomaly ? '<br><small class="fmap-sst-anomaly">' + esc(anomaly) + '</small>' : '') +
            (dhw     ? '<br><small class="fmap-popup-meta">' + esc(dhw) + '</small>' : '') +
            (s.alert > 0
                ? '<br><span class="fmap-sst-alert" style="background:' + s.alert_color + '">' +
                  esc(s.alert_label) + '</span>'
                : '') +
            (s.updated ? '<br><small class="fmap-popup-meta">Updated: ' +
                _sstFmtDate(s.updated) + '</small>' : '') +
            '<br><small class="fmap-popup-source">Source: NOAA CoRIS via ArcGIS Live Feeds</small>' +
            '</div>'
        );
    }

    function _sstFmtDate(iso) {
        try {
            return new Date(iso).toLocaleDateString([], { month:'short', day:'numeric' });
        } catch (e) { return iso; }
    }

    // ─── Wildfire + Smoke overlay (ArcGIS Live Feeds) ─────────────────────────

    // ─── NOAA METAR Surface Observations overlay (ArcGIS Live Feeds) ─────────

    function wireMetarLayer() {
        if (!map) return;
        metarLayer = L.layerGroup();

        var btn = document.getElementById('fmap-metar-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                metarOn = !metarOn;
                btn.classList.toggle('fmap-ctrl-btn--active', metarOn);
                btn.setAttribute('aria-pressed', metarOn ? 'true' : 'false');
                if (metarOn) {
                    metarLayer.addTo(map);
                    doFetchMetar();
                    map.on('moveend zoomend', onMetarViewport);
                } else {
                    map.removeLayer(metarLayer);
                    metarLayer.clearLayers();
                    map.off('moveend zoomend', onMetarViewport);
                }
            });
        }
    }

    function onMetarViewport() {
        clearTimeout(metarTimer);
        metarTimer = setTimeout(doFetchMetar, 700);
    }

    function doFetchMetar() {
        if (!metarOn || !map) return;
        if (map.getZoom() < 5) { metarLayer.clearLayers(); return; }
        if (metarAbort) { try { metarAbort.abort(); } catch (e) {} }
        metarAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/metar?south=' + b.getSouth().toFixed(4) +
                  '&west='  + b.getWest().toFixed(4) +
                  '&north=' + b.getNorth().toFixed(4) +
                  '&east='  + b.getEast().toFixed(4);

        fetch(url, { signal: metarAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!metarOn || !map || !data) return;
                metarLayer.clearLayers();
                var stations = data.stations || [];

                stations.forEach(function (st) {
                    var catColor = st.cat_color || '#9ca3af';
                    var windStr  = st.wind_kt != null
                        ? (st.wind_dir || '?') + ' ' + st.wind_kt + ' kt'
                          + (st.gust_kt ? ' G' + st.gust_kt : '')
                        : 'Calm';
                    var tempStr  = st.temp_f != null ? st.temp_f + '°F' : '–';

                    // Small circle with flight-category color + temp label
                    var icon = _cachedDivIcon('metar|' + catColor + '|' + tempStr, {
                        className: '',
                        html: '<div class="fmap-metar-dot" style="border-color:' + catColor + '">' +
                              '<span class="fmap-metar-temp">' + tempStr + '</span>' +
                              '</div>',
                        iconSize:   [46, 24],
                        iconAnchor: [23, 12],
                    });

                    var timeStr = '';
                    if (st.observed) {
                        try {
                            timeStr = new Date(st.observed).toLocaleTimeString([], {hour:'numeric', minute:'2-digit', timeZoneName:'short'});
                        } catch(e) {}
                    }
                    var visStr = st.visibility_m != null
                        ? (st.visibility_m >= 9000 ? '10+ km' : (st.visibility_m / 1000).toFixed(1) + ' km')
                        : '–';

                    var marker = L.marker([st.lat, st.lng], { icon: icon });
                    marker.bindPopup(
                        '<div class="fmap-metar-popup">' +
                        '<strong>' + (st.icao || st.name) + '</strong>' +
                        (st.name && st.icao ? '<div class="fmap-metar-name">' + st.name + '</div>' : '') +
                        (timeStr ? '<div class="fmap-metar-time">' + timeStr + '</div>' : '') +
                        '<table class="fmap-metar-table">' +
                        '<tr><th scope="row">Temp</th><td>' + tempStr + (st.dew_f != null ? ' · Dew ' + st.dew_f + '°' : '') + '</td></tr>' +
                        '<tr><th scope="row">Wind</th><td>' + windStr + '</td></tr>' +
                        (st.humidity != null ? '<tr><th scope="row">Humidity</th><td>' + st.humidity + '%</td></tr>' : '') +
                        (st.pressure_mb != null ? '<tr><th scope="row">Pressure</th><td>' + st.pressure_mb + ' mb</td></tr>' : '') +
                        '<tr><th scope="row">Visibility</th><td>' + visStr + '</td></tr>' +
                        (st.sky ? '<tr><th scope="row">Sky</th><td>' + st.sky + '</td></tr>' : '') +
                        (st.weather ? '<tr><th scope="row">Wx</th><td>' + st.weather + '</td></tr>' : '') +
                        (st.flight_cat ? '<tr><th scope="row">Flight cat</th><td><span style="color:' + catColor + ';font-weight:700">' + st.flight_cat + '</span></td></tr>' : '') +
                        '</table>' +
                        '<div class="fmap-metar-source">NOAA METAR via ArcGIS Live Feeds</div>' +
                        '</div>',
                        { maxWidth: 260 }
                    );
                    marker.addTo(metarLayer);
                });

                if (stations.length) {
                    showToast(stations.length + ' METAR station' + (stations.length !== 1 ? 's' : ''));
                } else {
                    showToast('No METAR stations in view');
                }
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return;
                console.warn('[fishing-map] METAR fetch failed:', err);
            });
    }


    function wireRecentStorms() {
        if (!map) return;
        recentStormsLayer = L.layerGroup();

        var btn = document.getElementById('fmap-recent-storms-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                recentStormsOn = !recentStormsOn;
                btn.classList.toggle('fmap-ctrl-btn--active', recentStormsOn);
                btn.setAttribute('aria-pressed', recentStormsOn ? 'true' : 'false');
                if (recentStormsOn) {
                    recentStormsLayer.addTo(map);
                    doFetchRecentStorms();
                } else {
                    map.removeLayer(recentStormsLayer);
                    recentStormsLayer.clearLayers();
                }
            });
        }
    }

    function doFetchRecentStorms() {
        if (!recentStormsOn || !map) return;

        fetch('/api/map/recent-storms')
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (data) {
                if (!recentStormsOn || !map) return;
                recentStormsLayer.clearLayers();
                var tracks = data.tracks || [];

                tracks.forEach(function (t) {
                    if (!t.path || t.path.length < 2) return;

                    var line = L.polyline(t.path, {
                        color:   t.color || '#94a3b8',
                        weight:  2,
                        opacity: 0.65,
                        smoothFactor: 1,
                    });

                    // Label marker at the start of each track
                    var firstPt = t.path[0];
                    var nameIcon = L.divIcon({
                        className: '',
                        html: '<span class="fmap-track-label">' + esc(t.name) + '</span>',
                        iconAnchor: [0, 0],
                    });
                    var labelMarker = L.marker(firstPt, {
                        icon:             nameIcon,
                        keyboard:         false,
                        interactive:      true,
                        zIndexOffset:     -200,
                    });

                    var popup = _recentStormPopup(t);
                    line.bindPopup(popup, { maxWidth: 260 });
                    labelMarker.bindPopup(popup, { maxWidth: 260 });

                    line.addTo(recentStormsLayer);
                    labelMarker.addTo(recentStormsLayer);
                });

                // Fit map to all tracks on first load if we're at default zoom
                if (tracks.length > 0 && map.getZoom() <= 5) {
                    var allPts = [];
                    tracks.forEach(function (t) { allPts = allPts.concat(t.path); });
                    if (allPts.length) {
                        try { map.fitBounds(L.latLngBounds(allPts), { padding: [30, 30], maxZoom: 6 }); }
                        catch (e) { /* ignore */ }
                    }
                }

                showToast(tracks.length + ' storm track' + (tracks.length !== 1 ? 's' : '') + ' loaded');
            })
            .catch(function (err) {
                console.warn('[fishing-map] recent storm tracks fetch failed:', err);
                showToast('Storm track data unavailable');
            });
    }

    function _recentStormPopup(t) {
        var dates = '';
        if (t.start_dtg) {
            try {
                var s = new Date(t.start_dtg).toLocaleDateString([], { month:'short', day:'numeric' });
                var e = t.end_dtg ? new Date(t.end_dtg).toLocaleDateString([], { month:'short', day:'numeric' }) : '';
                dates = s + (e ? ' – ' + e : '');
            } catch (ex) { /* ignore */ }
        }
        return (
            '<div class="fmap-storm-popup">' +
            '<strong>' + esc(t.name) + '</strong>' +
            (t.basin ? ' <small class="fmap-popup-meta">(' + esc(t.basin) + ')</small>' : '') +
            '<br><em>' + esc(t.category) + '</em>' +
            (dates ? '<br><small>' + dates + '</small>' : '') +
            '<br><small class="fmap-popup-source">Source: NHC/JTWC via ArcGIS Live Feeds</small>' +
            '</div>'
        );
    }

    // ─── Marine Warnings overlay (ArcGIS Live Feeds) ─────────────────────────

    function onMarineWarnViewport() { scheduleMarineWarnFetch(); }

    function wireMarineWarnings() {
        if (!map) return;
        marineWarnLayer = L.layerGroup();

        var btn = document.getElementById('fmap-marine-warn-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                marineWarnOn = !marineWarnOn;
                btn.classList.toggle('fmap-ctrl-btn--active', marineWarnOn);
                btn.setAttribute('aria-pressed', marineWarnOn ? 'true' : 'false');
                if (marineWarnOn) {
                    marineWarnLayer.addTo(map);
                    scheduleMarineWarnFetch();
                    map.on('moveend zoomend', onMarineWarnViewport);
                } else {
                    map.removeLayer(marineWarnLayer);
                    marineWarnLayer.clearLayers();
                    map.off('moveend zoomend', onMarineWarnViewport);
                }
            });
        }
    }

    function scheduleMarineWarnFetch() {
        clearTimeout(marineWarnTimer);
        marineWarnTimer = setTimeout(doFetchMarineWarnings, 600);
    }

    function doFetchMarineWarnings() {
        if (!marineWarnOn || !map) return;
        if (marineWarnAbort) { try { marineWarnAbort.abort(); } catch (e) {} }
        marineWarnAbort = new AbortController();
        var b  = map.getBounds();
        var sw = b.getSouthWest();
        var ne = b.getNorthEast();
        var url = '/api/map/marine-warnings?south=' + sw.lat.toFixed(4) +
                  '&west='  + sw.lng.toFixed(4) +
                  '&north=' + ne.lat.toFixed(4) +
                  '&east='  + ne.lng.toFixed(4);

        fetch(url, { signal: marineWarnAbort.signal })
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (data) {
                if (!marineWarnOn || !map) return;
                marineWarnLayer.clearLayers();
                (data.warnings || []).forEach(function (w) {
                    if (!w.rings || !w.rings.length) return;
                    var fillColor = w.color || '#60a5fa';
                    w.rings.forEach(function (ring) {
                        if (!ring.length) return;
                        L.polygon(ring, {
                            color:       fillColor,
                            fillColor:   fillColor,
                            fillOpacity: 0.18,
                            weight:      1.5,
                            opacity:     0.7,
                        })
                        .bindPopup(_marineWarnPopup(w), { maxWidth: 300 })
                        .addTo(marineWarnLayer);
                    });
                });
                _updateMarineWarnBadge(data.count || 0);
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return;
                console.warn('[fishing-map] marine warnings fetch failed:', err);
            });
    }

    function _marineWarnPopup(w) {
        var expires = '';
        if (w.expires) {
            try {
                expires = '<br><span class="fmap-warn-expires">Expires ' +
                    new Date(w.expires).toLocaleString([], { month:'short', day:'numeric',
                        hour:'numeric', minute:'2-digit' }) + '</span>';
            } catch (e) { /* ignore */ }
        }
        var marineTag = w.marine
            ? '<span class="fmap-warn-marine-tag">Marine</span> '
            : '';
        var desc = (w.description || w.summary || '').slice(0, 400);
        var instr = w.instruction ? '<p class="fmap-warn-instruction">' + esc(w.instruction.slice(0,300)) + '</p>' : '';
        return (
            '<div class="fmap-warn-popup">' +
            '<strong>' + marineTag + esc(w.event) + '</strong>' +
            '<br><em>' + esc(w.severity) + '</em>' +
            expires +
            (w.affected ? '<br><small>' + esc(w.affected.slice(0, 120)) + '</small>' : '') +
            (desc ? '<p class="fmap-warn-desc">' + esc(desc) + '</p>' : '') +
            instr +
            '</div>'
        );
    }

    function _updateMarineWarnBadge(count) {
        var badge = document.getElementById('fmap-marine-warn-badge');
        if (!badge) return;
        badge.textContent = count;
        badge.style.display = count > 0 ? '' : 'none';
    }

    // ─── Storm Tracker overlay (ArcGIS Live Feeds) ────────────────────────────

    function wireStormTracker() {
        if (!map) return;
        stormTrackerLayer = L.layerGroup();

        var btn = document.getElementById('fmap-storm-tracker-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                stormTrackerOn = !stormTrackerOn;
                btn.classList.toggle('fmap-ctrl-btn--active', stormTrackerOn);
                btn.setAttribute('aria-pressed', stormTrackerOn ? 'true' : 'false');
                if (stormTrackerOn) {
                    stormTrackerLayer.addTo(map);
                    doFetchActiveStorms();
                } else {
                    map.removeLayer(stormTrackerLayer);
                    stormTrackerLayer.clearLayers();
                    _updateStormBadge(0);
                }
            });
        }
    }

    function doFetchActiveStorms() {
        if (!stormTrackerOn || !map) return;

        fetch('/api/map/active-storms')
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (data) {
                if (!stormTrackerOn || !map) return;
                stormTrackerLayer.clearLayers();
                var storms = data.storms || [];

                storms.forEach(function (storm) {
                    // Uncertainty cone (filled, semi-transparent)
                    if (storm.cone && storm.cone.length) {
                        storm.cone.forEach(function (ring) {
                            L.polygon(ring, {
                                color:       '#f97316',
                                fillColor:   '#f97316',
                                fillOpacity: 0.12,
                                weight:      1,
                                dashArray:   '4 4',
                            }).addTo(stormTrackerLayer);
                        });
                    }

                    // Forecast track line
                    if (storm.track && storm.track.length > 1) {
                        L.polyline(storm.track, {
                            color:  '#f97316',
                            weight: 2.5,
                            opacity: 0.85,
                            dashArray: '6 4',
                        }).addTo(stormTrackerLayer);
                    }

                    // Storm position marker
                    if (storm.lat && storm.lng) {
                        var color = _stormColor(storm.category);
                        var icon = L.divIcon({
                            className: '',
                            html: '<div class="fmap-storm-dot" style="background:' + color + '" ' +
                                  'title="' + esc(storm.name) + '"></div>',
                            iconSize:    [22, 22],
                            iconAnchor:  [11, 11],
                            popupAnchor: [0, -13],
                        });
                        L.marker([storm.lat, storm.lng], { icon: icon })
                            .bindPopup(_stormPopup(storm), { maxWidth: 280 })
                            .addTo(stormTrackerLayer);
                    }
                });

                _updateStormBadge(storms.length);

                if (storms.length && map) {
                    showToast(storms.length + ' active storm' +
                        (storms.length > 1 ? 's' : '') + ' tracked');
                } else if (storms.length === 0) {
                    showToast('No active tropical storms at this time');
                }
            })
            .catch(function (err) {
                console.warn('[fishing-map] storm tracker fetch failed:', err);
                showToast('Storm data unavailable');
            });
    }

    function _stormColor(category) {
        if (!category) return '#94a3b8';
        var c = category.toLowerCase();
        if (c.indexOf('5') !== -1) return '#7c3aed';
        if (c.indexOf('4') !== -1) return '#dc2626';
        if (c.indexOf('3') !== -1) return '#ef4444';
        if (c.indexOf('2') !== -1) return '#f97316';
        if (c.indexOf('1') !== -1) return '#f59e0b';
        if (c.indexOf('tropical storm') !== -1) return '#3b82f6';
        return '#94a3b8';  // depression / unknown
    }

    function _stormPopup(s) {
        var wind   = s.wind_mph ? s.wind_mph + ' mph max winds' : '';
        var press  = s.pressure_mb ? s.pressure_mb + ' mb' : '';
        var detail = [wind, press].filter(Boolean).join(' &bull; ');
        return (
            '<div class="fmap-storm-popup">' +
            '<strong>' + esc(s.name) + '</strong>' +
            '<br><em>' + esc(s.category) + '</em>' +
            (detail ? '<br><small>' + detail + '</small>' : '') +
            '<br><small class="fmap-popup-source">Source: NHC/JTWC via ArcGIS Live Feeds</small>' +
            '</div>'
        );
    }

    function _updateStormBadge(count) {
        var badge = document.getElementById('fmap-storm-badge');
        if (!badge) return;
        badge.textContent = count;
        badge.style.display = count > 0 ? '' : 'none';
    }

    // ─── AQI / PM2.5 overlay (ArcGIS Live Feeds) ──────────────────────────────

    function wireBuoyLayer() {
        if (!map) return;
        buoyLayer = L.layerGroup();

        var btn = document.getElementById('fmap-buoy-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                buoyOn = !buoyOn;
                btn.classList.toggle('fmap-ctrl-btn--active', buoyOn);
                btn.setAttribute('aria-pressed', buoyOn ? 'true' : 'false');
                if (buoyOn) {
                    buoyLayer.addTo(map);
                    doFetchBuoys();
                    map.on('moveend zoomend', onBuoyViewport);
                } else {
                    map.removeLayer(buoyLayer);
                    buoyLayer.clearLayers();
                    map.off('moveend zoomend', onBuoyViewport);
                }
            });
        }
    }

    function doFetchBuoys() {
        if (!buoyOn || !map) return;
        if (buoyAbort) { try { buoyAbort.abort(); } catch (e) {} }
        buoyAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/buoys?south=' + b.getSouth().toFixed(3) +
                  '&west='  + b.getWest().toFixed(3) +
                  '&north=' + b.getNorth().toFixed(3) +
                  '&east='  + b.getEast().toFixed(3);

        fetch(url, { signal: buoyAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!buoyOn || !map || !data) return;
                buoyLayer.clearLayers();
                (data.buoys || []).forEach(function (b) {
                    var wt  = b.water_temp_f != null ? b.water_temp_f + '°F' : '–';
                    var wh  = b.wave_ht_ft   != null ? b.wave_ht_ft   + ' ft' : '–';
                    var ws  = b.wind_kt      != null ? b.wind_kt      + ' kt' : '–';
                    var pr  = b.period_s     != null ? b.period_s     + ' s' : '–';
                    var clr      = _sstColor(b.water_temp_f);
                    var waveLabel = b.wave_ht_ft != null ? b.wave_ht_ft : '·';
                    var icon = _cachedDivIcon('buoy|' + clr + '|' + waveLabel, {
                        className: '',
                        html: '<div class="fmap-buoy-dot" style="border-color:' + clr + '">' +
                              '<span class="fmap-buoy-wave">' + waveLabel + '</span>' +
                              '</div>',
                        iconSize: [38, 22], iconAnchor: [19, 11],
                    });
                    var updStr = '';
                    if (b.updated) { try { updStr = new Date(b.updated).toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZoneName:'short'}); } catch(e) {} }
                    L.marker([b.lat, b.lng], { icon: icon })
                     .bindPopup('<div class="fmap-buoy-popup">' +
                        '<strong>' + esc(b.id ? b.id + (b.name ? ' – ' + b.name : '') : b.name || 'NDBC Buoy') + '</strong>' +
                        (updStr ? '<div class="fmap-popup-meta">' + updStr + '</div>' : '') +
                        '<table class="fmap-gauge-table">' +
                        '<tr><th scope="row">Water Temp</th><td><span style="color:' + clr + '">' + wt + '</span></td></tr>' +
                        '<tr><th scope="row">Wave Height</th><td>' + wh + '</td></tr>' +
                        '<tr><th scope="row">Wave Period</th><td>' + pr + '</td></tr>' +
                        '<tr><th scope="row">Wind</th><td>' + ws + (b.wind_dir != null ? ' @ ' + b.wind_dir + '°' : '') + '</td></tr>' +
                        (b.pressure_mb != null ? '<tr><th scope="row">Pressure</th><td>' + b.pressure_mb + ' mb</td></tr>' : '') +
                        '</table>' +
                        '<div class="fmap-popup-source">NDBC via ArcGIS Live Feeds</div>' +
                        '</div>', { maxWidth: 260 })
                     .addTo(buoyLayer);
                });
                if (!data.buoys || !data.buoys.length) showToast('No buoy data in view');
            })
            .catch(function (err) { if (err && err.name !== 'AbortError') console.warn('[fishing-map] buoy fetch failed:', err); });
    }

    // ─── HF Radar Ocean Currents overlay (NOAA HFRNet via ArcGIS) ────────────

    var hfradarOn     = false;
    var hfradarLayer  = null;
    var hfradarTimer  = null;
    var hfradarAbort  = null;

    function onHfradarViewport() { clearTimeout(hfradarTimer); hfradarTimer = setTimeout(doFetchHfradar, 700); }

    function wireHfradarLayer() {
        if (!map) return;
        hfradarLayer = L.layerGroup();

        var btn = document.getElementById('fmap-hfradar-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                hfradarOn = !hfradarOn;
                btn.classList.toggle('fmap-ctrl-btn--active', hfradarOn);
                btn.setAttribute('aria-pressed', hfradarOn ? 'true' : 'false');
                if (hfradarOn) {
                    hfradarLayer.addTo(map);
                    doFetchHfradar();
                    map.on('moveend zoomend', onHfradarViewport);
                } else {
                    map.removeLayer(hfradarLayer);
                    hfradarLayer.clearLayers();
                    map.off('moveend zoomend', onHfradarViewport);
                }
            });
        }
    }

    function doFetchHfradar() {
        if (!hfradarOn || !map) return;
        if (map.getZoom() < 5) { hfradarLayer.clearLayers(); return; }
        if (hfradarAbort) { try { hfradarAbort.abort(); } catch (e) {} }
        hfradarAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/hfradar?south=' + b.getSouth().toFixed(3) +
                  '&west='  + b.getWest().toFixed(3) +
                  '&north=' + b.getNorth().toFixed(3) +
                  '&east='  + b.getEast().toFixed(3);

        fetch(url, { signal: hfradarAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!hfradarOn || !map || !data) return;
                hfradarLayer.clearLayers();
                var vectors = data.vectors || [];

                // Subsample if too dense (>600 pts) to keep FPS up
                if (vectors.length > 600) {
                    var step = Math.ceil(vectors.length / 600);
                    vectors = vectors.filter(function (_, i) { return i % step === 0; });
                }

                vectors.forEach(function (v) {
                    // Draw an arrow rotated to the current direction
                    var rot = v.dir_deg != null ? v.dir_deg : 0;
                    var kts = v.speed_kts != null ? v.speed_kts.toFixed(1) + ' kt' : '';
                    var icon = L.divIcon({
                        className: '',
                        html: '<div class="fmap-hfradar-arrow" style="color:' + v.color +
                              ';transform:rotate(' + rot + 'deg)" title="' + kts + '">' +
                              '<svg viewBox="0 0 10 16" width="10" height="16" fill="currentColor" aria-hidden="true">' +
                              '<polygon points="5,0 10,16 5,12 0,16"/></svg></div>',
                        iconSize:   [10, 16],
                        iconAnchor: [5, 8],
                    });
                    var updStr = '';
                    if (v.updated) { try { updStr = new Date(v.updated).toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZoneName:'short'}); } catch(e) {} }
                    L.marker([v.lat, v.lng], { icon: icon })
                     .bindTooltip(
                        '<strong>Ocean Current</strong>' +
                        (kts ? '<br>' + kts + (v.dir_deg != null ? ' from ' + v.dir_deg + '°' : '') : '') +
                        (updStr ? '<br><small class="fmap-tooltip-sub">' + updStr + '</small>' : '') +
                        '<br><small class="fmap-tooltip-sub">NOAA HF Radar</small>',
                        { sticky: true, opacity: 0.93, direction: 'top' }
                     )
                     .addTo(hfradarLayer);
                });
                if (!vectors.length) showToast('No HF Radar data in view (US coasts only)');
                else showToast(vectors.length + ' current vector' + (vectors.length !== 1 ? 's' : ''));
            })
            .catch(function (err) { if (err && err.name !== 'AbortError') console.warn('[fishing-map] HF Radar fetch failed:', err); });
    }

    // ─── NHC Tropical Weather Outlook overlay (ArcGIS Live Feeds) ────────────

    var tropicalOn     = false;
    var tropicalLayer  = null;
    var tropicalAbort  = null;

    function wireTropicalOutlook() {
        if (!map) return;
        tropicalLayer = L.layerGroup();

        var btn = document.getElementById('fmap-tropical-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                tropicalOn = !tropicalOn;
                btn.classList.toggle('fmap-ctrl-btn--active', tropicalOn);
                btn.setAttribute('aria-pressed', tropicalOn ? 'true' : 'false');
                if (tropicalOn) { tropicalLayer.addTo(map); doFetchTropicalOutlook(); }
                else { map.removeLayer(tropicalLayer); tropicalLayer.clearLayers(); }
            });
        }
    }

    function doFetchTropicalOutlook() {
        if (!tropicalOn || !map) return;
        if (tropicalAbort) { try { tropicalAbort.abort(); } catch (e) {} }
        tropicalAbort = new AbortController();

        fetch('/api/map/tropical-outlook', { signal: tropicalAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!tropicalOn || !map || !data) return;
                tropicalLayer.clearLayers();
                var areas = data.areas || [];

                areas.forEach(function (a) {
                    if (!a.rings || !a.rings.length) return;
                    a.rings.forEach(function (ring) {
                        L.polygon(ring, {
                            color:       a.color,
                            fillColor:   a.color,
                            fillOpacity: _getLayerOpacity('fmap-tropical-btn') / 100,
                            weight:      2,
                            opacity:     0.75,
                            dashArray:   '6 4',
                        }).bindPopup(
                            '<div class="fmap-tropical-popup">' +
                            '<strong>Tropical Development Area</strong>' +
                            '<div class="fmap-tropical-prob" style="color:' + a.color + '">' +
                            esc(a.prob_label || a.probability) + ' probability</div>' +
                            '<div class="fmap-popup-meta">Basin: ' + esc(a.basin) + '</div>' +
                            (a.discussion ? '<p class="fmap-tropical-discussion">' + esc(a.discussion) + '</p>' : '') +
                            '<div class="fmap-popup-source">NHC Tropical Weather Outlook · ArcGIS Live Feeds</div>' +
                            '</div>',
                            { maxWidth: 280 }
                        ).addTo(tropicalLayer);
                    });
                });

                if (!areas.length) showToast('No tropical development areas active');
                else showToast(areas.length + ' tropical area' + (areas.length !== 1 ? 's' : '') + ' in outlook');
            })
            .catch(function (err) { if (err && err.name !== 'AbortError') console.warn('[fishing-map] tropical outlook fetch failed:', err); });
    }

    // ─── Layers popup panel ───────────────────────────────────────────────────
    // Only fishing-relevant layers remain.  Non-fishing layers (wildfire, drought,
    // AQI, sea ice, seismic, terminator, stream gauges, storm reports, NDFD
    // precipitation/temperature) have been removed from the map UI.
    var LAYER_BTN_IDS = [
        'fmap-marine-warn-btn', 'fmap-storm-tracker-btn', 'fmap-recent-storms-btn',
        'fmap-tropical-btn',
        'fmap-sst-btn', 'fmap-buoy-btn', 'fmap-hfradar-btn', 'fmap-metar-btn'
    ];
    var LS_LAYERS_KEY   = 'fmap_layers_v4';   // bumped — clears old non-fishing state
    var LS_SECTIONS_KEY = 'fmap_sections_v2';

    // Map from section data-section value → layer button IDs it contains
    var SECTION_LAYER_MAP = {
        weather: ['fmap-marine-warn-btn', 'fmap-storm-tracker-btn',
                  'fmap-recent-storms-btn', 'fmap-tropical-btn'],
        ocean:   ['fmap-sst-btn', 'fmap-buoy-btn', 'fmap-hfradar-btn', 'fmap-metar-btn']
    };

    function _saveLayerState() {
        try {
            var active = LAYER_BTN_IDS.filter(function (id) {
                var b = document.getElementById(id);
                return b && b.getAttribute('aria-pressed') === 'true';
            });
            localStorage.setItem(LS_LAYERS_KEY, JSON.stringify(active));
        } catch (e) { /* storage unavailable */ }
    }

    // ─── Per-layer opacity controls ───────────────────────────────────────────
    var _OPACITY_DEFAULTS = {
        'fmap-tropical-btn':  20,
    };
    var _layerOpacities = {};
    try {
        var _opRaw = localStorage.getItem('fmap_opacities_v1');
        if (_opRaw) _layerOpacities = JSON.parse(_opRaw) || {};
    } catch(e) {}

    function _getPolygonLayers() {
        return {
            'fmap-tropical-btn': tropicalLayer,
        };
    }

    function _getLayerOpacity(btnId) {
        return (_layerOpacities[btnId] !== undefined)
            ? _layerOpacities[btnId]
            : (_OPACITY_DEFAULTS[btnId] || 35);
    }

    function _saveLayerOpacity(btnId, pct) {
        _layerOpacities[btnId] = pct;
        try { localStorage.setItem('fmap_opacities_v1', JSON.stringify(_layerOpacities)); } catch(e) {}
    }

    function _applyLayerOpacity(btnId, pct) {
        var layers = _getPolygonLayers();
        var lg = layers[btnId];
        if (!lg) return;
        var frac = pct / 100;
        lg.eachLayer(function(layer) {
            if (layer.setStyle) layer.setStyle({ fillOpacity: frac });
        });
    }

    function _showOpacityRow(btnId) {
        if (!_OPACITY_DEFAULTS.hasOwnProperty(btnId)) return;
        if (document.getElementById('fmap-opacity-' + btnId)) return;  // already shown
        var btn = document.getElementById(btnId);
        if (!btn) return;
        var pct = _getLayerOpacity(btnId);
        var row = document.createElement('div');
        row.id        = 'fmap-opacity-' + btnId;
        row.className = 'fmap-layer-opacity-row';
        row.innerHTML =
            '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" class="fmap-opacity-icon" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="3"/><line x1="12" y1="2" x2="12" y2="5"/>' +
            '<line x1="12" y1="19" x2="12" y2="22"/><line x1="4.22" y1="4.22" x2="6.34" y2="6.34"/>' +
            '<line x1="17.66" y1="17.66" x2="19.78" y2="19.78"/></svg>' +
            '<input type="range" class="fmap-opacity-slider" min="5" max="80" step="5" value="' + pct + '" aria-label="Layer opacity">' +
            '<span class="fmap-opacity-val">' + pct + '%</span>';
        btn.insertAdjacentElement('afterend', row);
        row.addEventListener('click', function(e) { e.stopPropagation(); });
        var slider = row.querySelector('.fmap-opacity-slider');
        var valEl  = row.querySelector('.fmap-opacity-val');
        slider.addEventListener('input', function() {
            var v = parseInt(this.value, 10);
            valEl.textContent = v + '%';
            _applyLayerOpacity(btnId, v);
            _saveLayerOpacity(btnId, v);
        });
    }

    function _hideOpacityRow(btnId) {
        var el = document.getElementById('fmap-opacity-' + btnId);
        if (el && el.parentNode) el.parentNode.removeChild(el);
    }

    // ─── Map legend (auto-shows for color-coded layers) ───────────────────────
    var legendControl   = null;
    var legendCollapsed = false;

    var _LEGEND_SPECS = {
        hfradar: {
            label: 'Currents (cm/s)',
            items: [
                { color: '#60a5fa', text: '<10',   dark: true },
                { color: '#22c55e', text: '10–25', dark: true },
                { color: '#eab308', text: '25–50' },
                { color: '#f97316', text: '50–100',dark: true },
                { color: '#ef4444', text: '>100',  dark: true },
            ]
        },
        tropical: {
            label: 'Tropical Dev.',
            items: [
                { color: '#eab308', text: 'Low' },
                { color: '#f97316', text: 'Medium', dark: true },
                { color: '#ef4444', text: 'High',   dark: true },
            ]
        },
        metar: {
            label: 'METAR (flight cat.)',
            items: [
                { color: '#22c55e', text: 'VFR — clear',         dark: true },
                { color: '#60a5fa', text: 'MVFR — marginal',     dark: true },
                { color: '#f87171', text: 'IFR — low vis',       dark: true },
                { color: '#c084fc', text: 'LIFR — fog/obscured', dark: true },
            ]
        },
    };

    // Map from layer key → the boolean var that tracks "is this layer on?"
    function _legendLayerOn(key) {
        switch (key) {
            case 'hfradar':   return hfradarOn;
            case 'tropical':  return tropicalOn;
            case 'metar':     return metarOn;
            default:          return false;
        }
    }

    function _initLegend() {
        if (!map || legendControl) return;
        var LegendCtrl = L.Control.extend({
            options: { position: 'bottomleft' },
            onAdd: function () {
                var div = L.DomUtil.create('div', 'fmap-legend-ctrl');
                div.hidden = true;
                L.DomEvent.disableClickPropagation(div);
                return div;
            }
        });
        legendControl = new LegendCtrl();
        legendControl.addTo(map);
    }

    function _updateLegend() {
        if (!legendControl) return;
        var container = legendControl.getContainer();
        if (!container) return;

        var activeSpecs = Object.keys(_LEGEND_SPECS).filter(_legendLayerOn);
        if (!activeSpecs.length || legendCollapsed) {
            if (!activeSpecs.length) {
                container.hidden = true;
                legendCollapsed  = false;
            } else if (legendCollapsed) {
                container.hidden = false;
                container.innerHTML =
                    '<div class="fmap-legend-collapsed">' +
                    '<button class="fmap-legend-expand-btn" title="Show layer legend">' +
                    '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">' +
                    '<polyline points="6 9 12 15 18 9"/></svg> Legend</button></div>';
                container.querySelector('.fmap-legend-expand-btn').addEventListener('click', function () {
                    legendCollapsed = false;
                    _updateLegend();
                });
            }
            return;
        }

        container.hidden = false;
        var html = '<div class="fmap-legend-inner">' +
            '<div class="fmap-legend-header">' +
            '<span class="fmap-legend-title-main">Legend</span>' +
            '<button class="fmap-legend-collapse-btn" title="Collapse legend">' +
            '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">' +
            '<polyline points="18 15 12 9 6 15"/></svg></button></div>';

        activeSpecs.forEach(function (key) {
            var spec = _LEGEND_SPECS[key];
            html += '<div class="fmap-legend-section">' +
                    '<div class="fmap-legend-section-title">' + spec.label + '</div>' +
                    '<div class="fmap-legend-items">';
            spec.items.forEach(function (item) {
                var txtColor = item.dark ? '#fff' : '#111';
                html += '<span class="fmap-legend-item">' +
                        '<span class="fmap-legend-swatch" style="background:' + item.color +
                        ';color:' + txtColor + '"></span>' +
                        '<span class="fmap-legend-label">' + item.text + '</span></span>';
            });
            html += '</div></div>';
        });

        html += '</div>';
        container.innerHTML = html;

        // Wire collapse button
        var colBtn = container.querySelector('.fmap-legend-collapse-btn');
        if (colBtn) {
            colBtn.addEventListener('click', function () {
                legendCollapsed = true;
                _updateLegend();
            });
        }
    }

    function _updateLayersBadge() {
        var badge    = document.getElementById('fmap-layers-active-badge');
        var clearBtn = document.getElementById('fmap-layers-clear-btn');
        var total = 0;
        LAYER_BTN_IDS.forEach(function (id) {
            var b = document.getElementById(id);
            if (b && b.getAttribute('aria-pressed') === 'true') total++;
        });
        if (badge) {
            badge.textContent = total;
            badge.style.display = total > 0 ? '' : 'none';
        }
        if (clearBtn) clearBtn.hidden = total === 0;

        // Update per-section active count badges
        Object.keys(SECTION_LAYER_MAP).forEach(function (sec) {
            var countEl = document.getElementById('fmap-sec-count-' + sec);
            if (!countEl) return;
            var n = SECTION_LAYER_MAP[sec].filter(function (id) {
                var b = document.getElementById(id);
                return b && b.getAttribute('aria-pressed') === 'true';
            }).length;
            countEl.textContent = n + ' on';
            countEl.style.display = n > 0 ? '' : 'none';
        });
    }

    function wireLayersPopup() {
        var triggerBtn  = document.getElementById('fmap-layers-popup-btn');
        var popup       = document.getElementById('fmap-layers-popup');
        var closeBtn    = document.getElementById('fmap-layers-popup-close');
        var clearBtn    = document.getElementById('fmap-layers-clear-btn');
        var pinBtn      = document.getElementById('fmap-layers-pin-btn');
        var searchInput = document.getElementById('fmap-layers-search');
        if (!triggerBtn || !popup) return;

        var _closeTimer = null;
        var _pinned     = false;   // when true, outside-click and Escape don't close

        // ── Pin toggle (keep panel open while switching layers) ───────────────
        function _updatePinAppearance() {
            if (!pinBtn) return;
            pinBtn.classList.toggle('fmap-layers-pin-btn--active', _pinned);
            pinBtn.setAttribute('aria-pressed', _pinned ? 'true' : 'false');
            pinBtn.title = _pinned ? 'Unpin panel (allow auto-close)' : 'Pin panel open';
        }

        if (pinBtn) {
            pinBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                _pinned = !_pinned;
                _updatePinAppearance();
            });
        }

        // ── Layer search filter ───────────────────────────────────────────────
        // Filters visible layer rows by the text inside .fmap-layer-row-name
        function _applyLayerSearch(q) {
            var lq = q.toLowerCase().trim();
            var allRows  = popup.querySelectorAll('.fmap-layer-row');
            var sections = popup.querySelectorAll('.fmap-layers-section');
            var anyVisible = false;
            allRows.forEach(function (row) {
                var nameEl = row.querySelector('.fmap-layer-row-name');
                var descEl = row.querySelector('.fmap-layer-row-desc');
                var text = ((nameEl ? nameEl.textContent : '') + ' ' + (descEl ? descEl.textContent : '')).toLowerCase();
                var show = !lq || text.indexOf(lq) !== -1;
                row.style.display = show ? '' : 'none';
                if (show) anyVisible = true;
            });
            // Hide section headers when all their rows are hidden; show otherwise.
            // Sections with no .fmap-layer-row elements (e.g. Spot Filters) are always shown.
            sections.forEach(function (sec) {
                var allSectionRows = sec.querySelectorAll('.fmap-layer-row');
                if (!allSectionRows.length) { sec.style.display = lq ? 'none' : ''; return; }
                var visibleRows = Array.prototype.filter.call(allSectionRows, function (r) {
                    return r.style.display !== 'none';
                });
                sec.style.display = visibleRows.length ? '' : 'none';
                // When searching, expand collapsed sections so matches are visible
                if (lq && visibleRows.length) {
                    sec.classList.remove('fmap-layers-section--collapsed');
                    var hdr = sec.querySelector('.fmap-layers-section-hdr');
                    if (hdr) hdr.setAttribute('aria-expanded', 'true');
                }
            });
            // Show/hide no-results state
            var noResultsEl = popup.querySelector('.fmap-layers-no-results');
            if (!noResultsEl && lq && !anyVisible) {
                noResultsEl = document.createElement('p');
                noResultsEl.className = 'fmap-layers-no-results';
                var body = popup.querySelector('.fmap-layers-popup-body');
                if (body) body.appendChild(noResultsEl);
            }
            if (noResultsEl) {
                noResultsEl.textContent = lq && !anyVisible ? 'No layers match "' + q.trim() + '"' : '';
                noResultsEl.style.display = lq && !anyVisible ? '' : 'none';
            }
        }

        if (searchInput) {
            searchInput.addEventListener('input', function () { _applyLayerSearch(searchInput.value); });
            searchInput.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && searchInput.value) {
                    // Clear search text first; stop propagation so the
                    // document-level handler doesn't also close the popup.
                    e.stopPropagation();
                    searchInput.value = '';
                    _applyLayerSearch('');
                }
            });
        }

        function openPopup() {
            clearTimeout(_closeTimer);
            popup.classList.remove('fmap-layers-popup--closing');
            popup.hidden = false;
            triggerBtn.classList.add('fmap-ctrl-btn--active');
            triggerBtn.setAttribute('aria-pressed', 'true');
            // Auto-focus the search box for quick keyboard filtering
            if (searchInput) { setTimeout(function () { searchInput.focus(); }, 60); }
            else {
                var firstRow = popup.querySelector('.fmap-layer-row, .fmap-layers-section-hdr');
                if (firstRow) firstRow.focus();
            }
        }

        function closePopup() {
            popup.classList.add('fmap-layers-popup--closing');
            _closeTimer = setTimeout(function () {
                popup.hidden = true;
                popup.classList.remove('fmap-layers-popup--closing');
                // Clear search so it resets for next open
                if (searchInput) { searchInput.value = ''; _applyLayerSearch(''); }
            }, 160);
            triggerBtn.classList.remove('fmap-ctrl-btn--active');
            triggerBtn.setAttribute('aria-pressed', 'false');
            triggerBtn.focus();
        }

        triggerBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (popup.hidden || popup.classList.contains('fmap-layers-popup--closing')) {
                openPopup();
            } else {
                closePopup();
            }
        });

        if (closeBtn) {
            closeBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                closePopup();
            });
        }

        // "Clear all" turns off every active layer
        if (clearBtn) {
            clearBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                LAYER_BTN_IDS.forEach(function (id) {
                    var b = document.getElementById(id);
                    if (b && b.getAttribute('aria-pressed') === 'true') b.click();
                });
            });
        }

        // Escape closes popup (unless pinned)
        // 'L' key (no modifiers, not in a text field) toggles panel
        document.addEventListener('keydown', function (e) {
            var tag = (e.target || {}).tagName || '';
            var inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' ||
                          (e.target || {}).isContentEditable;

            if (!popup.hidden && (e.key === 'Escape' || e.keyCode === 27) && !_pinned) {
                closePopup();
                return;
            }
            if (!inInput && (e.key === 'l' || e.key === 'L') && !e.ctrlKey && !e.metaKey && !e.altKey) {
                e.preventDefault();
                if (popup.hidden || popup.classList.contains('fmap-layers-popup--closing')) {
                    openPopup();
                } else if (!_pinned) {
                    closePopup();
                }
            }
        });

        // Close when clicking outside (unless pinned)
        document.addEventListener('click', function (e) {
            if (popup.hidden || _pinned) return;
            if (popup.contains(e.target) || triggerBtn.contains(e.target)) return;
            closePopup();
        });

        // ── Collapsible section headers ─────────────────────────────────────
        var collapsedSections = [];
        try {
            var raw = localStorage.getItem(LS_SECTIONS_KEY);
            if (raw) collapsedSections = JSON.parse(raw) || [];
        } catch (e) { /* ignore */ }

        popup.querySelectorAll('.fmap-layers-section').forEach(function (section) {
            var sec   = section.getAttribute('data-section');
            var hdr   = section.querySelector('.fmap-layers-section-hdr');
            if (!hdr) return;

            // Restore collapsed state from previous session
            if (collapsedSections.indexOf(sec) !== -1) {
                section.classList.add('fmap-layers-section--collapsed');
                hdr.setAttribute('aria-expanded', 'false');
            }

            hdr.addEventListener('click', function (e) {
                e.stopPropagation();
                var isCollapsed = section.classList.toggle('fmap-layers-section--collapsed');
                hdr.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');

                // Persist collapsed state
                try {
                    if (isCollapsed) {
                        if (collapsedSections.indexOf(sec) === -1) collapsedSections.push(sec);
                    } else {
                        collapsedSections = collapsedSections.filter(function (s) { return s !== sec; });
                    }
                    localStorage.setItem(LS_SECTIONS_KEY, JSON.stringify(collapsedSections));
                } catch (e) { /* ignore */ }
            });
        });

        // ── Quick preset pills — toggle all layers in a section ──────────────
        document.querySelectorAll('.fmap-preset-pill').forEach(function (pill) {
            pill.addEventListener('click', function (e) {
                e.stopPropagation();
                var sec = pill.getAttribute('data-preset');
                var ids = SECTION_LAYER_MAP[sec];
                if (!ids || !ids.length) return;

                // Determine: are all layers in this section already on?
                var allOn = ids.every(function (id) {
                    var b = document.getElementById(id);
                    return b && b.getAttribute('aria-pressed') === 'true';
                });

                // If all on → turn all off. Otherwise → turn all on.
                ids.forEach(function (id, idx) {
                    setTimeout(function () {
                        var b = document.getElementById(id);
                        if (!b) return;
                        if (allOn && b.getAttribute('aria-pressed') === 'true') b.click();
                        if (!allOn && b.getAttribute('aria-pressed') !== 'true') b.click();
                    }, idx * 80);
                });

                // Mark pill as active if turning on, inactive if turning off
                pill.classList.toggle('fmap-preset-pill--active', !allOn);
                pill.setAttribute('aria-pressed', !allOn ? 'true' : 'false');
            });
        });

        // Keep preset pill active state in sync with layer state
        function _syncPresetPills() {
            document.querySelectorAll('.fmap-preset-pill').forEach(function (pill) {
                var sec = pill.getAttribute('data-preset');
                var ids = SECTION_LAYER_MAP[sec] || [];
                var anyOn = ids.some(function (id) {
                    var b = document.getElementById(id);
                    return b && b.getAttribute('aria-pressed') === 'true';
                });
                pill.classList.toggle('fmap-preset-pill--active', anyOn);
                pill.setAttribute('aria-pressed', anyOn ? 'true' : 'false');
            });
        }

        // ── Per-row: loading shimmer, badge refresh, state persistence ───────
        LAYER_BTN_IDS.forEach(function (id) {
            var btn = document.getElementById(id);
            if (!btn) return;
            btn.addEventListener('click', function () {
                // Run after the wire*Layer handler has flipped aria-pressed
                setTimeout(function () {
                    _updateLayersBadge();
                    _saveLayerState();
                    _updateLegend();
                    _syncPresetPills();
                    var isOn = btn.getAttribute('aria-pressed') === 'true';
                    if (isOn) _showOpacityRow(id);
                    else _hideOpacityRow(id);
                }, 0);

                // Loading shimmer on toggle track while data fetches (turning ON only)
                if (btn.getAttribute('aria-pressed') !== 'true') {
                    btn.classList.add('fmap-layer-row--loading');
                    setTimeout(function () {
                        btn.classList.remove('fmap-layer-row--loading');
                    }, 2200);
                }
            });
        });
    }

    // Restore which layers were active in the previous session.
    // Must be called AFTER all wire*Layer() functions have attached their handlers.
    //
    // Layers are staggered 350 ms apart (starting 700 ms after boot) so that
    // tile loads get network priority first.
    function restoreLayerState() {
        try {
            var raw = localStorage.getItem(LS_LAYERS_KEY);
            if (!raw) return;
            var active = JSON.parse(raw);
            if (!Array.isArray(active) || !active.length) return;
            // Filter to valid IDs only
            var valid = active.filter(function (id) {
                return LAYER_BTN_IDS.indexOf(id) !== -1;
            });
            valid.forEach(function (id, i) {
                setTimeout(function () {
                    var btn = document.getElementById(id);
                    if (btn && btn.getAttribute('aria-pressed') !== 'true') btn.click();
                }, 700 + i * 350);
            });
            // Update legend after all staggered restores have fired
            if (valid.length) setTimeout(_updateLegend, 700 + valid.length * 350 + 50);
            // Show opacity rows for restored polygon layers
            if (valid.length) {
                setTimeout(function() {
                    valid.forEach(function(id) {
                        var b = document.getElementById(id);
                        if (b && b.getAttribute('aria-pressed') === 'true') _showOpacityRow(id);
                    });
                }, 700 + valid.length * 350 + 100);
            }
        } catch (e) { /* malformed storage */ }
    }

    // ─── Boot ─────────────────────────────────────────────────────────────────
    function boot() {
        ensureLeaflet()
            .then(function () {
                if (!window.L) throw new Error('Leaflet unavailable');
                initMap();
                restoreFromHash();
                loadFilters();
                wireMapControls();
                wireSpotTypeFilters();
                wireCommunityLayer();
                wireLogCatch();
                wireFullscreen();
                wireShareBtn();
                wireAdminMode();
                wireLayersPopup();
                wireSstLayer();
                wireMetarLayer();
                wireRecentStorms();
                wireMarineWarnings();
                wireStormTracker();
                wireBuoyLayer();
                wireHfradarLayer();
                wireTropicalOutlook();
                wireCategoryFilterTabs();
                wireTideChart();
                wireSpotDetailPanel();
                _syncBottomBarLayout();
                restoreLayerState();
                // Restore cached structure data from the previous page view so
                // markers appear instantly on refresh instead of waiting for Overpass.
                _ssLoad();
                // Kick off the structure query immediately when server-provided
                // coordinates are available — don't wait for the NOAA API round-trip.
                if (typeof CURRENT_LOC_LAT !== 'undefined' && CURRENT_LOC_LAT &&
                    typeof CURRENT_LOC_LNG !== 'undefined' && CURRENT_LOC_LNG) {
                    // Fire immediately (no debounce) — _ssLoad may have already
                    // populated spotCache, so queryStructures() can render at once.
                    queryStructures();
                }
                _hideMainLoading();
                scheduleAIQuery();
            })
            .catch(function (err) {
                console.error('[fishing-map] boot error:', err);
                if (els.loading) els.loading.textContent = 'Map could not be loaded.';
            });
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
            var pressed = isFullscreen ? 'true' : 'false';
            if (fsToolbarBtn) fsToolbarBtn.setAttribute('aria-pressed', pressed);
            if (fsMapBtn) fsMapBtn.setAttribute('aria-pressed', pressed);
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
            var hashParts = [];
            if (activeSpotTypes.length) hashParts.push('types=' + activeSpotTypes.slice().sort().join(','));
            // Include current map center + zoom so the shared link opens to the same view
            if (map) {
                var c = map.getCenter();
                hashParts.push('lat=' + c.lat.toFixed(5));
                hashParts.push('lng=' + c.lng.toFixed(5));
                hashParts.push('z=' + map.getZoom());
            }
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
        var params = {};
        raw.split('&').forEach(function (part) {
            var eq = part.indexOf('=');
            if (eq === -1) return;
            params[part.slice(0, eq)] = decodeURIComponent(part.slice(eq + 1));
        });
        if (params.types) {
            var requested = params.types.split(',').map(function (t) { return t.trim(); })
                             .filter(function (t) { return t && SPOT_TYPES[t]; });
            if (requested.length) _applySpotTypeUI(requested);
        }
        // Restore shared map view (center + zoom)
        if (map && params.lat && params.lng) {
            var lat = parseFloat(params.lat);
            var lng = parseFloat(params.lng);
            var z   = params.z ? parseInt(params.z, 10) : null;
            if (!isNaN(lat) && !isNaN(lng)) {
                if (z && !isNaN(z)) map.setView([lat, lng], z, { animate: false });
                else map.panTo([lat, lng], { animate: false });
            }
        }
        updateAdvBadge();
    }

    // ─── Bottom bar layout ────────────────────────────────────────────────────
    // The filter bar and tide bar are both absolute-positioned at bottom:0.
    // We measure the filter bar's actual rendered height and set a CSS custom
    // property so the tide bar stacks directly above it without overlap.
    function _syncBottomBarLayout() {
        var fb = document.getElementById('fmap-spot-filter-bar');
        var tb = document.getElementById('fmap-tide-bar');
        function _apply() {
            var fh = fb ? fb.offsetHeight : 0;
            var th = tb ? tb.offsetHeight : 0;
            if (fh > 0) document.documentElement.style.setProperty('--fmap-filter-bar-h', fh + 'px');
            if (th > 0) document.documentElement.style.setProperty('--fmap-tide-bar-h',   th + 'px');
        }
        _apply();
        if (typeof ResizeObserver !== 'undefined') {
            var ro = new ResizeObserver(_apply);
            if (fb) ro.observe(fb);
            if (tb) ro.observe(tb);
        }
    }

    // ─── Score-based marker tinting ───────────────────────────────────────────
    // Defined at module scope so renderFishingSpots can call it after each render.
    function _recolourSpotsByScore(score) {
        if (!fishingSpotLayer) return;
        var color = score >= 8 ? '#4ade80' :
                    score >= 6 ? '#a3e635' :
                    score >= 4 ? '#fbbf24' : '#f87171';
        fishingSpotLayer.eachLayer(function (layer) {
            if (!layer._icon) return;
            var dot = layer._icon.querySelector('.fmap-spot-dot');
            if (dot) {
                dot.style.borderColor = color;
                dot.style.boxShadow   = '0 0 7px ' + color + '77';
            }
        });
    }

    // ─── Favorite spot pins ("My Spots" category) ────────────────────────────
    // Renders golden star markers from the _favoriteSpotKeys localStorage store
    // whenever the "My Spots" category tab is active.
    function renderFavoriteSpots() {
        if (!map) return;
        // Create the layer once; add it above fishingSpotLayer
        if (!_favSpotsLayer) {
            _favSpotsLayer = L.layerGroup().addTo(map);
        }
        _favSpotsLayer.clearLayers();
        var keys = Object.keys(_favoriteSpotKeys || {});
        if (!keys.length) {
            _renderFavEmptyState();
            return;
        }
        _hideFavEmptyState();
        keys.forEach(function (k) {
            var sp = _favoriteSpotKeys[k];
            if (!sp || !sp.lat || !sp.lng) return;
            var icon = L.divIcon({
                className: 'fmap-fav-pin-wrap',
                html: '<div class="fmap-fav-pin" title="' + esc(sp.name || 'Saved spot') +
                      '"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"' +
                      ' stroke="none" aria-hidden="true"><path d="M12 2l3.09 6.26L22 9.27l-5' +
                      ' 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>' +
                      '</svg></div>',
                iconSize:   [22, 22],
                iconAnchor: [11, 11],
            });
            var m = L.marker([sp.lat, sp.lng], { icon: icon, zIndexOffset: 200 });
            m.bindTooltip('<strong>' + esc(sp.name || spotTypeLabel(sp.type) || 'Saved spot') +
                          '</strong><br><small>' + sp.lat.toFixed(4) + ', ' + sp.lng.toFixed(4) +
                          '</small>', { className: 'fmap-tooltip', direction: 'top', offset: [0,-8] });
            (function (spotData) {
                m.on('click', function () {
                    if (typeof window._fmapShowSpotDetail === 'function') {
                        window._fmapShowSpotDetail(spotData);
                    }
                });
            }({ lat: sp.lat, lng: sp.lng, name: sp.name, type: sp.type || 'fishing' }));
            _favSpotsLayer.addLayer(m);
        });
    }

    // ─── Init ─────────────────────────────────────────────────────────────────
    // ─── Category filter tabs ─────────────────────────────────────────────────
    // Replaces the flat 22-pill list with 4 high-level category tabs:
    // Structures | Habitats | Amenities | My Spots
    // Clicking a tab shows only spots in that category; clicking the active tab
    // again shows all types.
    function wireCategoryFilterTabs() {
        var tabs = document.querySelectorAll('.fmap-cat-tab');
        if (!tabs.length) return;

        function _applyCategory(cat) {
            _activeCategory = cat;
            var isMine = (cat === 'my_spots');
            // "My Spots" renders from saved favorites, not OSM types
            var types = (cat && !isMine) ? (_CATEGORY_TYPES[cat] || []) : [];
            activeSpotTypes = types.slice();
            updateAdvBadge();
            // Show/hide the favorites layer
            if (_favSpotsLayer) {
                if (isMine) _favSpotsLayer.addTo(map);
                else if (map.hasLayer(_favSpotsLayer)) map.removeLayer(_favSpotsLayer);
            }
            if (isMine) {
                // Render favorite pins; hide OSM structures and AI habitats
                renderFavoriteSpots();
                fishingSpotLayer && fishingSpotLayer.clearLayers();
                aiPickLayer && aiPickLayer.clearLayers();
                return;
            }
            // Restore OSM spots and AI habitats for non-mine categories
            _hideFavEmptyState();
            renderFishingSpots(_lastRenderedSpotKey ? (spotCache[_lastRenderedSpotKey] || []) : []);
            var cachedAI = aiCache[_lastRenderedSpotKey || ''] || [];
            if (aiPickLayer) renderAIHabitatSpots(cachedAI);
        }

        tabs.forEach(function (tab) {
            tab.addEventListener('click', function () {
                var cat = tab.getAttribute('data-cat');
                var isSame = _activeCategory === cat;
                // Deselect all tabs
                tabs.forEach(function (t) {
                    t.setAttribute('aria-pressed', 'false');
                    t.classList.remove('fmap-cat-tab--active');
                });
                if (isSame) {
                    // Toggle off — show all
                    _applyCategory(null);
                } else {
                    tab.setAttribute('aria-pressed', 'true');
                    tab.classList.add('fmap-cat-tab--active');
                    _applyCategory(cat);
                }
                saveFilters();
            });
        });
    }

    // ─── Tide chart + time slider ─────────────────────────────────────────────
    // Fetches /api/v1/map/score for the saved location and renders a 24-bar
    // chart.  A range slider lets the user drag to a specific hour; at each
    // position the spot icons are recoloured (green=Excellent…red=Slow) and
    // the strike score badge is updated.
    function wireTideChart() {
        var chartEl   = document.getElementById('fmap-tide-chart');
        var sliderEl  = document.getElementById('fmap-tide-slider');
        var scoreEl   = document.getElementById('fmap-tide-score');
        var labelEl   = document.getElementById('fmap-tide-score-label');
        var hourEl    = document.getElementById('fmap-tide-hour');
        var moonEl    = document.getElementById('fmap-tide-moon');
        if (!chartEl || !sliderEl) return;

        // Fetch scores for the map's saved location
        function fetchScores() {
            var lat = typeof CURRENT_LOC_LAT !== 'undefined' ? CURRENT_LOC_LAT : null;
            var lng = typeof CURRENT_LOC_LNG !== 'undefined' ? CURRENT_LOC_LNG : null;
            if (!lat || !lng) return;

            if (_scoreAbort) _scoreAbort.abort();
            _scoreAbort = new AbortController();
            // Show loading pulse on the badge while the request is in flight
            if (scoreEl) scoreEl.classList.add('fmap-tide-score--loading');
            fetch('/api/v1/map/score?lat=' + lat + '&lng=' + lng, {
                signal: _scoreAbort.signal
            })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.ok) {
                    if (scoreEl) scoreEl.classList.remove('fmap-tide-score--loading');
                    return;
                }
                _scoreData = d.data;
                renderChart();
                updateSliderDisplay(); // replaces scoreEl className, clearing the loading class
                if (moonEl) {
                    var _solRating = (_scoreData.solunar_rating || '').toLowerCase();
                    var _solClass  = _solRating === 'major' ? 'fmap-tide-sol--major'
                                   : _solRating === 'minor' ? 'fmap-tide-sol--minor'
                                   : 'fmap-tide-sol--none';
                    moonEl.innerHTML = esc(_scoreData.moon_phase || '') +
                        ' <span class="fmap-tide-sol ' + _solClass + '">· ' +
                        esc(_scoreData.solunar_rating || '') + '</span>';
                }
            })
            .catch(function (err) {
                if (scoreEl) scoreEl.classList.remove('fmap-tide-score--loading');
                if (err && err.name !== 'AbortError')
                    console.warn('[fishing-map] score fetch failed:', err);
            });
        }

        // Render 24 bars in the chart SVG container, with a cursor at the selected hour
        function renderChart() {
            if (!_scoreData || !chartEl) return;
            var hours = _scoreData.hours || [];
            var maxScore = 10;
            var W = 240, H = 44;
            var barW = W / 24;
            var nowHour = new Date().getHours();
            var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" aria-hidden="true">';
            var scoreColors = {Excellent:'#4ade80', Good:'#a3e635', Fair:'#fbbf24', Slow:'#f87171'};
            hours.forEach(function (h, i) {
                var color = scoreColors[h.label] || '#64748b';
                var barH  = Math.max(2, (h.score / maxScore) * (H - 4));
                var x     = i * barW;
                var y     = H - barH;
                var isSelected = (i === _tideSliderHour);
                svg += '<rect x="' + (x + 0.5) + '" y="' + y + '" width="' +
                       (barW - 1) + '" height="' + barH +
                       '" fill="' + color + '" opacity="' + (isSelected ? '1' : '0.55') +
                       '" rx="1"/>';
            });
            // Triangle/chevron marking current real-time hour at top of chart
            var ncx = (nowHour + 0.5) * barW;
            svg += '<polygon points="' + ncx + ',0 ' + (ncx - 3) + ',5 ' + (ncx + 3) + ',5"' +
                   ' fill="rgba(255,255,255,0.8)"/>';
            // Cursor line at selected hour
            var cx = (_tideSliderHour + 0.5) * barW;
            svg += '<line x1="' + cx + '" y1="0" x2="' + cx + '" y2="' + H +
                   '" stroke="#fff" stroke-width="1.5" stroke-dasharray="2 2" opacity="0.7"/>';
            svg += '</svg>';
            chartEl.innerHTML = svg;
        }

        // Update the badge and hour label for the current slider position
        var _metaRowEl  = document.getElementById('fmap-tide-meta-row');
        var _waterTempEl = document.getElementById('fmap-tide-water-temp');

        function updateSliderDisplay() {
            if (!_scoreData) return;
            var hours = _scoreData.hours || [];
            var hData = hours[_tideSliderHour] || {};
            var score = hData.score || 0;
            var label = hData.label || '';
            var grade = _LABEL_TO_GRADE[label] || 'fair';
            var fac   = hData.factors || {};

            if (scoreEl) {
                scoreEl.textContent = score;
                scoreEl.className = 'fmap-tide-score-badge fmap-tide-score-badge--' + grade;
            }
            if (labelEl) {
                labelEl.textContent = label;
                labelEl.className = 'fmap-tide-score-label' + (grade ? ' fmap-tide-score-label--' + grade : '');
            }
            if (hourEl) {
                var ampm = _tideSliderHour < 12 ? 'AM' : 'PM';
                var h12  = _tideSliderHour % 12 || 12;
                var isNow = _tideSliderHour === new Date().getHours();
                hourEl.textContent = h12 + ':00 ' + ampm + (isNow ? ' · Now' : '');
                sliderEl.setAttribute('aria-valuetext',
                    h12 + ':00 ' + ampm + (isNow ? ', current hour' : '') + (label ? ' — ' + label : ''));
            }
            // Water temp + tide state in the meta row
            if (_waterTempEl && _metaRowEl) {
                var parts = [];
                if (fac.water_temp_f != null) parts.push(fac.water_temp_f + '°F');
                if (fac.tide)                 parts.push(String(fac.tide).replace(/\s*\(.*\)/, '') + ' tide');
                if (fac.wind_mph != null)     parts.push(fac.wind_mph + ' mph wind');
                _waterTempEl.textContent = parts.join(' · ');
                _metaRowEl.hidden = !parts.length;
            }
            // Re-colour spot icons on the map by score
            _recolourSpotsByScore(score);
            // Refresh chart to move the cursor
            renderChart();
            // If the spot detail panel is open, refresh only its score + conditions
            if (_activeSpotData && typeof window._fmapRefreshPanelScore === 'function') {
                window._fmapRefreshPanelScore();
            }
        }

        // Wire the range slider
        sliderEl.min   = 0;
        sliderEl.max   = 23;
        sliderEl.value = _tideSliderHour;
        sliderEl.addEventListener('input', function () {
            _tideSliderHour = parseInt(this.value, 10) || 0;
            updateSliderDisplay();
        });

        // Click anywhere on the chart to jump to that hour
        chartEl.style.cursor = 'pointer';
        chartEl.title = window.matchMedia('(pointer: coarse)').matches
            ? 'Tap to select hour' : 'Click to select hour';
        chartEl.addEventListener('click', function (e) {
            if (!_scoreData) return;
            var rect = chartEl.getBoundingClientRect();
            if (!rect.width) return;
            var hour = Math.min(23, Math.max(0, Math.floor((e.clientX - rect.left) / rect.width * 24)));
            _tideSliderHour = hour;
            sliderEl.value  = hour;
            updateSliderDisplay();
        });

        // Hover over chart bars to preview that hour's score (desktop)
        chartEl.addEventListener('mousemove', function (e) {
            if (!_scoreData) return;
            var rect = chartEl.getBoundingClientRect();
            var x = e.clientX - rect.left;
            if (x < 0 || x > rect.width) return;
            var hoverHour = Math.min(23, Math.max(0, Math.floor(x / rect.width * 24)));
            var hData = (_scoreData.hours || [])[hoverHour] || {};
            var label = hData.label || '';
            var grade = _LABEL_TO_GRADE[label] || 'fair';
            if (scoreEl) {
                scoreEl.textContent = hData.score || 0;
                scoreEl.className = 'fmap-tide-score-badge fmap-tide-score-badge--' + grade;
            }
            if (labelEl) {
                labelEl.textContent = label;
                labelEl.className = 'fmap-tide-score-label' + (grade ? ' fmap-tide-score-label--' + grade : '');
            }
            if (hourEl) {
                var ap = hoverHour < 12 ? 'AM' : 'PM';
                hourEl.textContent = (hoverHour % 12 || 12) + ':00 ' + ap;
            }
        });
        // Restore selected-hour display when mouse leaves the chart
        chartEl.addEventListener('mouseleave', updateSliderDisplay);

        // Initial fetch — debounce if location changes
        fetchScores();
        clearTimeout(_tideChartTimer);
        _tideChartTimer = setTimeout(fetchScores, 300);
    }

    // ─── Spot detail panel ────────────────────────────────────────────────────
    // Shows when the user clicks a spot marker.  Displays: name, type, coords,
    // current strike score, predicted best times, regulation info, and a
    // "Save as favorite" toggle.
    function wireSpotDetailPanel() {
        var panel        = document.getElementById('fmap-spot-detail');
        var closeBtn     = document.getElementById('fmap-spot-detail-close');
        var favBtn       = document.getElementById('fmap-spot-detail-fav');
        var coordsEl     = document.getElementById('fmap-spot-detail-coords');
        var typeEl       = document.getElementById('fmap-spot-detail-type');
        var scoreEl      = document.getElementById('fmap-spot-detail-score');
        var condEl       = document.getElementById('fmap-spot-conditions');
        var bestTimesEl  = document.getElementById('fmap-spot-detail-best-times');
        var tipEl        = document.getElementById('fmap-spot-detail-tip');
        var regEl        = document.getElementById('fmap-spot-detail-regs');

        if (!panel) return;

        // Load persisted favorites from localStorage
        try {
            var _favRaw = localStorage.getItem('fmap_fav_spots_v1');
            _favoriteSpotKeys = _favRaw ? JSON.parse(_favRaw) : {};
        } catch(e) { _favoriteSpotKeys = {}; }

        function _saveFavorites() {
            try { localStorage.setItem('fmap_fav_spots_v1', JSON.stringify(_favoriteSpotKeys)); }
            catch(e) {}
        }

        var nameEl = document.getElementById('fmap-spot-detail-name');

        // Updates score, conditions chips, and best times — called both on panel
        // open and whenever the time slider moves or new score data arrives.
        function _refreshPanelScore() {
            var hasScore = !!_scoreData;
            var currentScore = 0, currentLabel = '', currentGrade = 'fair';
            if (hasScore) {
                var hd = (_scoreData.hours || [])[_tideSliderHour] || {};
                currentScore = hd.score || 0;
                currentLabel = hd.label || '';
                currentGrade = _LABEL_TO_GRADE[currentLabel] || 'fair';
            }
            if (scoreEl) {
                if (!hasScore) {
                    scoreEl.innerHTML = '<span class="fmap-spot-score-num fmap-spot-score-num--empty">–</span>' +
                        '<span class="fmap-spot-score-denom">/10</span>';
                    scoreEl.className = 'fmap-spot-score fmap-tide-score--loading';
                } else {
                    // Trend: compare current hour to 2 hours ahead
                    var futureHour = Math.min(23, _tideSliderHour + 2);
                    var futureScore = ((_scoreData.hours || [])[futureHour] || {}).score || 0;
                    var delta = futureScore - currentScore;
                    var trendArrow = delta >= 1.5 ? '↑' : delta <= -1.5 ? '↓' : '';
                    var trendClass = delta >= 1.5 ? 'fmap-score-trend--up' :
                                     delta <= -1.5 ? 'fmap-score-trend--down' : '';
                    var trendDirection = delta >= 1.5 ? 'Improving' : delta <= -1.5 ? 'Declining' : '';
                    var trendAttrs = trendArrow
                        ? 'title="Score in 2h: ' + futureScore + '/10"' +
                          ' aria-label="' + trendDirection + ' — score in 2h: ' + futureScore + '/10"'
                        : '';
                    scoreEl.innerHTML = '<span class="fmap-spot-score-num">' + currentScore +
                        '</span><span class="fmap-spot-score-denom">/10</span>' +
                        (currentLabel ? ' <span class="fmap-spot-score-label">' + currentLabel + '</span>' : '') +
                        (trendArrow ? ' <span class="fmap-score-trend ' + trendClass + '" ' + trendAttrs + '>' + trendArrow + '</span>' : '');
                    scoreEl.className = 'fmap-spot-score fmap-spot-score--' + currentGrade;
                }
            }
            if (condEl) {
                var hd2 = _scoreData ? ((_scoreData.hours || [])[_tideSliderHour] || {}) : {};
                var fac = hd2.factors || {};
                var chips = [];
                if (fac.tide)             chips.push({ icon: '🌊', title: 'Tide', text: String(fac.tide).replace(/\s*\(.*\)/, '') });
                if (fac.solunar && fac.solunar !== 'none')
                                          chips.push({ icon: '🌙', title: 'Solunar', text: fac.solunar.charAt(0).toUpperCase() + fac.solunar.slice(1) });
                if (fac.wind_mph != null) chips.push({ icon: '💨', title: 'Wind speed', text: fac.wind_mph + ' mph' });
                if (fac.wave_ft  != null) chips.push({ icon: '≋', title: 'Wave height', text: fac.wave_ft + ' ft' });
                if (fac.water_temp_f != null) chips.push({ icon: '🌡', title: 'Water temperature', text: fac.water_temp_f + '°F' });
                if (chips.length) {
                    condEl.innerHTML = chips.map(function (c) {
                        var chipLabel = c.title ? c.title + ': ' + String(c.text) : String(c.text);
                        return '<span class="fmap-cond-chip" title="' + esc(chipLabel) + '" aria-label="' + esc(chipLabel) + '">' +
                            '<span aria-hidden="true">' + c.icon + '</span> ' +
                            esc(String(c.text)) + '</span>';
                    }).join('');
                    condEl.hidden = false;
                } else {
                    condEl.hidden = true;
                }
            }
            // Best times — doesn't change with slider; skip re-render if already current.
            if (bestTimesEl && bestTimesEl._scoreRef !== _scoreData) {
                bestTimesEl._scoreRef = _scoreData;
                if (!_scoreData) {
                    bestTimesEl.textContent = 'Loading…';
                } else {
                    var hours = _scoreData.hours || [];
                    var goodHourNums = hours
                        .filter(function (h) { return h.score >= 7; })
                        .map(function (h) { return h.hour; });
                    var rangeStrs = [];
                    var j = 0;
                    while (j < goodHourNums.length) {
                        var rs = goodHourNums[j], re = rs;
                        while (j + 1 < goodHourNums.length && goodHourNums[j + 1] === goodHourNums[j] + 1) {
                            j++; re = goodHourNums[j];
                        }
                        rangeStrs.push(rs === re ? _fmtHour(rs) : _fmtHour(rs) + '–' + _fmtHour(re));
                        j++;
                    }
                    var bestText = rangeStrs.length
                        ? rangeStrs.slice(0, 4).join(', ')
                        : 'No peak windows today';
                    // Render a mini 24-bar sparkline so the user can see peak windows at a glance
                    var scoreColors = { Excellent: '#4ade80', Good: '#a3e635', Fair: '#fbbf24', Slow: '#f87171' };
                    var barSvg = '<svg class="fmap-best-times-spark" viewBox="0 0 48 8" ' +
                                 'preserveAspectRatio="none" aria-hidden="true" width="48" height="8">';
                    hours.forEach(function (h, i) {
                        var fill = scoreColors[h.label] || '#334155';
                        var opacity = h.score >= 7 ? '1' : '0.3';
                        barSvg += '<rect x="' + (i * 2) + '" y="0" width="1.6" height="8" ' +
                                  'fill="' + fill + '" opacity="' + opacity + '" rx="0.4"/>';
                    });
                    barSvg += '</svg>';
                    bestTimesEl.innerHTML = barSvg + '<span>' + esc(bestText) + '</span>';
                }
            }
        }
        window._fmapRefreshPanelScore = _refreshPanelScore;

        // Called by renderFishingSpots() when a spot marker is clicked
        window._fmapShowSpotDetail = function (spotData) {
            _activeSpotData = spotData;
            var key = (spotData.type || 'spot') + ':' + spotData.lat + ':' + spotData.lng;
            // Reset the best-times cache so it re-renders for the new spot
            if (bestTimesEl) bestTimesEl._scoreRef = undefined;

            // Static fields — name, coords, type (don't change with the slider)
            if (nameEl) {
                nameEl.textContent = spotData.name ||
                    ((spotData.type || 'Fishing spot').replace(/_/g, ' ')
                        .replace(/\b\w/g, function (c) { return c.toUpperCase(); }));
            }
            if (coordsEl) {
                coordsEl.textContent = spotData.lat.toFixed(5) + ', ' +
                                       spotData.lng.toFixed(5);
                coordsEl.href = 'https://maps.google.com/?q=' + spotData.lat + ',' + spotData.lng;
            }
            if (typeEl) {
                var t = spotData.type || '';
                typeEl.textContent = (t.charAt(0).toUpperCase() + t.slice(1)).replace(/_/g, ' ');
                typeEl.setAttribute('data-type', t);
            }

            // Score, conditions chips, and best times (delegate to shared helper)
            _refreshPanelScore();

            // Fishing tip from the spot
            if (tipEl) {
                var tipRow = document.getElementById('fmap-spot-tip-row');
                tipEl.textContent = spotData.tip || '';
                if (tipRow) tipRow.style.display = spotData.tip ? '' : 'none';
            }

            // Regulation info keyed by spot type
            if (regEl) {
                var regHints = {
                    reef:         'Check reef fish size/bag limits for your state.',
                    wreck:        'Verify artificial reef fishing regulations.',
                    pier:         'A pier fishing permit may be required at some locations.',
                    bridge:       'Check local ordinance for permitted bridge fishing zones.',
                    jetty:        'Verify jetty access and any posted size/bag limits.',
                    seawall:      'Confirm public fishing access before casting.',
                    saltmarsh:    'Catch-and-release encouraged in sensitive marsh areas.',
                    mangrove:     'Mangrove areas may have seasonal closures — check regs.',
                    oyster_reef:  'Oyster reef areas may be closed to harvest — check regs.',
                    grass_flat:   'Avoid anchoring in seagrass — use poles or anchor off-flat.',
                    tidal_flat:   'Some tidal flats are closed to shellfish harvest — check local regs.',
                    inlet:        'Some inlets have restricted access or no-wake zones — verify before launching.',
                    beach:        'Check local ordinances for surf fishing access and license requirements.',
                    kelp:         'Kelp harvest and some rockfish species may be restricted — check state regs.',
                    shoal:        'Verify local regulations for bottom fishing and minimum size limits.',
                    point:        'Check for posted access restrictions on public vs. private headlands.',
                    dive_site:    'Spearfishing may be prohibited at marine reserves — check before diving.',
                };
                var reg = regHints[spotData.type] || '';
                regEl.textContent = reg;
                var regRow = document.getElementById('fmap-spot-reg-row');
                if (regRow) regRow.style.display = reg ? '' : 'none';
            }

            // Favorite button state
            if (favBtn) {
                var isFav = !!_favoriteSpotKeys[key];
                favBtn.setAttribute('aria-pressed', isFav ? 'true' : 'false');
                var _favLabel = isFav ? 'Remove from favorites' : 'Save as favorite';
                favBtn.title = _favLabel;
                favBtn.setAttribute('aria-label', _favLabel);
            }

            panel.setAttribute('aria-hidden', 'false');
            panel.classList.add('fmap-spot-detail--open');
            // Move focus to the close button after slide-in completes
            if (closeBtn) setTimeout(function () { closeBtn.focus(); }, 230);
        };

        var _panelPrevFocus = null;

        // Wrap to capture focus origin and scroll panel to top on each open
        (function () {
            var _inner = window._fmapShowSpotDetail;
            var _prevMarker = null;
            window._fmapShowSpotDetail = function (spotData) {
                // _activeSpotMarker was already set by the click handler to the NEW marker
                _setMarkerActive(_prevMarker, false);      // clear previous
                _setMarkerActive(_activeSpotMarker, true); // highlight new
                _prevMarker = _activeSpotMarker;
                _panelPrevFocus = document.activeElement || null;
                _inner(spotData);
                // Scroll the panel body to the top so repeated opens feel fresh
                var body = panel.querySelector('.fmap-spot-detail-body');
                if (body) body.scrollTop = 0;
            };
        }());

        function _setMarkerActive(marker, on) {
            if (!marker || !marker._icon) return;
            var dot = marker._icon.querySelector('.fmap-spot-dot');
            if (dot) dot.classList.toggle('fmap-spot-dot--active', on);
        }

        function _closePanel() {
            panel.style.transform = '';
            panel.style.transition = '';
            panel.classList.remove('fmap-spot-detail--open');
            panel.setAttribute('aria-hidden', 'true');
            _activeSpotData = null;
            _setMarkerActive(_activeSpotMarker, false);
            _activeSpotMarker = null;
            if (_panelPrevFocus && typeof _panelPrevFocus.focus === 'function') {
                _panelPrevFocus.focus({ preventScroll: true });
                _panelPrevFocus = null;
            }
        }

        // Swipe-down to dismiss on touch devices
        var _swipeStartY = 0, _swipeActive = false;
        var _swipeZone = panel.querySelector('.fmap-spot-detail-handle') ||
                         panel.querySelector('.fmap-spot-detail-header');
        if (_swipeZone) {
            _swipeZone.addEventListener('touchstart', function (e) {
                if (!panel.classList.contains('fmap-spot-detail--open')) return;
                _swipeStartY = e.touches[0].clientY;
                _swipeActive = true;
                panel.style.transition = 'none';
            }, { passive: true });
        }
        document.addEventListener('touchmove', function (e) {
            if (!_swipeActive) return;
            var dy = e.touches[0].clientY - _swipeStartY;
            if (dy < 0) return;
            panel.style.transform = 'translateY(' + dy + 'px)';
        }, { passive: true });
        document.addEventListener('touchend', function (e) {
            if (!_swipeActive) return;
            _swipeActive = false;
            var dy = e.changedTouches[0].clientY - _swipeStartY;
            if (dy > 80) {
                panel.style.transition = 'transform 0.2s ease-in';
                panel.style.transform = 'translateY(100%)';
                var _swipeTarget = _activeSpotData;
                setTimeout(function () {
                    if (_activeSpotData === _swipeTarget) _closePanel();
                }, 200);
            } else {
                panel.style.transition = '';
                panel.style.transform = '';
            }
        }, { passive: true });

        if (closeBtn) {
            closeBtn.addEventListener('click', _closePanel);
        }

        // Escape key closes the panel; Tab is trapped within while open
        document.addEventListener('keydown', function (e) {
            if (!panel.classList.contains('fmap-spot-detail--open')) return;
            if (e.key === 'Escape') { _closePanel(); return; }
            _trapFocusOnTab(panel, e);
        });

        if (favBtn) {
            favBtn.addEventListener('click', function () {
                if (!_activeSpotData) return;
                var key = (_activeSpotData.type || 'spot') + ':' +
                          _activeSpotData.lat + ':' + _activeSpotData.lng;
                var isFav = !!_favoriteSpotKeys[key];
                if (isFav) {
                    delete _favoriteSpotKeys[key];
                } else {
                    _favoriteSpotKeys[key] = {
                        type: _activeSpotData.type,
                        name: _activeSpotData.name || '',
                        lat:  _activeSpotData.lat,
                        lng:  _activeSpotData.lng,
                        savedAt: Date.now()
                    };
                }
                _saveFavorites();
                var _newFavLabel = !isFav ? 'Remove from favorites' : 'Save as favorite';
                favBtn.setAttribute('aria-pressed', !isFav ? 'true' : 'false');
                favBtn.title = _newFavLabel;
                favBtn.setAttribute('aria-label', _newFavLabel);
                showToast(!isFav ? 'Spot saved to favorites' : 'Spot removed from favorites');
                // Keep My Spots layer current if it's active
                if (_activeCategory === 'my_spots') renderFavoriteSpots();
            });
        }

        // "Log a catch here" quick action — opens the log modal at the spot's coords
        var logHereBtn = document.getElementById('fmap-spot-detail-log');
        if (logHereBtn) {
            logHereBtn.addEventListener('click', function () {
                if (!_activeSpotData) return;
                if (!IS_LOGGED_IN) { showToast('Sign in to log catches on the map'); return; }
                var lat      = _activeSpotData.lat;
                var lng      = _activeSpotData.lng;
                var spotName = _activeSpotData.name || ''; // capture before _closePanel clears it
                _closePanel();
                // Place a temporary pin and open the log form
                if (pendingCatchMarker && map) map.removeLayer(pendingCatchMarker);
                if (map) {
                    pendingCatchMarker = L.marker([lat, lng], {
                        icon: L.divIcon({
                            className: 'fmap-community-pin-wrap',
                            html: '<span class="fmap-community-pin fmap-community-pin--mine"></span>',
                            iconSize: [22, 28], iconAnchor: [11, 26]
                        })
                    }).addTo(map);
                }
                openLogModal(lat, lng);
                // Pre-fill the catch title with the spot name
                if (spotName && els.logTitle && !els.logTitle.value) {
                    els.logTitle.value = spotName;
                }
            });
        }

        // Click on the map (not a marker) closes the panel
        if (map) {
            map.on('click', function () {
                if (panel.classList.contains('fmap-spot-detail--open')) {
                    _closePanel();
                }
            });
        }
    }

    // Shows an inline empty-state hint inside the favorites layer when no spots are saved.
    function _renderFavEmptyState() {
        var el = document.getElementById('fmap-fav-empty');
        if (!el) {
            var mapWrap = document.querySelector('.fmap-map-wrap');
            if (!mapWrap) return;
            el = document.createElement('div');
            el.id = 'fmap-fav-empty';
            el.className = 'fmap-fav-empty';
            var _tapOrClickFav = window.matchMedia('(pointer: coarse)').matches ? 'Tap' : 'Click';
            el.setAttribute('role', 'status');
            el.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>' +
                '<p>No saved spots yet</p>' +
                '<small>' + _tapOrClickFav + ' any spot marker, then press <strong>Save as favorite</strong> to save it here.</small>';
            mapWrap.appendChild(el);
        }
        el.hidden = false;
    }

    function _hideFavEmptyState() {
        var el = document.getElementById('fmap-fav-empty');
        if (el) el.hidden = true;
    }

    function init() {
        var root = document.getElementById('fmap-root');
        if (!root) return;

        els.mapEl         = document.getElementById('fishing-map-el');
        els.loading       = document.getElementById('fmap-loading');
        // Community / social elements
        els.catchDetail    = document.getElementById('fmap-catch-detail');
        els.catchDetailTitle = document.getElementById('fmap-catch-detail-title');
        els.catchDetailMeta  = document.getElementById('fmap-catch-detail-meta');
        els.catchDetailBody  = document.getElementById('fmap-catch-detail-body');
        els.catchDetailComments = document.getElementById('fmap-catch-detail-comments');
        els.catchDetailActions  = document.getElementById('fmap-catch-detail-actions');
        // Log modal
        els.logModal       = document.getElementById('fmap-log-modal');
        els.logForm        = document.getElementById('fmap-log-form');
        els.logCoords      = document.getElementById('fmap-log-modal-coords');
        els.logSpecies     = document.getElementById('fmap-log-species');
        els.logWeight      = document.getElementById('fmap-log-weight');
        els.logLength      = document.getElementById('fmap-log-length');
        els.logBait        = document.getElementById('fmap-log-bait');
        els.logNotes       = document.getElementById('fmap-log-notes');
        els.logTitle       = document.getElementById('fmap-log-title');
        els.logCaughtAt    = document.getElementById('fmap-log-caught-at');
        els.logImageUrl    = document.getElementById('fmap-log-image-url');
        els.logPublic      = document.getElementById('fmap-log-public');
        els.logError       = document.getElementById('fmap-log-error');
        els.logSubmit      = document.getElementById('fmap-log-submit');
        if (!els.mapEl) return;
        // Defer Leaflet CDN fetch + map init until the section enters the
        // viewport.  Users who never scroll to the map skip the ~158 KB
        // Leaflet download entirely.  rootMargin: '300px' means init starts
        // just before the section reaches the visible area.
        if (window.IntersectionObserver) {
            var _mapObs = new window.IntersectionObserver(function (entries) {
                if (entries[0].isIntersecting) {
                    _mapObs.disconnect();
                    boot();
                }
            }, { rootMargin: '300px' });
            _mapObs.observe(root);
        } else {
            boot();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
