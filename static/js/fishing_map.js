(function () {
    'use strict';

    // ─── Config ───────────────────────────────────────────────────────────────
    var API_URL = '/api/fishing-map';

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
    var userCoords       = null;      // {lat, lng} set after Near Me fires
    var sortByDist       = false;    // legacy (kept for localStorage compat)
    var fishingSpotLayer = null;     // L.layerGroup for structure markers
    var spotQueryTimer   = null;     // debounce timer for structure queries
    var spotCache        = {};       // bbox+types key → array of spot objects
    var _ssSaveTimer          = null; // debounce timer for sessionStorage writes
    var _lastRenderedSpotKey  = null; // cache key of the last renderFishingSpots() call
    var _elStructFiltersHint  = null; // cached DOM ref — fmap-struct-filters-hint
    var _elSpotTypesClear     = null; // cached DOM ref — fmap-spot-types-clear
    var _spotIconCache        = {};   // type → L.divIcon; icons are immutable so one per type
    var _markerIconCache      = {};   // "activity|0/1" → L.divIcon (10 combinations max)
    var _elStructSpinner      = null; // cached DOM ref — fmap-struct-spinner
    var _elStructError        = null; // cached DOM ref — fmap-struct-error
    var _elStructErrorMsg     = null; // cached DOM ref — fmap-struct-error-msg
    var activeSpotTypes      = [];   // [] = all types; populated by type-filter pills
    var _spotTypeSaveTimer   = null; // debounce timer for persisting spotTypes
    var _structLoadCount     = 0;    // pending /api/map/structures requests (spinner ref-count)
    var _structReqGen        = 0;    // monotonic counter; stale completions are discarded
    var _structAbort         = null; // AbortController for the live structure fetch
    var _mainAbort           = null; // AbortController for the in-flight /api/fishing-map fetch
    var aiPickLayer      = null;     // L.layerGroup for AI habitat picks
    var aiQueryTimer     = null;     // debounce timer for AI habitat queries
    var aiCache          = {};       // bbox-key+species → array of habitat features
    var _aiReqGen        = 0;        // monotonic counter; stale AI completions are discarded
    var _aiAbort         = null;     // AbortController for the live AI habitat fetch

    // ─── Structure-mode state ─────────────────────────────────────────────────
    var structureMode       = false;
    var structureMarkers    = [];    // [{leaflet, data}]
    var structureFetchTimer = null;
    var lastStructureBbox   = null;  // last fetched {sw_lat,sw_lng,ne_lat,ne_lng}

    // ─── AI-overlay state ─────────────────────────────────────────────────────
    var aiMode        = false;
    var heatLayer     = null;
    var aiPickMarkers = [];          // [{leaflet, data}]

    // ─── Advanced filter state ────────────────────────────────────────────────
    var activeSeason  = '';          // spring|summer|fall|winter|''
    var activeTime    = '';          // dawn|morning|midday|evening|night|''
    var activeTide    = '';          // incoming|outgoing|high|low|''
    var activeMinTemp = '';          // numeric string or ''
    var activeMaxTemp = '';          // numeric string or ''

    // ─── Community / social state ─────────────────────────────────────────────
    var communityLayerOn  = false;   // whether community pins are visible
    var communityLayer    = null;    // L.layerGroup for community catch pins
    var communityData     = [];      // [{id,lat,lng,species,…}]
    var communityTimer    = null;    // debounce for community fetch on move
    var catchLogMode      = false;   // user is placing a catch pin
    var pendingCatchLatLng = null;   // {lat,lng} for the log modal
    var pendingCatchMarker = null;   // temporary L.marker shown before submit
    var activeTab         = 'spots'; // 'spots' | 'ai' | 'community'
    var IS_LOGGED_IN      = !!(window.IS_LOGGED_IN || false);

    // ─── ArcGIS Live Feeds state ──────────────────────────────────────────────
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
    var wildfireOn        = false;   // wildfire + smoke overlay active
    var wildfireLayer     = null;    // L.layerGroup for fire + smoke
    var wildfireTimer     = null;    // debounce for viewport reload
    var seaIceOn          = false;   // sea ice extent overlay active
    var seaIceLayer       = null;    // L.layerGroup for ice boundary
    var seismicOn         = false;   // USGS seismic overlay active
    var seismicLayer      = null;    // L.layerGroup for quake markers
    var seismicTimer      = null;    // debounce for viewport reload
    var metarOn           = false;   // METAR surface obs overlay active
    var metarLayer        = null;    // L.layerGroup for METAR stations
    var metarTimer        = null;    // debounce for viewport reload
    var terminatorOn      = false;   // Day/Night terminator overlay active
    var terminatorLayer   = null;    // L.layerGroup for shadow polygon
    var terminatorInterval = null;   // setInterval for auto-refresh
    var gaugeOn           = false;   // Stream gauge overlay active
    var gaugeLayer        = null;    // L.layerGroup for gauge markers
    var gaugeTimer        = null;    // debounce for viewport reload
    var stormRptOn        = false;   // Storm reports overlay active
    var stormRptLayer     = null;    // L.layerGroup for storm report markers
    var stormRptTimer     = null;    // debounce for viewport reload

    // Per-layer AbortControllers — cancel in-flight requests when viewport changes
    var sstAbort        = null;
    var wildfireAbort   = null;
    var seismicAbort    = null;
    var metarAbort      = null;
    var gaugeAbort      = null;
    var stormRptAbort   = null;
    var marineWarnAbort = null;

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
    var TILE_SATELLITE = {
        url:  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        opts: { attribution: 'Tiles &copy; Esri &mdash; Source: Esri, USGS, NOAA', maxZoom: 19 }
    };
    var TILE_STREET = {
        url:  'https://{s}.basemaps.cartocdn.com/dark_matter_no_labels/{z}/{x}/{y}{r}.png',
        opts: { attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap', subdomains: 'abcd', maxZoom: 19 }
    };
    var activeTileLayer = null;
    var isSatellite = true;

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
            hasAutoZoomed = true; // don't let autoZoomToSavedLocation reset the view
            // Pre-warm the structure cache for the full home corridor so nearby
            // icons appear immediately and pan/zoom serve from cache.
            // 500 ms lets the tile request and first moveend query fire first,
            // while still dispatching while Overpass is busy on the initial fetch.
            setTimeout(prefetchHomeCorridorStructures, 500);
        }

        map = L.map(els.mapEl, { zoomControl: true }).setView(startCenter, startZoom);

        // Default: satellite so users can visually see coastline, piers, structure
        activeTileLayer = L.tileLayer(TILE_SATELLITE.url, TILE_SATELLITE.opts);
        activeTileLayer.once('tileerror', function () {
            // Fall back to street tiles if ESRI is unavailable
            map.removeLayer(activeTileLayer);
            activeTileLayer = L.tileLayer(TILE_STREET.url, TILE_STREET.opts).addTo(map);
        });
        activeTileLayer.addTo(map);

        // Layer groups — AI habitat picks render below OSM spots
        aiPickLayer      = L.layerGroup().addTo(map);
        fishingSpotLayer = L.layerGroup().addTo(map);

        // Wire zoom/pan → refresh all layers (single handler — duplicate bindings
        // caused every event to fire twice, queuing double the debounced requests).
        map.on('moveend zoomend', function () {
            updateZoomHint();
            scheduleFishingSpotQuery();
            scheduleAIQuery();
            if (structureMode) scheduleStructureFetch();
        });

        setTimeout(function () { if (map) map.invalidateSize(); }, 350);

        // Update the zoom hint immediately so it reflects the starting zoom level
        // (hidden at zoom ≥ 8, i.e. when server coords are used)
        updateZoomHint();
    }

    // ─── Map overlay controls ─────────────────────────────────────────────────
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
                        userCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
                        map.flyTo([userCoords.lat, userCoords.lng], 12, { duration: 0.9 });
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

    // ─── Zoom hint ────────────────────────────────────────────────────────────
    function updateZoomHint() {
        var hint = document.getElementById('fmap-zoom-hint');
        if (!hint || !map) return;
        hint.classList.toggle('fmap-zoom-hint--hidden', map.getZoom() >= 8);
    }


    // ─── AI Habitat Spot Finder ───────────────────────────────────────────────
    //
    // Habitat type is inferred from the bait/rig/lures text returned by the API
    // for the matched species — covers all 851 species without name hardcoding.
    //
    var HABITAT_DEFS = {
        pelagic: {
            tags:    [],   // open-ocean species — no fixed OSM features
            color:   '#60a5fa',
            insight: 'This is an offshore, open-water species. Fish concentrate along temperature breaks, current edges, floating weedlines, and bait schools — none of which are fixed map features. Head offshore and watch for birds, bait activity, and blue/green water color changes.'
        },
        surf: {
            tags: [
                'way["natural"="beach"]',
                'node["natural"="shoal"]'
            ],
            color:   '#fde68a',
            insight: 'Surf species work the wash zone along sandy beaches. Focus on troughs and cuts behind sandbars — the water digs deeper in those spots and concentrates bait. Fish low-light edges of the trough.'
        },
        mangrove: {
            tags: [
                'way["natural"="wetland"]["wetland"="mangrove"]',
                'way["waterway"="tidal_channel"]'
            ],
            color:   '#22c55e',
            insight: 'Mangrove species ambush prey along root edges and tidal creek mouths. Work falling tides at pinch points — culverts, bends, and channel exits where bait gets squeezed out.'
        },
        grassflat: {
            tags: [
                'way["natural"="wetland"]["wetland"="seagrass"]',
                'way["natural"="wetland"]["wetland"="saltmarsh"]',
                'way["waterway"="tidal_channel"]'
            ],
            color:   '#34d399',
            insight: 'Grass-flat species patrol the edges where seagrass or marsh meets deeper water. Dawn topwater bites happen on shallow flats; mid-day fish slide to channel edges and drop-offs. Fish current-swept grass points.'
        },
        estuary: {
            tags: [
                'way["natural"="wetland"]["wetland"="saltmarsh"]',
                'way["waterway"="tidal_channel"]',
                'node["natural"="shoal"]',
                'way["natural"="wetland"]["wetland"="tidalflat"]'
            ],
            color:   '#2dd4bf',
            insight: 'Estuary species follow bait in and out with tidal flow. Key spots: channel bends, creek mouths, oyster bars, and shallow flat edges adjacent to deeper water. Falling tides concentrate everything at the exits.'
        },
        reef: {
            tags: [
                'way["natural"="reef"]',
                'node["natural"="shoal"]',
                'node["seamark:type"="wreck"]',
                'node["historic"="wreck"]'
            ],
            color:   '#f59e0b',
            insight: 'Reef and structure species hold on hard bottom — rocky reefs, pinnacles, and wrecks. Fish the upcurrent edge where bait gets swept against structure. Work the base of rock walls and depth transitions.'
        },
        bottom: {
            tags: [
                'node["natural"="shoal"]',
                'way["waterway"="tidal_channel"]',
                'way["natural"="wetland"]["wetland"="tidalflat"]'
            ],
            color:   '#fb923c',
            insight: 'Bottom feeders work sandy or muddy substrate near structure transitions. Channel edges adjacent to flats are prime ambush zones — fish depth changes with a slow bottom presentation.'
        },
        general: {
            tags: [
                'way["natural"="reef"]',
                'node["natural"="shoal"]',
                'way["natural"="wetland"]["wetland"="saltmarsh"]',
                'way["waterway"="tidal_channel"]'
            ],
            color:   '#a78bfa',
            insight: 'Fish concentrate where structure meets current — reef edges, channel bends, shoal drop-offs, and marsh creek mouths. These highlighted areas offer the best natural ambush opportunities in the current view.'
        }
    };

    // Infer habitat type from bait + rig + lures text.
    // Automatically covers all 851 species without any name hardcoding.
    function inferHabitatType(meta) {
        var text = [meta.bait || '', meta.rig || '', meta.lures || ''].join(' ').toLowerCase();

        if (/troll|offshore|blue\s*water|open\s*ocean|spreader\s*bar|ballyhoo|cedar\s*plug|feather|marlin|sailfish|wahoo|yellowfin|blackfin\s*tuna|mahi/.test(text)) {
            return 'pelagic';
        }
        if (/sand\s*(crab|flea|flee)|mole\s*crab|pompano\s*jig|surf\s*(rod|cast|fish)/.test(text)) {
            return 'surf';
        }
        if (/mangrove/.test(text)) {
            return 'mangrove';
        }
        if (/popping[- ]?cork|grass\s*flat|seagrass|over\s*(grass|flat)|shrimp.*cork|cork.*shrimp/.test(text)) {
            return 'grassflat';
        }
        if (/marsh|tidal\s*(creek|channel)|inlet|estuar|finger\s*mullet|live\s*shrimp|cut\s*(menhaden|mullet)/.test(text)) {
            return 'estuary';
        }
        if (/reef|rock\s*(fish|cod)|kelp|wreck|structure|bucktail|dropper\s*loop|hi[- ]?lo|jig.*reef/.test(text)) {
            return 'reef';
        }
        if (/bottom\s*rig|egg\s*sinker|fish\s*finder|pyramid\s*sinker|spreader\s*rig|sinker.*bottom/.test(text)) {
            return 'bottom';
        }
        return 'general';
    }

    var HABITAT_TYPE_LABELS = {
        reef:      { tip: 'Rocky reef or wreck' },
        saltmarsh: { tip: 'Salt marsh edge' },
        seagrass:  { tip: 'Seagrass flat' },
        mangrove:  { tip: 'Mangrove shoreline' },
        channel:   { tip: 'Tidal creek / channel' },
        shoal:     { tip: 'Shallow shoal / sandbar' },
        tidalflat: { tip: 'Tidal flat' },
        beach:     { tip: 'Sandy beach trough' },
        wreck:     { tip: 'Submerged wreck' },
        bay:       { tip: 'Bay / cove' }
    };

    function osmTagsToType(tags) {
        if (!tags) return 'general';
        if (tags.wetland === 'saltmarsh')  return 'saltmarsh';
        if (tags.wetland === 'seagrass')   return 'seagrass';
        if (tags.wetland === 'mangrove')   return 'mangrove';
        if (tags.wetland === 'tidalflat')  return 'tidalflat';
        if (tags.natural === 'reef')       return 'reef';
        if (tags.natural === 'shoal')      return 'shoal';
        if (tags.natural === 'beach')      return 'beach';
        if (tags.natural === 'bay')        return 'bay';
        if (tags.waterway)                 return 'channel';
        if (tags['seamark:type'] === 'wreck' || tags.historic === 'wreck') return 'wreck';
        return 'general';
    }

    function makeAIPickIcon(osmType, habitatType) {
        var def   = HABITAT_DEFS[habitatType] || HABITAT_DEFS.general;
        var html  = '<span class="fmap-ai-dot" style="--ai-c:' + def.color + '"></span>';
        return L.divIcon({ className: 'fmap-ai-wrap', html: html, iconSize: [14, 14], iconAnchor: [7, 7] });
    }

    var currentSpeciesMeta = null;

    function renderAIHabitatSpots(features, habitatType) {
        if (!aiPickLayer) return;
        aiPickLayer.clearLayers();

        var def     = HABITAT_DEFS[habitatType] || HABITAT_DEFS.general;
        var bar     = document.getElementById('fmap-ai-bar');
        var barText = document.getElementById('fmap-ai-bar-text');

        if (bar && barText && activeSpecies) {
            barText.textContent = def.insight;
            bar.hidden = false;
        } else if (bar) {
            bar.hidden = true;
        }

        features.forEach(function (f) {
            if (!f.lat || !f.lng) return;
            var tipCfg = HABITAT_TYPE_LABELS[f.osmType] || { tip: 'Habitat feature' };
            var m      = L.marker([f.lat, f.lng], { icon: makeAIPickIcon(f.osmType, habitatType) });
            var name   = f.name ? '<strong>' + esc(f.name) + '</strong><br>' : '';
            m.bindTooltip(
                '<span class="fmap-ai-tip-label">AI Pick</span>' + name +
                '<span style="opacity:.8">' + esc(tipCfg.tip) + '</span>',
                { className: 'fmap-tooltip fmap-ai-tooltip', direction: 'top', offset: [0, -7] }
            );
            aiPickLayer.addLayer(m);
        });
    }

    function queryAIHabitatSpots() {
        if (!map || !aiPickLayer) return;
        var bar = document.getElementById('fmap-ai-bar');

        if (!activeSpecies || !currentSpeciesMeta) {
            aiPickLayer.clearLayers();
            if (bar) bar.hidden = true;
            return;
        }

        var habitatType = inferHabitatType(currentSpeciesMeta);
        var def         = HABITAT_DEFS[habitatType];

        // Pelagic / open-water: show insight bar only, no OSM markers to place
        if (!def || !def.tags.length) {
            aiPickLayer.clearLayers();
            var barText = document.getElementById('fmap-ai-bar-text');
            if (bar && barText) { barText.textContent = def.insight; bar.hidden = false; }
            return;
        }

        if (map.getZoom() < 10) {
            aiPickLayer.clearLayers();
            if (bar) bar.hidden = true;
            return;
        }

        var b   = map.getBounds();
        var s   = Math.floor(b.getSouth() * 4) / 4;
        var w   = Math.floor(b.getWest()  * 4) / 4;
        var n   = Math.ceil(b.getNorth()  * 4) / 4;
        var e   = Math.ceil(b.getEast()   * 4) / 4;
        var key = habitatType + '|' + s + ',' + w + ',' + n + ',' + e;

        if (aiCache[key]) { renderAIHabitatSpots(aiCache[key], habitatType); return; }

        var bbox  = s + ',' + w + ',' + n + ',' + e;
        var tags  = def.tags.map(function (t) { return t + '(' + bbox + ');'; }).join('');
        var query = '[out:json][timeout:20];(' + tags + ');out center;';
        var body  = 'data=' + encodeURIComponent(query);

        // Abort any in-flight AI habitat fetch before starting the new one.
        if (_aiAbort) _aiAbort.abort();
        _aiAbort = new AbortController();
        var thisAiGen = ++_aiReqGen;
        var aiSignal  = _aiAbort.signal;

        function tryAIOverpass(urlIdx) {
            return fetch(OVERPASS_URLS[urlIdx], {
                method:  'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body:    body,
                signal:  aiSignal,
            }).then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            }).catch(function (err) {
                if (err.name === 'AbortError') throw err;  // don't retry aborted requests
                if (urlIdx + 1 < OVERPASS_URLS.length) {
                    console.warn('[fishing-map] AI habitat: mirror ' + OVERPASS_URLS[urlIdx] + ' failed, trying next…');
                    return tryAIOverpass(urlIdx + 1);
                }
                throw err;
            });
        }

        tryAIOverpass(0)
        .then(function (data) {
            if (thisAiGen !== _aiReqGen) return; // superseded by newer species/pan
            var features = (data.elements || []).map(function (el) {
                return {
                    lat:     el.lat  || (el.center && el.center.lat),
                    lng:     el.lon  || (el.center && el.center.lon),
                    name:    (el.tags || {}).name || '',
                    osmType: osmTagsToType(el.tags || {})
                };
            }).filter(function (f) { return f.lat && f.lng; });
            aiCache[key] = features;
            renderAIHabitatSpots(features, habitatType);
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

    // ─── OSM Fishing Spots (Overpass API) ─────────────────────────────────────
    var OVERPASS_URLS = [
        'https://overpass-api.de/api/interpreter',
        'https://overpass.kumi.systems/api/interpreter'
    ];
    var OVERPASS_URL = OVERPASS_URLS[0];  // kept for AI query compat

    var SPOT_TYPES = {
        pier:         { label: 'Pier',              color: '#a78bfa', habitat: false },
        jetty:        { label: 'Jetty',             color: '#818cf8', habitat: false },
        bridge:       { label: 'Bridge',            color: '#f97316', habitat: false },
        reef:         { label: 'Reef',              color: '#f59e0b', habitat: false },
        oyster_reef:  { label: 'Oyster Reef',       color: '#f59e0b', habitat: true  },
        wreck:        { label: 'Wreck',             color: '#d97706', habitat: false },
        inlet:        { label: 'Inlet / Channel',   color: '#38bdf8', habitat: true  },
        marina:       { label: 'Marina / Harbor',   color: '#67e8f9', habitat: false },
        shoal:        { label: 'Shoal',             color: '#94a3b8', habitat: false },
        point:        { label: 'Point / Headland',  color: '#c084fc', habitat: false },
        beach:        { label: 'Beach / Surf Zone', color: '#fbbf24', habitat: false },
        grass_flat:   { label: 'Grass Flat',        color: '#22c55e', habitat: true  },
        tidal_flat:   { label: 'Tidal Flat',        color: '#6ee7b7', habitat: true  },
        saltmarsh:    { label: 'Saltmarsh Edge',    color: '#34d399', habitat: true  },
        mangrove:     { label: 'Mangrove',          color: '#16a34a', habitat: true  },
        buoy:         { label: 'Navigation Buoy',   color: '#e879f9', habitat: false },
        fishing:      { label: 'Fishing Spot',      color: '#2dd4bf', habitat: false },
        fishing_shop: { label: 'Bait & Tackle',     color: '#fb923c', habitat: false }
    };

    // Habitat area types rendered as filled polygon overlays instead of point markers.
    // Must match POLYGON_HABITAT_TYPES in services/fish_structures.py.
    var POLYGON_HABITAT_TYPES = {
        saltmarsh: true, mangrove: true, tidal_flat: true,
        grass_flat: true, beach: true, oyster_reef: true, inlet: true
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

    // Single-character labels rendered inside circle markers for at-a-glance identification
    var SPOT_LABELS = {
        pier:         'P',  jetty:  'J',  bridge: 'B',  reef:  'R',
        oyster_reef:  'O',  wreck:  'W',  inlet:  'C',  marina:'M',
        shoal:        'S',  point:  '^',  beach:  '~',  buoy:  '·',
        fishing:      'F',  fishing_shop: '$'
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
        fishing_shop: 'Local bait & tackle — stop in for real-time bite reports.'
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
        // Habitat = rotating diamond (no letter); Structure = circle with type letter
        var sz    = isHabitat ? 14 : 18;
        var br    = isHabitat ? '3px' : '50%';
        var rot   = isHabitat ? 'transform:rotate(45deg)' : '';
        var lbl   = isHabitat ? '' : (SPOT_LABELS[type] || '');
        var inner = lbl
            ? '<span style="font-size:8px;font-weight:800;color:rgba(255,255,255,0.95);' +
              'font-family:system-ui,sans-serif;line-height:1;pointer-events:none;' +
              'letter-spacing:-0.5px">' + lbl + '</span>'
            : '';
        var html  = '<span class="fmap-spot-dot" style="background:' + color +
                    ';box-shadow:0 0 7px ' + color + '88;width:' + sz + 'px;height:' + sz + 'px' +
                    ';border-radius:' + br + ';flex-shrink:0;' + rot + '">' + inner + '</span>';
        var icon = L.divIcon({ className: 'fmap-spot-wrap', html: html,
                               iconSize:   [sz + 4, sz + 4],
                               iconAnchor: [Math.ceil((sz + 4) / 2), Math.ceil((sz + 4) / 2)] });
        _spotIconCache[type] = icon;
        return icon;
    }

    function renderFishingSpots(spots, cacheKey) {
        if (!fishingSpotLayer) return;
        // Skip rebuilding all Leaflet markers when the same data is already
        // displayed — common when the user pans within the same 0.5° grid cell.
        if (cacheKey && cacheKey === _lastRenderedSpotKey &&
                fishingSpotLayer.getLayers().length) {
            return;
        }
        _lastRenderedSpotKey = cacheKey || null;
        fishingSpotLayer.clearLayers();
        _customMarkers = [];  // will be repopulated by renderCustomMarkers below

        // Render OSM / NOAA spots first
        spots.filter(function (f) { return !f.custom; }).forEach(function (f) {
            var name = f.name || spotTypeLabel(f.type);
            var tip  = f.tip || STRUCTURE_TIPS[f.type] || '';
            var tooltipHtml =
                '<strong>' + esc(name) + '</strong>' +
                '<br><span style="opacity:0.75;font-size:0.7rem">' + esc(spotTypeLabel(f.type)) + '</span>' +
                (tip ? '<br><span class="fmap-struct-tip">' + esc(tip) + '</span>' : '');

            // Habitat area features with geometry → area overlay
            if (f.geometry && f.geometry.length >= 3 && POLYGON_HABITAT_TYPES[f.type]) {
                var color = spotTypeColor(f.type);
                var geom  = f.geometry;
                var layer;

                // Closed ring (OSM closed way): first ≈ last coord → filled polygon
                // Open linestring (river, canal, tidal channel): coloured stroke only
                var first = geom[0], last = geom[geom.length - 1];
                var isClosed = Math.abs(first[0] - last[0]) < 0.00002 &&
                               Math.abs(first[1] - last[1]) < 0.00002;

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
            fishingSpotLayer.addLayer(m);
        });

        // Render admin-created custom markers with edit affordances
        renderCustomMarkers(spots);
    }

    // ── Structure-query loading / error UI helpers ────────────────────────────
    // Use a ref-count so overlapping requests (e.g. rapid pan + type toggle)
    // don't hide the spinner prematurely when the first of two requests lands.

    // Show the inline spinner in the filter bar header.
    function showStructLoading() {
        _structLoadCount++;
        if (!_elStructSpinner) _elStructSpinner = document.getElementById('fmap-struct-spinner');
        if (_elStructSpinner) _elStructSpinner.hidden = false;
    }

    // Decrement the ref-count; hide the spinner only when all requests finish.
    function hideStructLoading() {
        _structLoadCount = Math.max(0, _structLoadCount - 1);
        if (_structLoadCount > 0) return;
        if (!_elStructSpinner) _elStructSpinner = document.getElementById('fmap-struct-spinner');
        if (_elStructSpinner) _elStructSpinner.hidden = true;
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

    // Return a cached spot list whose bbox fully contains [s, w, n, e] and
    // whose type string matches, or null if none found.  Used to serve
    // viewport queries from a wider pre-fetched corridor without a new request.
    // Find a cached result whose bbox covers s/w/n/e AND whose type set is a
    // superset of the requested types.  Keys have the form "s,w,n,e" (all
    // types) or "s,w,n,e|type1,type2,…" (filtered).
    //
    // When the cached entry has ALL types but the caller wants a subset, we
    // filter the array in JS so the caller never has to hit the server.
    function _cachedSupersetOf(s, w, n, e, typesStr) {
        var requestedTypes = typesStr ? typesStr.split(',') : null; // null = all
        for (var k in spotCache) {
            var pipe      = k.indexOf('|');
            var coordsStr = pipe >= 0 ? k.slice(0, pipe) : k;
            var ktype     = pipe >= 0 ? k.slice(pipe + 1) : ''; // '' = all types
            var coords    = coordsStr.split(',');
            if (coords.length < 4) continue;
            var cs = +coords[0], cw = +coords[1], cn = +coords[2], ce = +coords[3];
            if (!(cs <= s && cw <= w && cn >= n && ce >= e)) continue; // bbox too small

            if (ktype === (typesStr || '')) {
                // Exact match — return directly
                return spotCache[k];
            }
            if (!ktype && requestedTypes) {
                // Cached all-types entry; filter client-side — zero server round-trip
                return spotCache[k].filter(function (sp) {
                    return requestedTypes.indexOf(sp.type) !== -1;
                });
            }
            // ktype is a different subset; skip (we can't expand a subset to all types)
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
                    spotCache[key] = data.structures;
                    _ssSave();
                    console.log('[fishing-map] home corridor pre-fetch → ' + data.structures.length + ' features cached');
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
                            spotCache[_vkey] = data.structures;
                            renderFishingSpots(data.structures, _vkey);
                            _updateSpotTypeHint();
                        }
                    }
                }
            })
            .catch(function () {});  // silent — regular queryStructures() will still run
    }

    // ── Primary fetch: backend /api/map/structures ────────────────────────────
    // Builds a cache key that includes the type filter so different selections
    // are cached independently.  Falls back to Overpass if the server fails.
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

        // Include active type filter in the cache key
        var typesStr = activeSpotTypes.length ? activeSpotTypes.slice().sort().join(',') : '';
        var key = s + ',' + w + ',' + n + ',' + e + (typesStr ? '|' + typesStr : '');

        if (spotCache[key]) {
            renderFishingSpots(spotCache[key], key);
            _updateSpotTypeHint();
            return;
        }

        // Check whether a previously fetched (wider) bbox already contains
        // this viewport — e.g. the home-corridor pre-fetch covers zoom-12
        // viewport queries without a second Overpass trip.
        var superResult = _cachedSupersetOf(s, w, n, e, typesStr);
        if (superResult) {
            spotCache[key] = superResult;  // alias so next pan hits directly
            renderFishingSpots(superResult, key);
            _updateSpotTypeHint();
            return;
        }

        var url = '/api/map/structures' +
            '?south=' + s + '&west=' + w + '&north=' + n + '&east=' + e;
        if (typesStr) url += '&types=' + encodeURIComponent(typesStr);

        // Abort any in-flight structure fetch so the previous stale request
        // stops consuming network / server resources immediately.
        if (_structAbort) _structAbort.abort();
        _structAbort = new AbortController();
        var thisGen = ++_structReqGen;

        showStructLoading();
        hideStructError();

        fetch(url, { signal: _structAbort.signal })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) {
                // A newer request has already started — drop this stale result.
                if (thisGen !== _structReqGen) { hideStructLoading(); return; }

                hideStructLoading();

                // Server signals the viewport is too large — show hint, clear layers.
                if (data.zoom_required) {
                    _lastRenderedSpotKey = null;
                    fishingSpotLayer.clearLayers();
                    if (!_elStructFiltersHint) _elStructFiltersHint = document.getElementById('fmap-struct-filters-hint');
                    if (_elStructFiltersHint) _elStructFiltersHint.textContent = 'Zoom in further to see structure markers';
                    return;
                }

                var spots = data.structures || [];
                console.log('[fishing-map] /api/map/structures → ' + spots.length + ' features');
                spotCache[key] = spots;
                _ssSave();
                renderFishingSpots(spots, key);
                // Restore normal hint text (may have been set to zoom-in message)
                _updateSpotTypeHint();
            })
            .catch(function (err) {
                if (err.name === 'AbortError') { hideStructLoading(); return; }
                // Drop response if superseded by a newer request.
                if (thisGen !== _structReqGen) { hideStructLoading(); return; }
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
            if (wetland === 'seagrass')  return 'grass_flat';
            if (wetland === 'saltmarsh') return 'saltmarsh';
            if (wetland === 'mangrove')  return 'mangrove';
            if (wetland === 'tidalflat') return 'tidal_flat';
            return null;
        }
        if (natural === 'mud')       return 'tidal_flat';
        if (natural === 'beach')     return 'beach';
        if (natural === 'bay')       return 'inlet';
        if (natural === 'reef')      return 'reef';
        if (natural === 'shoal' || natural === 'rock') return 'shoal';
        if (natural === 'cape' || natural === 'headland' || natural === 'peninsula') return 'point';

        if (tags.harbour === 'yes') return 'inlet';

        if (tags.landuse === 'aquaculture' &&
            (tags.produce === 'oyster' || tags.product === 'oysters')) {
            return 'oyster_reef';
        }

        if (tags.historic === 'wreck' || seamark === 'wreck') return 'wreck';

        if (waterway === 'tidal_channel' || waterway === 'river' ||
            waterway === 'canal'         || waterway === 'stream') return 'inlet';
        if (waterway === 'weir' || waterway === 'dam') return 'jetty';
        if (waterway === 'dock')                       return 'pier';

        if (manMade === 'pier'  || tags.leisure === 'pier')   return 'pier';
        if (manMade === 'jetty')                              return 'jetty';
        if (manMade === 'groyne' || manMade === 'breakwater') return 'jetty';
        if (manMade === 'wharf')                              return 'pier';
        if (manMade === 'lighthouse' || manMade === 'offshore_platform') return 'point';
        if (manMade === 'buoy')                               return 'buoy';

        if (tags.bridge === 'yes' && tags.highway) return 'bridge';

        if (tags.amenity === 'marina' || tags.leisure === 'marina') return 'marina';
        if (tags.amenity === 'boat_ramp') return 'pier';
        if (tags.leisure === 'fishing')   return 'fishing';

        if (seamark && seamark.indexOf('buoy') === 0) return 'buoy';
        if (tags.shop === 'fishing') return 'fishing_shop';

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
                   'node["natural"="wetland"]["wetland"="seagrass"](' + bbox + ');');
        }
        if (has('saltmarsh')) {
            h.push('way["natural"="wetland"]["wetland"="saltmarsh"](' + bbox + ');');
        }
        if (has('mangrove')) {
            h.push('way["natural"="wetland"]["wetland"="mangrove"](' + bbox + ');');
        }
        if (has('tidal_flat')) {
            h.push('way["natural"="wetland"]["wetland"="tidalflat"](' + bbox + ');',
                   'way["natural"="mud"](' + bbox + ');');
        }
        if (has('beach')) {
            h.push('way["natural"="beach"](' + bbox + ');');
        }
        if (has('oyster_reef')) {
            h.push('node["landuse"="aquaculture"]["produce"="oyster"](' + bbox + ');',
                   'way["landuse"="aquaculture"]["produce"="oyster"](' + bbox + ');',
                   'way["landuse"="aquaculture"]["product"="oysters"](' + bbox + ');');
        }
        if (has('inlet')) {
            h.push('way["waterway"="tidal_channel"](' + bbox + ');',
                   'way["waterway"="river"](' + bbox + ');',
                   'way["waterway"="canal"](' + bbox + ');',
                   'node["waterway"="stream"](' + bbox + ');',
                   'way["waterway"="stream"](' + bbox + ');',
                   'node["harbour"="yes"](' + bbox + ');',
                   'way["harbour"="yes"](' + bbox + ');',
                   'node["natural"="bay"](' + bbox + ');',
                   'way["natural"="bay"](' + bbox + ');');
        }

        // ── Structure point/linear types (centroid only) ──────────────────
        if (has('reef')) {
            s.push('node["natural"="reef"](' + bbox + ');',
                   'way["natural"="reef"](' + bbox + ');');
        }
        if (has('wreck')) {
            s.push('node["historic"="wreck"](' + bbox + ');',
                   'way["historic"="wreck"](' + bbox + ');',
                   'node["seamark:type"="wreck"](' + bbox + ');');
        }
        if (has('shoal')) {
            s.push('node["natural"="shoal"](' + bbox + ');',
                   'way["natural"="shoal"](' + bbox + ');',
                   'node["natural"="rock"](' + bbox + ');');
        }
        if (has('pier')) {
            s.push('node["man_made"="pier"](' + bbox + ');',
                   'way["man_made"="pier"](' + bbox + ');',
                   'node["leisure"="pier"](' + bbox + ');',
                   'way["leisure"="pier"](' + bbox + ');',
                   'node["waterway"="dock"](' + bbox + ');',
                   'way["waterway"="dock"](' + bbox + ');',
                   'node["man_made"="wharf"](' + bbox + ');',
                   'way["man_made"="wharf"](' + bbox + ');',
                   'node["amenity"="boat_ramp"](' + bbox + ');',
                   'way["amenity"="boat_ramp"](' + bbox + ');');
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
                   'node["waterway"="dam"](' + bbox + ');');
        }
        if (has('bridge')) {
            s.push('way["bridge"="yes"]["highway"~"^(primary|secondary|tertiary|trunk|unclassified|residential|service)$"](' + bbox + ');');
        }
        if (has('marina')) {
            s.push('node["amenity"="marina"](' + bbox + ');',
                   'way["amenity"="marina"](' + bbox + ');',
                   'node["leisure"="marina"](' + bbox + ');',
                   'way["leisure"="marina"](' + bbox + ');',
                   'relation["leisure"="marina"](' + bbox + ');');
        }
        if (has('point')) {
            s.push('node["natural"="cape"](' + bbox + ');',
                   'node["natural"="headland"](' + bbox + ');',
                   'way["natural"="headland"](' + bbox + ');',
                   'node["natural"="peninsula"](' + bbox + ');',
                   'node["man_made"="lighthouse"](' + bbox + ');',
                   'node["man_made"="offshore_platform"](' + bbox + ');');
        }
        if (has('fishing')) {
            s.push('node["leisure"="fishing"](' + bbox + ');',
                   'way["leisure"="fishing"](' + bbox + ');');
        }
        if (has('buoy')) {
            s.push('node["seamark:type"="buoy_lateral"](' + bbox + ');',
                   'node["seamark:type"="buoy_cardinal"](' + bbox + ');',
                   'node["seamark:type"="buoy_safe_water"](' + bbox + ');',
                   'node["man_made"="buoy"](' + bbox + ');');
        }
        if (has('fishing_shop')) {
            s.push('node["shop"="fishing"](' + bbox + ');');
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
        // Build a query scoped to active types; empty activeSpotTypes = all types.
        var q = _buildFallbackQuery(bbox, activeSpotTypes);
        if (!q) {
            hideStructLoading();
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
            if (gen !== _structReqGen) { hideStructLoading(); return; }

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
                if (f.lat < s || f.lat > n || f.lng < w || f.lng > e) return false;
                // Belt-and-suspenders type filter; the query is already scoped
                if (activeSpotTypes.length && activeSpotTypes.indexOf(f.type) === -1) return false;
                return true;
            });

            var deduped = deduplicateSpots(spots);
            console.log('[fishing-map] Overpass fallback → ' + spots.length + ' features → ' + deduped.length + ' after dedup');
            spotCache[key] = deduped;
            _ssSave();
            hideStructLoading(); // request chain complete; drop spinner
            hideStructError();   // fallback succeeded — dismiss the error banner
            renderFishingSpots(deduped, key);
            _updateSpotTypeHint();
        })
        .catch(function (err) {
            hideStructLoading(); // both paths must release the spinner
            if (err && err.name === 'AbortError') return;
            console.error('[fishing-map] Overpass fallback error:', err);
            showStructError("Couldn\u2019t load structure data; showing basic map markers.");
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
        spotQueryTimer = setTimeout(queryStructures, 300);
    }

    // ─── sessionStorage persistence for spotCache ─────────────────────────────
    // Persists the in-memory spotCache across page refreshes within the same
    // browser session.  Structure data (piers, reefs, wrecks) rarely changes,
    // so a 30-minute TTL per entry is safe.  Quota errors are silently ignored.
    var _SS_KEY = 'fmap_spot_cache_v2';
    var _SS_TTL = 1800000; // 30 minutes in ms

    function _ssLoad() {
        try {
            var raw = sessionStorage.getItem(_SS_KEY);
            if (!raw) return;
            var obj = JSON.parse(raw);
            var now = Date.now();
            var loaded = 0;
            Object.keys(obj).forEach(function (k) {
                var e = obj[k];
                if (e && e.ts && (now - e.ts) < _SS_TTL && Array.isArray(e.data)) {
                    spotCache[k] = e.data;
                    loaded++;
                }
            });
            if (loaded) console.log('[fishing-map] sessionStorage → ' + loaded + ' bbox entries restored');
        } catch (e) { /* quota or parse error — start cold */ }
    }

    function _ssSaveNow() {
        try {
            var now = Date.now();
            var obj = {};
            Object.keys(spotCache).forEach(function (k) {
                obj[k] = { ts: now, data: spotCache[k] };
            });
            sessionStorage.setItem(_SS_KEY, JSON.stringify(obj));
        } catch (e) { /* quota exceeded — silently skip */ }
    }

    // Debounced save — coalesce rapid sequential fetches into one write.
    function _ssSave() {
        clearTimeout(_ssSaveTimer);
        _ssSaveTimer = setTimeout(_ssSaveNow, 1500);
    }

    // ─── Custom marker icon ───────────────────────────────────────────────────
    function makeIcon(activity, isSelected) {
        var _mk = activity + (isSelected ? '|1' : '|0');
        if (_markerIconCache[_mk]) return _markerIconCache[_mk];
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
        var icon = L.divIcon({
            className: 'fmap-marker-wrap',
            html: html,
            iconSize:    [size * 2, size * 2],
            iconAnchor:  [size, size],
            popupAnchor: [0, -size - 2]
        });
        _markerIconCache[_mk] = icon;
        return icon;
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

    function updateHotspotHeader(isAiMode) {
        var headerLeft = document.querySelector('.fmap-hotspots-header-left');
        if (!headerLeft) return;
        if (isAiMode) {
            headerLeft.innerHTML =
                '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#fbbf24" stroke-width="2.5" aria-hidden="true">' +
                '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>' +
                '<span class="fmap-ai-panel-label">AI Picks</span>';
        } else {
            headerLeft.innerHTML =
                '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
                '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>' +
                ' Top Spots';
        }
    }

    // ── Shared AI-picks rendering helpers ────────────────────────────────────
    // Both the Spots-tab hotspot list (aiMode=true) and the dedicated AI tab
    // panel render the same data; these two helpers keep the HTML in one place.

    // Return the <li> markup for the ranked AI picks in `locations`.
    function _aiPickItemsHtml(locations) {
        var picks = locations
            .filter(function (l) { return l.ai_pick_rank; })
            .sort(function (a, b) { return (a.ai_pick_rank || 99) - (b.ai_pick_rank || 99); });
        if (!picks.length) {
            return '<li class="fmap-hotspot-empty">No AI picks for current filters</li>';
        }
        var html = '';
        picks.forEach(function (loc) {
            var sp      = loc.top_species && loc.top_species[0];
            var spName  = sp ? (sp.name || '') : '';
            var reason  = loc.ai_reasoning || '';
            var snippet = reason.split('.')[0];
            if (snippet && snippet.length < reason.length) snippet += '.';
            var act = loc.activity || 'none';
            var cfg = ACTIVITY[act] || ACTIVITY.none;
            var wt  = loc.water_temp != null ? Math.round(loc.water_temp) + '\u00b0F' : '';
            html +=
                '<li class="fmap-hotspot-item fmap-ai-hotspot-item' +
                (loc.id === selectedId ? ' fmap-hotspot-item--sel' : '') +
                '" data-loc-id="' + esc(loc.id) + '">' +
                '<span class="fmap-ai-hotspot-rank">' + loc.ai_pick_rank + '</span>' +
                '<span class="fmap-hotspot-info">' +
                  '<span class="fmap-hotspot-name">' + esc(loc.name) + ', ' + esc(loc.state) + '</span>' +
                  (spName ? '<span class="fmap-hotspot-sp">' + esc(spName) + (wt ? ' &bull; ' + wt : '') + '</span>' : '') +
                  (snippet ? '<span class="fmap-ai-hotspot-snippet">' + esc(snippet) + '</span>' : '') +
                '</span>' +
                '<span class="fmap-hotspot-badge fmap-hotspot-badge--' + act + '">' + cfg.label + '</span>' +
                '</li>';
        });
        return html;
    }

    // Wire fly-to + popup on each rendered AI pick <li> within `container`.
    function _wireAiPickClicks(container) {
        container.querySelectorAll('.fmap-hotspot-item').forEach(function (li) {
            li.addEventListener('click', function () {
                var id  = li.getAttribute('data-loc-id');
                var loc = currentData.find(function (l) { return l.id === id; });
                if (!loc) return;
                map.flyTo([loc.lat, loc.lng], Math.max(map.getZoom(), 7), { duration: 0.5 });
                setTimeout(function () { showAiPickPopup(loc); }, 600);
            });
        });
    }

    function renderAiHotspots(locations) {
        if (!els.hotspotsList) return;
        var picks = locations.filter(function (l) { return l.ai_pick_rank; });
        if (els.hotspotCount) {
            els.hotspotCount.textContent = picks.length ? picks.length : '';
            els.hotspotCount.style.display = picks.length ? '' : 'none';
        }
        els.hotspotsList.innerHTML = _aiPickItemsHtml(locations);
        _wireAiPickClicks(els.hotspotsList);
    }

    function renderHotspots(locations) {
        // Always refresh AI picks list in its own panel
        var aiList = document.getElementById('fmap-ai-picks-list');
        if (aiList) renderAiPicksList(locations, aiList);

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
            var commCount = loc.community_catches || 0;
            var commBadge = commCount > 0
                ? '<span class="fmap-hotspot-community-badge" title="' + commCount + ' recent community catch' + (commCount !== 1 ? 'es' : '') + '">' +
                  '\uD83D\uDD25 ' + commCount + '</span>'
                : '';
            html +=
                '<li class="fmap-hotspot-item' + (loc.id === selectedId ? ' fmap-hotspot-item--sel' : '') +
                '" data-loc-id="' + esc(loc.id) + '">' +
                '<span class="fmap-hotspot-rank">' + (i + 1) + '</span>' +
                '<span class="fmap-hotspot-dot" style="background:' + cfg.color + ';box-shadow:0 0 5px ' + cfg.ring + '"></span>' +
                '<span class="fmap-hotspot-info">' +
                  '<span class="fmap-hotspot-name">' + esc(loc.name) + ', ' + esc(loc.state) + '</span>' +
                  (spName ? '<span class="fmap-hotspot-sp">' + esc(spName) + '</span>' : '') +
                '</span>' +
                commBadge +
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
    var LS_KEY = 'fmap_filters_v4';  // bumped for spotTypes field

    function saveFilters() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                species:    activeSpecies,
                coast:      activeCoast,
                cat:        activeCat,
                season:     activeSeason,
                time:       activeTime,
                tide:       activeTide,
                minTemp:    activeMinTemp,
                maxTemp:    activeMaxTemp,
                spotTypes:  activeSpotTypes.slice()
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
            if (f.species) {
                activeSpecies = f.species;
                if (els.speciesInput)  els.speciesInput.value = f.species;
                if (els.searchClear) els.searchClear.hidden = false;
            }
            if (f.coast && f.coast !== 'all') {
                activeCoast = f.coast;
                setPillActive('.fmap-pill--coast', 'data-coast', f.coast);
            }
            if (f.cat) {
                activeCat = f.cat;
                setPillActive('.fmap-pill--cat', 'data-cat', f.cat);
            }
            if (f.season) {
                activeSeason = f.season;
                setPillActive('.fmap-pill--season', 'data-season', f.season);
            }
            if (f.time) {
                activeTime = f.time;
                setPillActive('.fmap-pill--time', 'data-time', f.time);
            }
            if (f.tide) {
                activeTide = f.tide;
                setPillActive('.fmap-pill--tide', 'data-tide', f.tide);
            }
            if (f.minTemp) {
                activeMinTemp = f.minTemp;
                var minInput = document.getElementById('fmap-min-temp');
                if (minInput) minInput.value = f.minTemp;
            }
            if (f.maxTemp) {
                activeMaxTemp = f.maxTemp;
                var maxInput = document.getElementById('fmap-max-temp');
                if (maxInput) maxInput.value = f.maxTemp;
            }
            // Only restore from storage when restoreFromHash() hasn't already
            // applied types from the URL — the hash (shared link) wins.
            if (Array.isArray(f.spotTypes) && f.spotTypes.length && !activeSpotTypes.length) {
                var valid = f.spotTypes.filter(function (t) { return SPOT_TYPES[t]; });
                if (valid.length) _applySpotTypeUI(valid);
            }
            updateAdvBadge();
        } catch (e) {
            console.warn('[fishing-map] loadFilters failed:', e);
        }
    }

    // ─── Auto-center on saved location ───────────────────────────────────────
    var hasAutoZoomed = false;
    var savedLocationLatLng = null; // lat/lng of user's saved forecast location

    function autoZoomToSavedLocation(locations) {
        if (hasAutoZoomed) return;
        var locId = (typeof CURRENT_LOC_ID !== 'undefined') ? CURRENT_LOC_ID : '';
        if (!locId || !map) return;
        var match = locations.find(function (l) { return l.id === locId; });
        if (match) {
            hasAutoZoomed = true;
            savedLocationLatLng = { lat: match.lat, lng: match.lng };
            // Zoom to 12 so habitat/structure overlays are visible immediately
            map.setView([match.lat, match.lng], 12, { animate: false });
        }
    }

    // ─── Fetch & render ───────────────────────────────────────────────────────
    function _hideMainLoading() {
        if (!els.loading) return;
        els.loading.style.opacity = '0';
        setTimeout(function () { if (els.loading) els.loading.style.pointerEvents = 'none'; }, 300);
    }

    function fetchAndRender() {
        if (!map) return;
        saveFilters();

        var params = new URLSearchParams();
        if (activeSpecies) params.set('species', activeSpecies);
        if (activeCoast && activeCoast !== 'all') params.set('coast', activeCoast);
        if (activeCat) params.set('category', activeCat);
        if (activeMonth) params.set('month', String(activeMonth));
        // Advanced filters
        if (activeSeason) params.set('season', activeSeason);
        if (activeTime)   params.set('time_of_day', activeTime);
        if (activeTide)   params.set('tide_phase', activeTide);
        if (activeMinTemp) params.set('min_water_temp', activeMinTemp);
        if (activeMaxTemp) params.set('max_water_temp', activeMaxTemp);

        var url = API_URL + (params.toString() ? '?' + params.toString() : '');

        if (els.loading) { els.loading.style.opacity = '1'; els.loading.style.pointerEvents = 'auto'; }

        // Cancel any in-flight request so stale filter responses never overwrite fresh ones.
        if (_mainAbort) { try { _mainAbort.abort(); } catch (e) {} }
        _mainAbort = new AbortController();

        fetch(url, { signal: _mainAbort.signal })
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (data) {
                _hideMainLoading();

                currentData = data.locations || [];

                if (allSpecies.length === 0 && data.species_names) {
                    allSpecies = data.species_names;
                }

                // Update species meta for AI habitat inference (works for all 851 species)
                currentSpeciesMeta = (data.species_meta && data.species_meta.name)
                    ? data.species_meta : null;

                // Zoom to saved location then load structure overlays and community feed
                autoZoomToSavedLocation(currentData);
                updateZoomHint();
                scheduleFishingSpotQuery();
                // Reload community map pins with the updated species filter, so pins
                // reflect the same species the user has selected in the main search.
                if (communityLayerOn) scheduleCommunityLoad();
                // Load community catches once map is centred on saved location
                setTimeout(function () { loadCommunityFeed(); }, 900);
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return; // superseded by newer request
                _hideMainLoading();
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

    // ─── AI overlay ───────────────────────────────────────────────────────────

    function ensureLeafletHeat() {
        if (window.L && window.L.heatLayer) return Promise.resolve();
        return new Promise(function (resolve, reject) {
            var s = document.createElement('script');
            s.src = 'https://cdn.jsdelivr.net/npm/leaflet.heat@0.2.0/dist/leaflet-heat.js';
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    function makeAiPickIcon(rank) {
        return L.divIcon({
            className: 'fmap-ai-pick-wrap',
            html: '<span class="fmap-ai-pick-dot"><span class="fmap-ai-pick-rank">' + rank + '</span></span>',
            iconSize:    [34, 34],
            iconAnchor:  [17, 17],
            popupAnchor: [0, -20]
        });
    }

    function clearAiOverlay() {
        if (heatLayer && map) { map.removeLayer(heatLayer); heatLayer = null; }
        aiPickMarkers.forEach(function (m) { if (map) map.removeLayer(m.leaflet); });
        aiPickMarkers = [];
    }

    function renderAiOverlay(locations) {
        clearAiOverlay();
        if (!map || !window.L || !window.L.heatLayer) return;

        // Heat map — intensity proportional to score (0-100 → 0-1)
        var points = locations
            .filter(function (loc) { return loc.score > 0; })
            .map(function (loc) { return [loc.lat, loc.lng, loc.score / 100]; });

        if (points.length) {
            heatLayer = L.heatLayer(points, {
                radius:  55,
                blur:    40,
                maxZoom: 12,
                max:     1.0,
                gradient: { 0.15: '#164e63', 0.45: '#0e7490', 0.70: '#16a34a', 0.88: '#ca8a04', 1.0: '#dc2626' }
            }).addTo(map);
        }

        // AI pick markers for top 5 locations
        var picks = locations.filter(function (loc) { return loc.ai_pick_rank; });
        picks.sort(function (a, b) { return (a.ai_pick_rank || 99) - (b.ai_pick_rank || 99); });

        picks.forEach(function (loc) {
            var m = L.marker([loc.lat, loc.lng], {
                icon: makeAiPickIcon(loc.ai_pick_rank),
                zIndexOffset: 1000
            }).addTo(map);

            m.bindTooltip(
                '<strong>#' + loc.ai_pick_rank + ' AI Pick &mdash; ' + esc(loc.name) + ', ' + esc(loc.state) + '</strong>',
                { direction: 'top', offset: [0, -10], className: 'fmap-tooltip' }
            );

            m.on('click', function () { showAiPickPopup(loc); });
            aiPickMarkers.push({ leaflet: m, data: loc });
        });
    }

    function showAiPickPopup(loc) {
        if (!map) return;
        var badge = ACTIVITY[loc.activity] || ACTIVITY.none;
        var wtHtml = loc.water_temp != null
            ? '<span class="fmap-ai-popup-wt">\uD83C\uDF21\uFE0F ' + Math.round(loc.water_temp) + '\u00b0F water</span>'
            : '';
        L.popup({ className: 'fmap-ai-popup', maxWidth: 295, minWidth: 230 })
            .setLatLng([loc.lat, loc.lng])
            .setContent(
                '<div class="fmap-ai-popup-inner">' +
                '<div class="fmap-ai-popup-header">' +
                '<span class="fmap-ai-popup-pick-badge">#' + loc.ai_pick_rank + ' AI Pick</span>' +
                '<span class="fmap-ai-popup-act-badge fmap-ai-popup-act-badge--' + loc.activity + '">' + badge.label + '</span>' +
                wtHtml +
                '</div>' +
                '<div class="fmap-ai-popup-name">' + esc(loc.name) + ', ' + esc(loc.state) + '</div>' +
                '<p class="fmap-ai-popup-reasoning">' + esc(loc.ai_reasoning || '') + '</p>' +
                '<a href="/f/' + esc(loc.id) + '" class="fmap-ai-popup-link">View Full Forecast \u2192</a>' +
                '</div>'
            )
            .openOn(map);
    }

    function toggleAiMode() {
        aiMode = !aiMode;
        var btn = document.getElementById('fmap-ai-btn');
        if (btn) {
            btn.classList.toggle('fmap-ctrl-btn--active', aiMode);
            btn.setAttribute('aria-pressed', aiMode ? 'true' : 'false');
        }

        updateHotspotHeader(aiMode);

        if (aiMode) {
            markers.forEach(function (m) { m.leaflet.setOpacity(0); });
            renderAiHotspots(currentData);
            ensureLeafletHeat()
                .then(function () { if (aiMode) renderAiOverlay(currentData); })
                .catch(function (e) { console.error('[fishing-map] leaflet-heat load failed', e); });
        } else {
            clearAiOverlay();
            markers.forEach(function (m) { m.leaflet.setOpacity(1); });
            renderHotspots(currentData);
        }
    }

    // ─── Structure mode ───────────────────────────────────────────────────────

    function toggleStructureMode() {
        structureMode = !structureMode;
        var btn = document.getElementById('fmap-structure-btn');
        if (btn) btn.classList.toggle('fmap-ctrl-btn--active', structureMode);

        // Remove all current tile layers then rebuild for the new mode
        if (map) {
            map.eachLayer(function (layer) {
                if (layer instanceof L.TileLayer) map.removeLayer(layer);
            });
        }

        if (structureMode) {
            if (map) {
                // Esri Ocean Base shows bathymetry and depth gradients
                L.tileLayer(
                    'https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
                    { attribution: 'Tiles &copy; Esri', maxZoom: 16 }
                ).addTo(map);

                // OpenSeaMap overlay: wrecks, rocks, reefs, buoys as nautical chart symbols
                L.tileLayer(
                    'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png',
                    {
                        attribution: '&copy; <a href="https://www.openseamap.org/">OpenSeaMap</a> contributors',
                        maxZoom: 18,
                        opacity: 0.9
                    }
                ).addTo(map);
            }
            scheduleStructureFetch();
        } else {
            // Restore dark CARTO base
            if (map) {
                L.tileLayer(
                    'https://{s}.basemaps.cartocdn.com/dark_matter_no_labels/{z}/{x}/{y}{r}.png',
                    { attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap', subdomains: 'abcd', maxZoom: 19 }
                ).addTo(map);
            }
            clearStructureMarkers();
            setStructureHint(false);
            lastStructureBbox = null;
        }
    }

    function makeStructureIcon(type) {
        return L.divIcon({
            className: 'fmap-struct-wrap',
            html: '<span class="fmap-struct-dot fmap-struct-dot--' + type + '"></span>',
            iconSize:    [16, 16],
            iconAnchor:  [8, 8],
            popupAnchor: [0, -10]
        });
    }

    function clearStructureMarkers() {
        structureMarkers.forEach(function (m) { if (map) map.removeLayer(m.leaflet); });
        structureMarkers = [];
    }

    function drawStructureMarkers(features) {
        if (!map) return;
        clearStructureMarkers();
        features.forEach(function (feat) {
            var m = L.marker([feat.lat, feat.lng], {
                icon: makeStructureIcon(feat.type),
                title: feat.name,
                zIndexOffset: -100
            }).addTo(map);

            var depthStr = feat.depth_m != null
                ? '<br><span class="fmap-struct-depth">' + Number(feat.depth_m).toFixed(1) + ' m depth</span>'
                : '';
            m.bindTooltip(
                '<strong>' + esc(feat.name) + '</strong>' +
                '<br><span class="fmap-struct-type">' +
                (feat.type === 'wreck' ? 'Wreck' : 'Reef / Rock') +
                '</span>' + depthStr,
                { direction: 'top', offset: [0, -6], className: 'fmap-tooltip' }
            );
            structureMarkers.push({ leaflet: m, data: feat });
        });
    }

    function setStructureHint(visible) {
        var hint = document.getElementById('fmap-struct-hint');
        if (hint) hint.hidden = !visible;
    }

    function scheduleStructureFetch() {
        clearTimeout(structureFetchTimer);
        structureFetchTimer = setTimeout(doFetchStructureSpots, 500);
    }

    function doFetchStructureSpots() {
        if (!structureMode || !map) return;

        if (map.getZoom() < 8) {
            setStructureHint(true);
            clearStructureMarkers();
            return;
        }
        setStructureHint(false);

        var bounds = map.getBounds();
        var sw = bounds.getSouthWest();
        var ne = bounds.getNorthEast();

        var bbox = {
            sw_lat: Math.round(sw.lat * 100) / 100,
            sw_lng: Math.round(sw.lng * 100) / 100,
            ne_lat: Math.round(ne.lat * 100) / 100,
            ne_lng: Math.round(ne.lng * 100) / 100
        };

        // Skip if viewport hasn't shifted much since last fetch
        if (lastStructureBbox &&
            Math.abs(bbox.sw_lat - lastStructureBbox.sw_lat) < 0.25 &&
            Math.abs(bbox.sw_lng - lastStructureBbox.sw_lng) < 0.25) {
            return;
        }
        lastStructureBbox = bbox;

        var url = '/api/structure-spots' +
            '?sw_lat=' + bbox.sw_lat + '&sw_lng=' + bbox.sw_lng +
            '&ne_lat=' + bbox.ne_lat + '&ne_lng=' + bbox.ne_lng;

        fetch(url)
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (data) {
                if (!structureMode) return;
                if (data.zoom_required) { setStructureHint(true); return; }
                setStructureHint(false);
                drawStructureMarkers(data.features || []);
            })
            .catch(function (err) {
                console.error('[fishing-map] structure fetch error:', err);
            });
    }

    // ─── Utilities ────────────────────────────────────────────────────────────

    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content') || '';
        return window.CSRF_TOKEN || '';
    }

    function timeAgo(dateStr) {
        if (!dateStr) return '';
        var then = new Date(dateStr.indexOf('Z') === -1 ? dateStr + 'Z' : dateStr);
        var now  = new Date();
        var secs = Math.floor((now - then) / 1000);
        if (secs < 60)   return 'just now';
        if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
        if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
        return Math.floor(secs / 86400) + 'd ago';
    }

    // ─── Advanced filters ─────────────────────────────────────────────────────

    function updateAdvBadge() {
        var n = (activeSeason ? 1 : 0) + (activeTime ? 1 : 0) +
                (activeTide ? 1 : 0) +
                ((activeMinTemp || activeMaxTemp) ? 1 : 0);
        var badge  = document.getElementById('fmap-adv-badge');
        var toggle = document.getElementById('fmap-adv-toggle');
        if (badge) {
            badge.textContent = n;
            badge.hidden = n === 0;
        }
    }

    function setPillActive(selector, attrName, value) {
        document.querySelectorAll(selector).forEach(function (b) {
            b.classList.toggle('fmap-pill--active', b.getAttribute(attrName) === value);
        });
    }

    function wireAdvancedFilters() {
        var toggle  = document.getElementById('fmap-adv-toggle');
        var panel   = document.getElementById('fmap-adv-filters');
        var resetBtn = document.getElementById('fmap-adv-reset');

        if (toggle && panel) {
            toggle.addEventListener('click', function () {
                var open = panel.hidden;
                panel.hidden = !open;
                toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            });
        }

        document.querySelectorAll('.fmap-pill--season').forEach(function (b) {
            b.addEventListener('click', function () {
                var v = b.getAttribute('data-season');
                activeSeason = (activeSeason === v) ? '' : v;
                setPillActive('.fmap-pill--season', 'data-season', activeSeason);
                updateAdvBadge();
                scheduleFetch();
            });
        });

        document.querySelectorAll('.fmap-pill--time').forEach(function (b) {
            b.addEventListener('click', function () {
                var v = b.getAttribute('data-time');
                activeTime = (activeTime === v) ? '' : v;
                setPillActive('.fmap-pill--time', 'data-time', activeTime);
                updateAdvBadge();
                scheduleFetch();
            });
        });

        document.querySelectorAll('.fmap-pill--tide').forEach(function (b) {
            b.addEventListener('click', function () {
                var v = b.getAttribute('data-tide');
                activeTide = (activeTide === v) ? '' : v;
                setPillActive('.fmap-pill--tide', 'data-tide', activeTide);
                updateAdvBadge();
                scheduleFetch();
            });
        });

        document.querySelectorAll('.fmap-pill--coast').forEach(function (b) {
            b.addEventListener('click', function () {
                var v = b.getAttribute('data-coast') || 'all';
                activeCoast = v;
                setPillActive('.fmap-pill--coast', 'data-coast', v);
                renderQuickChips();
                scheduleFetch();
            });
        });

        document.querySelectorAll('.fmap-pill--cat').forEach(function (b) {
            b.addEventListener('click', function () {
                var v = b.getAttribute('data-cat');
                activeCat = (activeCat === v) ? '' : v;
                setPillActive('.fmap-pill--cat', 'data-cat', activeCat);
                scheduleFetch();
            });
        });

        var minTempEl = document.getElementById('fmap-min-temp');
        var maxTempEl = document.getElementById('fmap-max-temp');
        var tempTimer = null;
        function onTempChange() {
            clearTimeout(tempTimer);
            tempTimer = setTimeout(function () {
                activeMinTemp = (minTempEl && minTempEl.value) ? minTempEl.value : '';
                activeMaxTemp = (maxTempEl && maxTempEl.value) ? maxTempEl.value : '';
                updateAdvBadge();
                scheduleFetch();
            }, 600);
        }
        if (minTempEl) minTempEl.addEventListener('input', onTempChange);
        if (maxTempEl) maxTempEl.addEventListener('input', onTempChange);

        if (resetBtn) {
            resetBtn.addEventListener('click', function () {
                activeSeason = activeTime = activeTide = activeMinTemp = activeMaxTemp = '';
                setPillActive('.fmap-pill--season', 'data-season', '');
                setPillActive('.fmap-pill--time', 'data-time', '');
                setPillActive('.fmap-pill--tide', 'data-tide', '');
                if (minTempEl) minTempEl.value = '';
                if (maxTempEl) maxTempEl.value = '';
                updateAdvBadge();
                scheduleFetch();
            });
        }
    }

    // ─── Structure type-filter pills ─────────────────────────────────────────
    // Wires .fmap-pill--spot-type[data-type] toggle buttons so that anglers can
    // restrict which structure types appear on the map.  Empty activeSpotTypes
    // means "show all" (the default state).
    //
    // The spot cache is fully invalidated on every change so stale data from a
    // different filter selection never leaks through.

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
        var total = Object.keys(SPOT_TYPES).length;  // 18
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
                _scheduleSpotTypeSave();

                // Re-query immediately — _cachedSupersetOf will serve from the
                // all-types cache when available, so no server round-trip needed.
                clearTimeout(spotQueryTimer);
                queryStructures();
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
                _scheduleSpotTypeSave();
                clearTimeout(spotQueryTimer);
                queryStructures();
            });
        }

        // Wire the error banner dismiss button so users can manually clear it.
        var dismissBtn = document.getElementById('fmap-struct-error-dismiss');
        if (dismissBtn) {
            dismissBtn.addEventListener('click', hideStructError);
        }
    }

    // ─── Tabbed side panel ────────────────────────────────────────────────────

    function switchTab(tab) {
        activeTab = tab;
        ['spots', 'ai', 'community'].forEach(function (t) {
            var btn   = document.getElementById('fmap-tab-' + t);
            var panel = document.getElementById('fmap-panel-' + t);
            var active = (t === tab);
            if (btn) {
                btn.classList.toggle('fmap-side-tab--active', active);
                btn.setAttribute('aria-selected', active ? 'true' : 'false');
            }
            if (panel) {
                panel.classList.toggle('fmap-side-pane--active', active);
                panel.hidden = !active;
            }
        });
        if (tab === 'community') {
            loadCommunityFeed();
        } else if (tab === 'spots' && currentData.length) {
            renderHotspots(currentData);
        }
    }

    function wireTabs() {
        ['spots', 'ai', 'community'].forEach(function (t) {
            var btn = document.getElementById('fmap-tab-' + t);
            if (btn) btn.addEventListener('click', function () { switchTab(t); });
        });
        var refreshBtn = document.getElementById('fmap-community-refresh');
        if (refreshBtn) refreshBtn.addEventListener('click', loadCommunityFeed);
    }

    // ─── AI picks list (dedicated AI tab panel) ─────────────────────────────
    // Delegates to the shared helpers extracted near renderAiHotspots above.

    function renderAiPicksList(locations, container) {
        if (!container) return;
        container.innerHTML = _aiPickItemsHtml(locations);
        _wireAiPickClicks(container);
    }

    // ─── Community layer ──────────────────────────────────────────────────────

    function makeCommunityPin(isMine) {
        var cls = isMine ? 'fmap-community-pin--mine' : 'fmap-community-pin--public';
        return L.divIcon({
            className: 'fmap-community-pin-wrap',
            html: '<span class="fmap-community-pin ' + cls + '"></span>',
            iconSize:    [12, 12],
            iconAnchor:  [6, 6],
            popupAnchor: [0, -8]
        });
    }

    function loadCommunityPins() {
        if (!communityLayerOn || !map || !communityLayer) return;
        var b    = map.getBounds();
        var sw   = b.getSouthWest();
        var ne   = b.getNorthEast();
        var url  = '/api/map/catches?sw_lat=' + Math.round(sw.lat * 100) / 100 +
                   '&sw_lng=' + Math.round(sw.lng * 100) / 100 +
                   '&ne_lat=' + Math.round(ne.lat * 100) / 100 +
                   '&ne_lng=' + Math.round(ne.lng * 100) / 100;
        // When the user has filtered by species, show only matching catches on the map.
        if (activeSpecies) url += '&species=' + encodeURIComponent(activeSpecies);

        fetch(url)
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function (data) {
                communityData = data.catches || [];
                communityLayer.clearLayers();
                communityData.forEach(function (c) {
                    if (!c.lat || !c.lng) return;
                    var m = L.marker([c.lat, c.lng], { icon: makeCommunityPin(c.mine) });
                    m.bindTooltip(
                        '<strong>' + esc(c.species) + '</strong><br>' +
                        '<span style="opacity:.8">' + esc(c.angler_name) + ' &bull; ' + timeAgo(c.caught_at) + '</span>',
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
                console.warn('[fishing-map] loadCommunityPins failed:', err);
            });
    }

    function scheduleCommunityLoad() {
        if (!communityLayerOn) return;
        clearTimeout(communityTimer);
        communityTimer = setTimeout(loadCommunityPins, 700);
    }

    function wireCommunityLayer() {
        if (!map) return;
        communityLayer = L.layerGroup();

        var btn = document.getElementById('fmap-community-layer-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                communityLayerOn = !communityLayerOn;
                btn.classList.toggle('fmap-ctrl-btn--active', communityLayerOn);
                btn.setAttribute('aria-pressed', communityLayerOn ? 'true' : 'false');
                if (communityLayerOn) {
                    communityLayer.addTo(map);
                    loadCommunityPins();
                } else {
                    map.removeLayer(communityLayer);
                    communityLayer.clearLayers();
                }
            });
        }

        // Reload community pins on map move
        map.on('moveend zoomend', scheduleCommunityLoad);
    }

    // ─── Community catch detail drawer ────────────────────────────────────────

    function openCatchDetail(c) {
        if (!els.catchDetail) return;

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
            bodyHtml += '<div class="fmap-catch-photo-wrap">' +
                '<img src="' + esc(c.image_url) + '" class="fmap-catch-photo" alt="Catch photo" ' +
                'loading="lazy" onerror="this.parentNode.style.display=\'none\'">' +
                '</div>';
        }
        if (c.weight_lb) bodyHtml += '<div class="fmap-catch-stat"><span class="fmap-catch-stat-label">Weight</span>' + c.weight_lb.toFixed(1) + ' lb</div>';
        if (c.length_in) bodyHtml += '<div class="fmap-catch-stat"><span class="fmap-catch-stat-label">Length</span>' + c.length_in + ' in</div>';
        if (c.bait)      bodyHtml += '<div class="fmap-catch-stat"><span class="fmap-catch-stat-label">Bait</span>' + esc(c.bait) + '</div>';
        if (c.notes)     bodyHtml += '<div class="fmap-catch-notes">' + esc(c.notes) + '</div>';
        els.catchDetailBody.innerHTML = bodyHtml;

        // Load comments
        els.catchDetailComments.innerHTML = '<div style="opacity:.5;font-size:.75rem;padding:.4rem 0">Loading comments…</div>';
        fetch('/api/map/catches/' + c.id + '/comments')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var comments = data.comments || [];
                var html = '';
                comments.forEach(function (cm) {
                    html += '<div class="fmap-catch-comment"><span class="fmap-catch-comment-author">' +
                        esc(cm.angler_name) + '</span>' + esc(cm.body) +
                        '<span style="float:right;opacity:.4;font-size:.65rem">' + timeAgo(cm.created_at) + '</span></div>';
                });
                if (!html) html = '<div style="opacity:.4;font-size:.75rem;padding:.4rem 0">No comments yet</div>';
                // Add comment form if logged in
                var commentForm = IS_LOGGED_IN
                    ? '<div class="fmap-catch-comment-form">' +
                      '<input type="text" class="fmap-catch-comment-input" placeholder="Add a comment…" maxlength="500">' +
                      '<button class="fmap-catch-comment-post" data-catch-id="' + c.id + '">Post</button></div>'
                    : '';
                els.catchDetailComments.innerHTML = html + commentForm;

                var postBtn = els.catchDetailComments.querySelector('.fmap-catch-comment-post');
                var commentInput = els.catchDetailComments.querySelector('.fmap-catch-comment-input');
                if (postBtn && commentInput) {
                    postBtn.addEventListener('click', function () {
                        var body = commentInput.value.trim();
                        if (!body) return;
                        fetch('/api/map/catches/' + c.id + '/comments', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ body: body })
                        })
                        .then(function (r) { return r.json(); })
                        .then(function () { openCatchDetail(c); })
                        .catch(function () { showToast('Could not post comment.'); });
                    });
                }
            })
            .catch(function () {
                els.catchDetailComments.innerHTML = '';
            });

        // Action buttons: like + delete (own catches)
        var actionsHtml = '';
        actionsHtml += '<button class="fmap-catch-action-btn fmap-like-btn" data-catch-id="' + c.id + '">' +
            '\u2764\uFE0F ' + (c.likes_count || 0) + ' likes</button>';
        if (c.mine) {
            actionsHtml += '<button class="fmap-catch-action-btn fmap-catch-action-btn--delete fmap-delete-btn" data-catch-id="' + c.id + '">' +
                '\uD83D\uDDD1 Delete</button>';
        }
        els.catchDetailActions.innerHTML = actionsHtml;

        var likeBtn = els.catchDetailActions.querySelector('.fmap-like-btn');
        if (likeBtn) {
            likeBtn.addEventListener('click', function () {
                if (!IS_LOGGED_IN) { showToast('Sign in to like catches'); return; }
                fetch('/api/map/catches/' + c.id + '/like', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    likeBtn.textContent = '\u2764\uFE0F ' + d.likes_count + ' likes';
                    likeBtn.classList.toggle('fmap-catch-action-btn--liked', d.liked);
                    c.likes_count = d.likes_count;
                })
                .catch(function () { showToast('Could not update like.'); });
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

        var closeBtn = document.getElementById('fmap-catch-detail-close');
        if (closeBtn) {
            closeBtn.onclick = closeCatchDetail;
        }
    }

    function closeCatchDetail() {
        if (els.catchDetail) els.catchDetail.hidden = true;
    }

    // ─── Community feed ───────────────────────────────────────────────────────

    function loadCommunityFeed() {
        if (!els.communityList) return;
        els.communityList.innerHTML = '<li class="fmap-hotspot-empty">Loading…</li>';

        var url = '/api/map/feed?limit=20';
        if (userCoords) url += '&lat=' + userCoords.lat + '&lng=' + userCoords.lng;

        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var catches = data.catches || [];
                if (!catches.length) {
                    els.communityList.innerHTML = '<li class="fmap-hotspot-empty">No recent catches logged nearby.</li>';
                    return;
                }
                var html = '';
                catches.forEach(function (c) {
                    var weightStr = c.weight_lb ? ' \u2022 ' + c.weight_lb.toFixed(1) + ' lb' : '';
                    var headline  = c.title ? esc(c.title) : esc(c.species) + weightStr;
                    var subline   = c.title ? '<span class="fmap-community-species">' + esc(c.species) + weightStr + '</span>' : '';
                    var imgTag    = c.image_url
                        ? '<img class="fmap-community-thumb" src="' + esc(c.image_url) +
                          '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
                        : '';
                    html += '<li class="fmap-community-item' + (c.image_url ? ' fmap-community-item--has-img' : '') +
                        '" data-lat="' + c.lat + '" data-lng="' + c.lng + '" data-id="' + c.id + '">' +
                        imgTag +
                        '<div class="fmap-community-content">' +
                          '<span class="fmap-community-headline">' + headline + '</span>' +
                          subline +
                          '<div class="fmap-community-meta">' +
                            '<span class="fmap-community-angler">' + esc(c.angler_name) + '</span>' +
                            (c.bait ? '<span>' + esc(c.bait) + '</span>' : '') +
                            '<span class="fmap-community-time">' + timeAgo(c.caught_at) + '</span>' +
                          '</div>' +
                        '</div>' +
                        '</li>';
                });
                els.communityList.innerHTML = html;

                // Wire click to fly-to + open detail
                els.communityList.querySelectorAll('.fmap-community-item').forEach(function (li) {
                    li.addEventListener('click', function () {
                        var lat  = parseFloat(li.getAttribute('data-lat'));
                        var lng  = parseFloat(li.getAttribute('data-lng'));
                        var id   = parseInt(li.getAttribute('data-id'), 10);
                        var c    = catches.find(function (x) { return x.id === id; }) || {};
                        if (map && lat && lng) {
                            map.flyTo([lat, lng], Math.max(map.getZoom(), 12), { duration: 0.7 });
                        }
                        if (c.id) setTimeout(function () { openCatchDetail(c); }, 750);
                    });
                });

                // Update badge
                var badge = document.getElementById('fmap-community-badge');
                if (badge) {
                    badge.textContent = catches.length;
                    badge.style.display = catches.length ? '' : 'none';
                }
            })
            .catch(function () {
                if (els.communityList) els.communityList.innerHTML = '<li class="fmap-hotspot-empty">Could not load community catches.</li>';
            });
    }

    // ─── Log Catch mode ───────────────────────────────────────────────────────

    function _updateLogFab(active) {
        var btn   = document.getElementById('fmap-log-catch-btn');
        if (!btn) return;
        var plus  = btn.querySelector('.fmap-log-fab-plus');
        var x     = btn.querySelector('.fmap-log-fab-x');
        var label = btn.querySelector('.fmap-log-fab-label');
        btn.classList.toggle('fmap-log-fab--active', active);
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
            banner.textContent = 'Tap the map to place your catch pin \u2014 tap again to cancel';
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

    function openLogModal(lat, lng) {
        pendingCatchLatLng = { lat: lat, lng: lng };
        if (els.logCoords) {
            els.logCoords.textContent = lat.toFixed(5) + ', ' + lng.toFixed(5);
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
        if (els.logModal) els.logModal.hidden = false;
        if (els.logSpecies) els.logSpecies.focus();
        if (els.logError) els.logError.hidden = true;
    }

    function closeLogModal() {
        if (els.logModal) els.logModal.hidden = true;
        exitLogMode();
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
                        html: '<span class="fmap-community-pin fmap-community-pin--mine" style="width:16px;height:16px;border-width:2.5px"></span>',
                        iconSize: [16, 16], iconAnchor: [8, 8]
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

                if (els.logSubmit) els.logSubmit.disabled = true;

                fetch('/api/map/catches', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.error) {
                        if (els.logError) { els.logError.textContent = data.error; els.logError.hidden = false; }
                        if (els.logSubmit) els.logSubmit.disabled = false;
                        return;
                    }
                    showToast('Catch logged! \uD83C\uDFAF');
                    closeLogModal();
                    if (communityLayerOn) loadCommunityPins();
                    if (activeTab === 'community') loadCommunityFeed();
                })
                .catch(function () {
                    if (els.logError) { els.logError.textContent = 'Could not save catch. Please try again.'; els.logError.hidden = false; }
                    if (els.logSubmit) els.logSubmit.disabled = false;
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
                fetch('/api/map/custom-markers/' + spot.id, {
                    method:  'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ lat: ll.lat, lng: ll.lng }),
                }).catch(function (e) {
                    console.warn('[admin] drag-save failed:', e);
                });
                spot.lat = ll.lat;
                spot.lng = ll.lng;
            });

            m.on('click', function () {
                if (adminEditMode) {
                    _openAdminEditPanel(spot);
                } else {
                    var tip = spot.description || STRUCTURE_TIPS[spot.type] || '';
                    m.bindPopup(
                        '<strong>' + esc(spot.name || spotTypeLabel(spot.type)) + '</strong>' +
                        (tip ? '<br><span style="opacity:.8">' + esc(tip) + '</span>' : '')
                    ).openPopup();
                }
            });

            m.bindTooltip(
                '<strong>' + esc(spot.name || spotTypeLabel(spot.type)) + '</strong>' +
                '<br><span style="opacity:.7">' + esc(spotTypeLabel(spot.type)) + '</span>' +
                (adminEditMode ? '<br><em style="opacity:.6">click to edit</em>' : ''),
                { direction: 'top', offset: [0, -8], className: 'fmap-tooltip' }
            );

            fishingSpotLayer.addLayer(m);
            _customMarkers.push({ id: spot.id, leaflet: m, data: spot });
        });
    }

    // ── Admin modal ──────────────────────────────────────────────────────────

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
        if (modal)    { modal.hidden    = false; }
        if (backdrop) { backdrop.hidden = false; backdrop.style.display = ''; }
    }

    function _closeAdminModal() {
        var modal    = document.getElementById('fmap-admin-modal');
        var backdrop = document.getElementById('fmap-admin-backdrop');
        if (modal)    modal.hidden    = true;
        if (backdrop) { backdrop.hidden = true; backdrop.style.display = 'none'; }
    }

    function wireAdminMode() {
        if (typeof MAP_IS_ADMIN === 'undefined' || !MAP_IS_ADMIN) return;

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
                var markerId = saveBtn.dataset.markerId;
                var payload = {
                    lat:         parseFloat(document.getElementById('fmap-admin-lat').value),
                    lng:         parseFloat(document.getElementById('fmap-admin-lng').value),
                    name:        document.getElementById('fmap-admin-name').value.trim(),
                    type:        document.getElementById('fmap-admin-type').value,
                    description: document.getElementById('fmap-admin-desc').value.trim(),
                };
                var url    = markerId ? '/api/map/custom-markers/' + markerId : '/api/map/custom-markers';
                var method = markerId ? 'PUT' : 'POST';
                fetch(url, {
                    method:  method,
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(payload),
                })
                .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
                .then(function () {
                    _closeAdminModal();
                    spotCache = {};
                    try { sessionStorage.removeItem(_SS_KEY); } catch (e) {}
                    scheduleFishingSpotQuery();
                })
                .catch(function (e) { console.error('[admin] save marker failed:', e); });
            });
        }

        // ── Modal delete ──────────────────────────────────────────────────────
        var delBtn = document.getElementById('fmap-admin-delete');
        if (delBtn) {
            delBtn.addEventListener('click', function () {
                var markerId = delBtn.dataset.markerId;
                if (!markerId) return;
                if (!confirm('Delete this marker?')) return;
                fetch('/api/map/custom-markers/' + markerId, { method: 'DELETE' })
                .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
                .then(function () {
                    _closeAdminModal();
                    spotCache = {};
                    try { sessionStorage.removeItem(_SS_KEY); } catch (e) {}
                    scheduleFishingSpotQuery();
                })
                .catch(function (e) { console.error('[admin] delete marker failed:', e); });
            });
        }

        // ── Modal close / backdrop ────────────────────────────────────────────
        var closeBtn = document.getElementById('fmap-admin-modal-close');
        if (closeBtn) closeBtn.addEventListener('click', _closeAdminModal);

        var backdrop = document.getElementById('fmap-admin-backdrop');
        if (backdrop) backdrop.addEventListener('click', _closeAdminModal);
    }

    // ─── SST Stations overlay (ArcGIS Live Feeds / NOAA CoRIS) ──────────────

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
                } else {
                    map.removeLayer(sstLayer);
                    sstLayer.clearLayers();
                }
            });
        }

        map.on('moveend zoomend', function () {
            if (sstLayerOn) scheduleSstQuery();
        });
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
                    var icon = L.divIcon({
                        className: '',
                        html: '<div class="fmap-sst-dot" style="background:' + color + '">' +
                              (s.sst_f != null ? Math.round(s.sst_f) + '°' : '?') +
                              '</div>',
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
            (dhw     ? '<br><small style="opacity:.65">' + esc(dhw) + '</small>' : '') +
            (s.alert > 0
                ? '<br><span class="fmap-sst-alert" style="background:' + s.alert_color + '">' +
                  esc(s.alert_label) + '</span>'
                : '') +
            (s.updated ? '<br><small style="opacity:.45">Updated: ' +
                _sstFmtDate(s.updated) + '</small>' : '') +
            '<br><small style="opacity:.4">Source: NOAA CoRIS via ArcGIS Live Feeds</small>' +
            '</div>'
        );
    }

    function _sstFmtDate(iso) {
        try {
            return new Date(iso).toLocaleDateString([], { month:'short', day:'numeric' });
        } catch (e) { return iso; }
    }

    // ─── Wildfire + Smoke overlay (ArcGIS Live Feeds) ─────────────────────────

    function wireWildfireLayer() {
        if (!map) return;
        wildfireLayer = L.layerGroup();

        var btn = document.getElementById('fmap-wildfire-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                wildfireOn = !wildfireOn;
                btn.classList.toggle('fmap-ctrl-btn--active', wildfireOn);
                btn.setAttribute('aria-pressed', wildfireOn ? 'true' : 'false');
                if (wildfireOn) {
                    wildfireLayer.addTo(map);
                    scheduleWildfireQuery();
                } else {
                    map.removeLayer(wildfireLayer);
                    wildfireLayer.clearLayers();
                }
            });
        }

        map.on('moveend zoomend', function () {
            if (wildfireOn) scheduleWildfireQuery();
        });
    }

    function scheduleWildfireQuery() {
        clearTimeout(wildfireTimer);
        wildfireTimer = setTimeout(doFetchWildfires, 700);
    }

    function doFetchWildfires() {
        if (!wildfireOn || !map) return;
        if (wildfireAbort) { try { wildfireAbort.abort(); } catch (e) {} }
        wildfireAbort = new AbortController();
        var sig = wildfireAbort.signal;
        var b  = map.getBounds();
        var sw = b.getSouthWest();
        var ne = b.getNorthEast();
        var bbox = 'south=' + sw.lat.toFixed(3) +
                   '&west='  + sw.lng.toFixed(3) +
                   '&north=' + ne.lat.toFixed(3) +
                   '&east='  + ne.lng.toFixed(3);

        Promise.all([
            fetch('/api/map/wildfires?' + bbox, { signal: sig }).then(function (r) { return r.ok ? r.json() : {fires:[], count:0}; }),
            fetch('/api/map/smoke?'     + bbox, { signal: sig }).then(function (r) { return r.ok ? r.json() : {polygons:[], count:0}; }),
        ])
        .then(function (results) {
            if (!wildfireOn || !map) return;
            wildfireLayer.clearLayers();
            var fires   = results[0].fires    || [];
            var polys   = results[1].polygons || [];

            // Smoke polygons (rendered first, below fire markers)
            polys.forEach(function (p) {
                if (!p.rings || !p.rings.length) return;
                p.rings.forEach(function (ring) {
                    L.polygon(ring, {
                        color:       p.fill,
                        fillColor:   p.fill,
                        fillOpacity: p.opacity,
                        weight:      0,
                    })
                    .bindTooltip('<strong>Smoke:</strong> ' + esc(p.label) +
                        (p.valid_from ? '<br><small>' + _sstFmtDate(p.valid_from) + '</small>' : ''),
                        { direction: 'top', className: 'fmap-tooltip' })
                    .addTo(wildfireLayer);
                });
            });

            // Fire incident markers
            fires.forEach(function (f) {
                var sizeClass = f.acres > 50000 ? 'fmap-fire-dot--xl'
                              : f.acres > 10000 ? 'fmap-fire-dot--lg'
                              : f.acres > 1000  ? 'fmap-fire-dot--md'
                              : 'fmap-fire-dot--sm';
                var icon = L.divIcon({
                    className: '',
                    html: '<div class="fmap-fire-dot ' + sizeClass + '" title="' + esc(f.name) + '">🔥</div>',
                    iconSize:    [24, 24],
                    iconAnchor:  [12, 12],
                    popupAnchor: [0, -14],
                });
                L.marker([f.lat, f.lng], { icon: icon })
                    .bindPopup(_firePopup(f), { maxWidth: 280 })
                    .addTo(wildfireLayer);
            });
        })
        .catch(function (err) {
            if (err && err.name === 'AbortError') return;
            console.warn('[fishing-map] wildfire/smoke fetch failed:', err);
        });
    }

    function _firePopup(f) {
        var containment = f.contained_pct > 0
            ? '<br><span class="fmap-fire-contain">' + Math.round(f.contained_pct) + '% contained</span>'
            : '<br><span class="fmap-fire-contain fmap-fire-contain--0">Uncontained</span>';
        var acres = f.acres > 0
            ? '<br><small>' + f.acres.toLocaleString() + ' acres</small>'
            : '';
        var cause = f.cause ? '<br><small>Cause: ' + esc(f.cause) + '</small>' : '';
        var loc   = [f.county, f.state].filter(Boolean).join(', ');
        return (
            '<div class="fmap-fire-popup">' +
            '<strong>🔥 ' + esc(f.name) + '</strong>' +
            (loc ? '<br><small style="opacity:.7">' + esc(loc) + '</small>' : '') +
            acres + containment + cause +
            (f.age_days ? '<br><small style="opacity:.55">Day ' + f.age_days + '</small>' : '') +
            '</div>'
        );
    }

    // ─── Sea Ice Extent overlay (ArcGIS Live Feeds / NSIDC) ──────────────────

    function wireSeaIceLayer() {
        if (!map) return;
        seaIceLayer = L.layerGroup();

        var btn = document.getElementById('fmap-sea-ice-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                seaIceOn = !seaIceOn;
                btn.classList.toggle('fmap-ctrl-btn--active', seaIceOn);
                btn.setAttribute('aria-pressed', seaIceOn ? 'true' : 'false');
                if (seaIceOn) {
                    seaIceLayer.addTo(map);
                    doFetchSeaIce();
                } else {
                    map.removeLayer(seaIceLayer);
                    seaIceLayer.clearLayers();
                }
            });
        }
    }

    function doFetchSeaIce() {
        if (!seaIceOn || !map) return;
        fetch('/api/map/sea-ice')
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!seaIceOn || !map || !data || !data.sea_ice) {
                    showToast('Sea ice data unavailable');
                    return;
                }
                var ice = data.sea_ice;
                seaIceLayer.clearLayers();

                var MONTH_SHORT = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                var label = MONTH_SHORT[ice.month] + ' ' + ice.year;

                (ice.rings || []).forEach(function (ring) {
                    L.polygon(ring, {
                        color:       '#bfdbfe',
                        fillColor:   '#bfdbfe',
                        fillOpacity: 0.30,
                        weight:      1.5,
                        opacity:     0.7,
                    })
                    .bindPopup(
                        '<div class="fmap-ice-popup"><strong>Arctic Sea Ice</strong>' +
                        '<br><em>' + label + '</em>' +
                        '<br><small>Extent: ' + ice.extent_mkm2 + ' M km²</small>' +
                        '<br><small>Area: ' + ice.area_mkm2 + ' M km²</small>' +
                        '<br><small style="opacity:.5">Source: NSIDC via ArcGIS Live Feeds</small>' +
                        '</div>',
                        { maxWidth: 220 }
                    )
                    .addTo(seaIceLayer);
                });

                if (ice.rings && ice.rings.length) {
                    showToast('Arctic sea ice: ' + label + ' — ' + ice.extent_mkm2 + ' M km²');
                    // Fly to Arctic region
                    if (map.getZoom() < 4) map.setView([75, 0], 3);
                } else {
                    showToast('No sea ice data for current period');
                }
            })
            .catch(function (err) {
                console.warn('[fishing-map] sea ice fetch failed:', err);
                showToast('Sea ice data unavailable');
            });
    }

    // ─── USGS Seismic overlay (ArcGIS Live Feeds) ────────────────────────────

    function wireSeismicLayer() {
        if (!map) return;
        seismicLayer = L.layerGroup();

        var btn = document.getElementById('fmap-seismic-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                seismicOn = !seismicOn;
                btn.classList.toggle('fmap-ctrl-btn--active', seismicOn);
                btn.setAttribute('aria-pressed', seismicOn ? 'true' : 'false');
                if (seismicOn) {
                    seismicLayer.addTo(map);
                    doFetchSeismic();
                    map.on('moveend zoomend', onSeismicViewport);
                } else {
                    map.removeLayer(seismicLayer);
                    seismicLayer.clearLayers();
                    map.off('moveend zoomend', onSeismicViewport);
                }
            });
        }
    }

    function onSeismicViewport() {
        clearTimeout(seismicTimer);
        seismicTimer = setTimeout(doFetchSeismic, 600);
    }

    function doFetchSeismic() {
        if (!seismicOn || !map) return;
        if (seismicAbort) { try { seismicAbort.abort(); } catch (e) {} }
        seismicAbort = new AbortController();
        var b = map.getBounds();
        var url = '/api/map/seismic?south=' + b.getSouth().toFixed(4) +
                  '&west='  + b.getWest().toFixed(4)  +
                  '&north=' + b.getNorth().toFixed(4) +
                  '&east='  + b.getEast().toFixed(4);

        fetch(url, { signal: seismicAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!seismicOn || !map || !data) return;
                seismicLayer.clearLayers();

                var events = data.events || [];
                if (!events.length) {
                    showToast('No M2.5+ earthquakes in this area');
                    return;
                }

                events.forEach(function (ev) {
                    // Scale radius by magnitude: M2.5→5px, M5→12px, M7→22px
                    var r = Math.max(5, Math.min(28, Math.pow(ev.mag, 2.2)));
                    var color = ev.alert_color || (ev.tsunami ? '#E53E3E' : '#E97316');
                    var circle = L.circleMarker([ev.lat, ev.lng], {
                        radius:      r,
                        color:       color,
                        fillColor:   color,
                        fillOpacity: ev.mag >= 5 ? 0.75 : 0.55,
                        weight:      ev.mag >= 5 ? 2 : 1,
                        className:   'fmap-seismic-dot',
                    });

                    var timeStr = '';
                    if (ev.time) {
                        try {
                            var d = new Date(ev.time);
                            timeStr = d.toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
                        } catch (e) {}
                    }
                    var tsTag = ev.tsunami
                        ? '<span class="fmap-seismic-tag fmap-seismic-tag--tsunami">TSUNAMI ALERT</span>'
                        : '';
                    var alertTag = ev.alert
                        ? '<span class="fmap-seismic-tag" style="background:' + color + '20;color:' + color + '">' + ev.alert.toUpperCase() + '</span>'
                        : '';

                    circle.bindPopup(
                        '<div class="fmap-seismic-popup">' +
                        tsTag + alertTag +
                        '<strong>M' + ev.mag.toFixed(1) + ' — ' + (ev.event_type || 'earthquake') + '</strong>' +
                        '<div class="fmap-seismic-place">' + (ev.place || '') + '</div>' +
                        (timeStr ? '<div class="fmap-seismic-time">' + timeStr + '</div>' : '') +
                        '<div class="fmap-seismic-meta">' +
                        'Depth: ' + ev.depth_km.toFixed(1) + ' km' +
                        (ev.hours_old ? ' · ' + ev.hours_old + 'h ago' : '') +
                        '</div>' +
                        (ev.sig ? '<div class="fmap-seismic-sig">Significance: ' + ev.sig + '</div>' : '') +
                        '<div class="fmap-seismic-source">USGS via ArcGIS Live Feeds</div>' +
                        '</div>',
                        { maxWidth: 260 }
                    );

                    circle.addTo(seismicLayer);
                });

                var bigOnes = events.filter(function (e) { return e.mag >= 5; }).length;
                showToast(events.length + ' earthquake' + (events.length !== 1 ? 's' : '') +
                          (bigOnes ? ' · ' + bigOnes + ' M5+' : ''));
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return;
                console.warn('[fishing-map] seismic fetch failed:', err);
            });
    }

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
                    var icon = L.divIcon({
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
                        '<tr><td>Temp</td><td>' + tempStr + (st.dew_f != null ? ' · Dew ' + st.dew_f + '°' : '') + '</td></tr>' +
                        '<tr><td>Wind</td><td>' + windStr + '</td></tr>' +
                        (st.humidity != null ? '<tr><td>Humidity</td><td>' + st.humidity + '%</td></tr>' : '') +
                        (st.pressure_mb != null ? '<tr><td>Pressure</td><td>' + st.pressure_mb + ' mb</td></tr>' : '') +
                        '<tr><td>Visibility</td><td>' + visStr + '</td></tr>' +
                        (st.sky ? '<tr><td>Sky</td><td>' + st.sky + '</td></tr>' : '') +
                        (st.weather ? '<tr><td>Wx</td><td>' + st.weather + '</td></tr>' : '') +
                        (st.flight_cat ? '<tr><td>Flight cat</td><td><span style="color:' + catColor + ';font-weight:700">' + st.flight_cat + '</span></td></tr>' : '') +
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

    // ─── Day/Night Terminator overlay (ArcGIS Live Feeds) ────────────────────

    function wireTerminatorLayer() {
        if (!map) return;
        terminatorLayer = L.layerGroup();

        var btn = document.getElementById('fmap-terminator-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                terminatorOn = !terminatorOn;
                btn.classList.toggle('fmap-ctrl-btn--active', terminatorOn);
                btn.setAttribute('aria-pressed', terminatorOn ? 'true' : 'false');
                if (terminatorOn) {
                    terminatorLayer.addTo(map);
                    doFetchTerminator();
                    // Refresh every 5 minutes while active
                    terminatorInterval = setInterval(doFetchTerminator, 5 * 60 * 1000);
                } else {
                    map.removeLayer(terminatorLayer);
                    terminatorLayer.clearLayers();
                    clearInterval(terminatorInterval);
                    terminatorInterval = null;
                }
            });
        }
    }

    function doFetchTerminator() {
        if (!terminatorOn || !map) return;
        fetch('/api/map/terminator')
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!terminatorOn || !map || !data || !data.terminator) return;
                var t = data.terminator;
                terminatorLayer.clearLayers();

                var timeStr = '';
                if (t.timestamp) {
                    try { timeStr = new Date(t.timestamp).toLocaleTimeString([], {hour:'numeric', minute:'2-digit', timeZoneName:'short'}); }
                    catch(e) {}
                }

                (t.rings || []).forEach(function (ring) {
                    L.polygon(ring, {
                        color:       '#1e3a5f',
                        fillColor:   '#0f172a',
                        fillOpacity: 0.38,
                        weight:      1,
                        opacity:     0.6,
                        interactive: false,
                    }).addTo(terminatorLayer);
                });

                // Invisible clickable line along the boundary for a popup
                if (t.rings && t.rings[0] && t.rings[0].length) {
                    L.polyline(t.rings[0], { weight: 0, opacity: 0 })
                      .bindPopup(
                        '<div class="fmap-terminator-popup"><strong>Day / Night Boundary</strong>' +
                        (timeStr ? '<br><small>As of ' + timeStr + '</small>' : '') +
                        '<br><small style="opacity:.5">Source: ArcGIS Live Feeds</small></div>',
                        { maxWidth: 200 }
                      )
                      .addTo(terminatorLayer);
                }
            })
            .catch(function (err) {
                console.warn('[fishing-map] terminator fetch failed:', err);
            });
    }

    // ─── Live Stream Gauges overlay (ArcGIS Live Feeds / USGS-NWS) ───────────

    function wireGaugeLayer() {
        if (!map) return;
        gaugeLayer = L.layerGroup();

        var btn = document.getElementById('fmap-gauge-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                gaugeOn = !gaugeOn;
                btn.classList.toggle('fmap-ctrl-btn--active', gaugeOn);
                btn.setAttribute('aria-pressed', gaugeOn ? 'true' : 'false');
                if (gaugeOn) {
                    gaugeLayer.addTo(map);
                    doFetchGauges();
                    map.on('moveend zoomend', onGaugeViewport);
                } else {
                    map.removeLayer(gaugeLayer);
                    gaugeLayer.clearLayers();
                    map.off('moveend zoomend', onGaugeViewport);
                }
            });
        }
    }

    function onGaugeViewport() {
        clearTimeout(gaugeTimer);
        gaugeTimer = setTimeout(doFetchGauges, 600);
    }

    function doFetchGauges() {
        if (!gaugeOn || !map) return;
        if (gaugeAbort) { try { gaugeAbort.abort(); } catch (e) {} }
        gaugeAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/stream-gauges?south=' + b.getSouth().toFixed(4) +
                  '&west='  + b.getWest().toFixed(4) +
                  '&north=' + b.getNorth().toFixed(4) +
                  '&east='  + b.getEast().toFixed(4);

        fetch(url, { signal: gaugeAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!gaugeOn || !map || !data) return;
                gaugeLayer.clearLayers();
                var gauges = data.gauges || [];

                gauges.forEach(function (g) {
                    var color = g.status_color || '#22c55e';
                    var icon  = L.divIcon({
                        className: '',
                        html: '<div class="fmap-gauge-dot" style="background:' + color + '"></div>',
                        iconSize:   [14, 14],
                        iconAnchor: [7, 7],
                    });

                    var stageStr = g.stage_ft != null ? g.stage_ft.toFixed(2) + ' ft' : '–';
                    var flowStr  = g.flow_cfs  != null ? g.flow_cfs.toFixed(0)  + ' cfs' : '–';
                    var updStr   = '';
                    if (g.updated) {
                        try { updStr = new Date(g.updated).toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}); }
                        catch(e) {}
                    }

                    var marker = L.marker([g.lat, g.lng], { icon: icon });
                    marker.bindPopup(
                        '<div class="fmap-gauge-popup">' +
                        '<strong>' + (g.name || g.id) + '</strong>' +
                        '<div class="fmap-gauge-status" style="color:' + color + '">' + g.status + '</div>' +
                        '<table class="fmap-gauge-table">' +
                        '<tr><td>Stage</td><td>' + stageStr + '</td></tr>' +
                        '<tr><td>Flow</td><td>'  + flowStr  + '</td></tr>' +
                        (g.status_24h ? '<tr><td>24 h</td><td>' + g.status_24h + '</td></tr>' : '') +
                        (g.status_48h ? '<tr><td>48 h</td><td>' + g.status_48h + '</td></tr>' : '') +
                        (g.status_72h ? '<tr><td>72 h</td><td>' + g.status_72h + '</td></tr>' : '') +
                        (updStr ? '<tr><td>Updated</td><td>' + updStr + '</td></tr>' : '') +
                        '</table>' +
                        (g.graph_url ? '<a href="' + g.graph_url + '" target="_blank" rel="noopener" class="fmap-gauge-link">View hydrograph ↗</a>' : '') +
                        '<div class="fmap-gauge-source">USGS/NWS via ArcGIS Live Feeds</div>' +
                        '</div>',
                        { maxWidth: 260 }
                    );
                    marker.addTo(gaugeLayer);
                });

                var flooding = gauges.filter(function (g) { return g.status_class >= 2; }).length;
                showToast(gauges.length + ' gauge' + (gauges.length !== 1 ? 's' : '') +
                          (flooding ? ' · ' + flooding + ' flooding' : ''));
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return;
                console.warn('[fishing-map] gauge fetch failed:', err);
            });
    }

    // ─── NOAA Storm Reports overlay (ArcGIS Live Feeds) ──────────────────────

    function wireStormReportsLayer() {
        if (!map) return;
        stormRptLayer = L.layerGroup();

        var btn = document.getElementById('fmap-storm-rpt-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                stormRptOn = !stormRptOn;
                btn.classList.toggle('fmap-ctrl-btn--active', stormRptOn);
                btn.setAttribute('aria-pressed', stormRptOn ? 'true' : 'false');
                if (stormRptOn) {
                    stormRptLayer.addTo(map);
                    doFetchStormReports();
                    map.on('moveend zoomend', onStormRptViewport);
                } else {
                    map.removeLayer(stormRptLayer);
                    stormRptLayer.clearLayers();
                    map.off('moveend zoomend', onStormRptViewport);
                }
            });
        }
    }

    function onStormRptViewport() {
        clearTimeout(stormRptTimer);
        stormRptTimer = setTimeout(doFetchStormReports, 700);
    }

    function doFetchStormReports() {
        if (!stormRptOn || !map) return;
        if (stormRptAbort) { try { stormRptAbort.abort(); } catch (e) {} }
        stormRptAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/storm-reports?south=' + b.getSouth().toFixed(4) +
                  '&west='  + b.getWest().toFixed(4) +
                  '&north=' + b.getNorth().toFixed(4) +
                  '&east='  + b.getEast().toFixed(4);

        fetch(url, { signal: stormRptAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!stormRptOn || !map || !data) return;
                stormRptLayer.clearLayers();
                var reports = data.reports || [];

                var ICONS = { hail: '🌨', tornado: '🌪', wind: '💨' };
                reports.forEach(function (rpt) {
                    var color = rpt.color || '#facc15';
                    var icon  = L.divIcon({
                        className: '',
                        html: '<div class="fmap-storm-rpt-dot" style="background:' + color + '">' +
                              (ICONS[rpt.type] || '⚡') + '</div>',
                        iconSize:   [22, 22],
                        iconAnchor: [11, 11],
                    });

                    var timeStr = '';
                    if (rpt.time) {
                        try { timeStr = new Date(rpt.time).toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit', timeZoneName:'short'}); }
                        catch(e) {}
                    }

                    var marker = L.marker([rpt.lat, rpt.lng], { icon: icon });
                    marker.bindPopup(
                        '<div class="fmap-storm-rpt-popup">' +
                        '<strong>' + (ICONS[rpt.type] || '') + ' ' +
                        rpt.type.charAt(0).toUpperCase() + rpt.type.slice(1) +
                        (rpt.magnitude ? ' · ' + rpt.magnitude : '') + '</strong>' +
                        (rpt.location ? '<div>' + rpt.location + (rpt.state ? ', ' + rpt.state : '') + '</div>' : '') +
                        (timeStr ? '<div class="fmap-storm-rpt-time">' + timeStr + '</div>' : '') +
                        (rpt.comments ? '<div class="fmap-storm-rpt-comments">' + rpt.comments + '</div>' : '') +
                        '<div class="fmap-storm-rpt-source">NOAA via ArcGIS Live Feeds · 24h</div>' +
                        '</div>',
                        { maxWidth: 260 }
                    );
                    marker.addTo(stormRptLayer);
                });

                var types = {};
                reports.forEach(function (r) { types[r.type] = (types[r.type] || 0) + 1; });
                var summary = Object.keys(types).map(function (t) {
                    return types[t] + ' ' + t;
                }).join(', ');
                showToast(reports.length
                    ? 'Storm reports: ' + summary
                    : 'No storm reports in past 24 h');
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return;
                console.warn('[fishing-map] storm reports fetch failed:', err);
            });
    }

    // ─── Recent Hurricane Tracks overlay (ArcGIS Live Feeds) ─────────────────

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
            (t.basin ? ' <small style="opacity:.6">(' + esc(t.basin) + ')</small>' : '') +
            '<br><em>' + esc(t.category) + '</em>' +
            (dates ? '<br><small>' + dates + '</small>' : '') +
            '<br><small style="opacity:.5">Source: NHC/JTWC via ArcGIS Live Feeds</small>' +
            '</div>'
        );
    }

    // ─── Marine Warnings overlay (ArcGIS Live Feeds) ─────────────────────────

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
                } else {
                    map.removeLayer(marineWarnLayer);
                    marineWarnLayer.clearLayers();
                }
            });
        }

        map.on('moveend zoomend', function () {
            if (marineWarnOn) scheduleMarineWarnFetch();
        });
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
            '<br><small style="opacity:.6">Source: NHC/JTWC via ArcGIS Live Feeds</small>' +
            '</div>'
        );
    }

    function _updateStormBadge(count) {
        var badge = document.getElementById('fmap-storm-badge');
        if (!badge) return;
        badge.textContent = count;
        badge.style.display = count > 0 ? '' : 'none';
    }

    // ─── Layers popup panel ───────────────────────────────────────────────────
    // IDs of all layer-row buttons inside the popup (must match the HTML ids).
    var LAYER_BTN_IDS = [
        'fmap-marine-warn-btn', 'fmap-storm-tracker-btn', 'fmap-recent-storms-btn',
        'fmap-storm-rpt-btn', 'fmap-sst-btn', 'fmap-sea-ice-btn',
        'fmap-wildfire-btn', 'fmap-seismic-btn', 'fmap-metar-btn',
        'fmap-gauge-btn', 'fmap-terminator-btn'
    ];
    var LS_LAYERS_KEY   = 'fmap_layers_v1';
    var LS_SECTIONS_KEY = 'fmap_sections_v1'; // stores array of collapsed section ids

    // Map from section data-section value → layer button IDs it contains
    var SECTION_LAYER_MAP = {
        weather: ['fmap-marine-warn-btn', 'fmap-storm-tracker-btn',
                  'fmap-recent-storms-btn', 'fmap-storm-rpt-btn'],
        ocean:   ['fmap-sst-btn', 'fmap-sea-ice-btn',
                  'fmap-wildfire-btn', 'fmap-seismic-btn'],
        obs:     ['fmap-metar-btn', 'fmap-gauge-btn', 'fmap-terminator-btn']
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
        var triggerBtn = document.getElementById('fmap-layers-popup-btn');
        var popup      = document.getElementById('fmap-layers-popup');
        var closeBtn   = document.getElementById('fmap-layers-popup-close');
        var clearBtn   = document.getElementById('fmap-layers-clear-btn');
        if (!triggerBtn || !popup) return;

        var _closeTimer = null;

        function openPopup() {
            clearTimeout(_closeTimer);
            popup.classList.remove('fmap-layers-popup--closing');
            popup.hidden = false;
            triggerBtn.classList.add('fmap-ctrl-btn--active');
            triggerBtn.setAttribute('aria-pressed', 'true');
            // Move focus to first layer row for keyboard users
            var firstRow = popup.querySelector('.fmap-layer-row, .fmap-layers-section-hdr');
            if (firstRow) firstRow.focus();
        }

        function closePopup() {
            popup.classList.add('fmap-layers-popup--closing');
            _closeTimer = setTimeout(function () {
                popup.hidden = true;
                popup.classList.remove('fmap-layers-popup--closing');
            }, 140); // matches fmap-layers-out duration
            triggerBtn.classList.remove('fmap-ctrl-btn--active');
            triggerBtn.setAttribute('aria-pressed', 'false');
            triggerBtn.focus(); // return focus to trigger
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

        // Escape key closes popup
        document.addEventListener('keydown', function (e) {
            if (!popup.hidden && (e.key === 'Escape' || e.keyCode === 27)) {
                closePopup();
            }
        });

        // Close when clicking outside the popup or trigger button
        document.addEventListener('click', function (e) {
            if (popup.hidden) return;
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

        // ── Per-row: loading shimmer, badge refresh, state persistence ───────
        LAYER_BTN_IDS.forEach(function (id) {
            var btn = document.getElementById(id);
            if (!btn) return;
            btn.addEventListener('click', function () {
                // Run after the wire*Layer handler has flipped aria-pressed
                setTimeout(function () {
                    _updateLayersBadge();
                    _saveLayerState();
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
    // Layers are staggered 350 ms apart (starting 700 ms after boot) so the
    // main fetchAndRender() and tile loads get network priority first.
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
                renderQuickChips();
                wireFilters();
                wireMapControls();
                wireAdvancedFilters();
                wireSpotTypeFilters();
                wireTabs();
                wireCommunityLayer();
                wireLogCatch();
                wireFullscreen();
                wireShareBtn();
                wireAdminMode();
                wireLayersPopup();
                wireSstLayer();
                wireWildfireLayer();
                wireSeaIceLayer();
                wireSeismicLayer();
                wireMetarLayer();
                wireTerminatorLayer();
                wireGaugeLayer();
                wireStormReportsLayer();
                wireRecentStorms();
                wireMarineWarnings();
                wireStormTracker();
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
            if (activeCat)    hashParts.push('cat=' + activeCat);
            if (activeMonth)  hashParts.push('month=' + activeMonth);
            if (activeSeason)   hashParts.push('season=' + activeSeason);
            if (activeTime)     hashParts.push('time=' + activeTime);
            if (activeTide)     hashParts.push('tide=' + activeTide);
            if (activeMinTemp)  hashParts.push('min_temp=' + activeMinTemp);
            if (activeMaxTemp)  hashParts.push('max_temp=' + activeMaxTemp);
            if (activeSpotTypes.length) hashParts.push('types=' + activeSpotTypes.slice().sort().join(','));
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
            if (k === 'season' && v) {
                activeSeason = v;
                document.querySelectorAll('.fmap-pill--season').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-season') === v);
                });
            }
            if (k === 'time' && v) {
                activeTime = v;
                document.querySelectorAll('.fmap-pill--time').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-time') === v);
                });
            }
            if (k === 'tide' && v) {
                activeTide = v;
                document.querySelectorAll('.fmap-pill--tide').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-tide') === v);
                });
            }
            if (k === 'min_temp' && v) {
                activeMinTemp = v;
                var minEl = document.getElementById('fmap-min-temp');
                if (minEl) minEl.value = v;
            }
            if (k === 'max_temp' && v) {
                activeMaxTemp = v;
                var maxEl = document.getElementById('fmap-max-temp');
                if (maxEl) maxEl.value = v;
            }
            if (k === 'types' && v) {
                var requested = v.split(',').map(function (t) { return t.trim(); })
                                 .filter(function (t) { return t && SPOT_TYPES[t]; });
                if (requested.length) _applySpotTypeUI(requested);
            }
        });
        updateAdvBadge();
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
        els.hotspotsList   = document.getElementById('fmap-hotspots-list');
        els.hotspotCount   = document.getElementById('fmap-hotspot-count');
        els.chips          = document.getElementById('fmap-chips');
        els.speciesInput   = document.getElementById('fmap-species-input');
        els.searchClear    = document.getElementById('fmap-search-clear');
        els.suggestions    = document.getElementById('fmap-suggestions');
        els.insight        = document.getElementById('fmap-insight');
        els.insightText    = document.getElementById('fmap-insight-text');
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
        // Community feed
        els.communityList  = document.getElementById('fmap-community-list');

        if (!els.mapEl) return;
        boot();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
