(function () {
    'use strict';

    // ─── Config ───────────────────────────────────────────────────────────────
    var API_URL = '/api/fishing-map';

    var DEFAULT_CENTER = [37.5, -96.0];
    var DEFAULT_ZOOM   = 4;

    // ─── State ────────────────────────────────────────────────────────────────
    var map           = null;
    var mapReady      = false;
    var allSpecies    = [];          // species name strings for autocomplete
    var allSpeciesLower = [];        // pre-lowercased mirror of allSpecies — avoids .toLowerCase() on every keystroke
    var currentData   = [];          // last API response locations
    var fetchTimer    = null;
    var activeSpecies = '';
    var isFullscreen  = false;

    var fishingSpotLayer = null;     // L.layerGroup for structure markers
    var spotQueryTimer   = null;     // debounce timer for structure queries
    var spotCache        = {};       // bbox+types key → array of spot objects
    var _spotCacheKeys   = [];       // insertion-ordered keys for LRU eviction
    var _SPOT_CACHE_MAX  = 48;       // cap to prevent unbounded sessionStorage growth
    var _ssSaveTimer          = null; // debounce timer for sessionStorage writes
    var _lastRenderedSpotKey  = null; // cache key of the last renderFishingSpots() call
    var _elStructFiltersHint  = null; // cached DOM ref — fmap-struct-filters-hint
    var _elSpotTypesClear     = null; // cached DOM ref — fmap-spot-types-clear
    var _spotIconCache        = {};   // type → L.divIcon; icons are immutable so one per type
    // Shared cache for overlay icons (SST, gauge, AQI, METAR, buoy, storm report).
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
    var _structLoadCount     = 0;    // pending /api/map/structures requests (spinner ref-count)
    var _structReqGen        = 0;    // monotonic counter; stale completions are discarded
    var _structAbort         = null; // AbortController for the live structure fetch
    var _mainAbort           = null; // AbortController for the in-flight /api/fishing-map fetch
    var _communityAbort      = null; // AbortController for the in-flight /api/map/catches fetch
    var aiPickLayer      = null;     // L.layerGroup for AI habitat picks
    var aiQueryTimer     = null;     // debounce timer for AI habitat queries
    var aiCache          = {};       // bbox-key+species → array of habitat features
    var _aiCacheKeys     = [];       // insertion-ordered keys for LRU eviction
    var _AI_CACHE_MAX    = 64;       // cap so heavy sessions don't leak memory
    var _aiReqGen        = 0;        // monotonic counter; stale AI completions are discarded
    var _aiAbort         = null;     // AbortController for the live AI habitat fetch

    // ─── Community / social state ─────────────────────────────────────────────
    var communityLayerOn  = false;   // whether community pins are visible
    var communityLayer    = null;    // L.layerGroup for community catch pins
    var communityData     = [];      // [{id,lat,lng,species,…}]
    var communityTimer    = null;    // debounce for community fetch on move
    var catchLogMode      = false;   // user is placing a catch pin
    var pendingCatchLatLng = null;   // {lat,lng} for the log modal
    var pendingCatchMarker = null;   // temporary L.marker shown before submit
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

    // ─── New overlay state (AQI, Drought, Precipitation, NDBC Buoys) ────────────
    var aqiOn           = false;   // AQI/PM2.5 stations overlay active
    var aqiLayer        = null;    // L.layerGroup for AQI markers
    var aqiTimer        = null;    // debounce for viewport reload
    var droughtOn       = false;   // US Drought Monitor overlay active
    var droughtLayer    = null;    // L.layerGroup for drought polygons
    var droughtTimer    = null;    // debounce for viewport reload
    var precipOn        = false;   // NDFD Precipitation overlay active
    var precipLayer     = null;    // L.layerGroup for precip polygons
    var precipTimer     = null;    // debounce for viewport reload
    var ndfdTempOn      = false;   // NDFD Temperature overlay active
    var ndfdTempLayer   = null;    // L.layerGroup for temperature polygons
    var ndfdTempTimer   = null;    // debounce for viewport reload
    var buoyOn          = false;   // NDBC buoy overlay active
    var buoyLayer       = null;    // L.layerGroup for buoy markers
    var buoyTimer       = null;    // debounce for viewport reload

    // Per-layer AbortControllers — cancel in-flight requests when viewport changes
    var sstAbort        = null;
    var wildfireAbort   = null;
    var seismicAbort    = null;
    var metarAbort      = null;
    var gaugeAbort      = null;
    var stormRptAbort   = null;
    var marineWarnAbort = null;
    var aqiAbort        = null;
    var droughtAbort    = null;
    var precipAbort     = null;
    var ndfdTempAbort   = null;
    var buoyAbort       = null;

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
            hasAutoZoomed = true; // don't let autoZoomToSavedLocation reset the view
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
            // Fall back to street tiles if ESRI is unavailable
            map.removeLayer(activeTileLayer);
            activeTileLayer = L.tileLayer(TILE_STREET.url, TILE_STREET.opts).addTo(map);
        });
        activeTileLayer.addTo(map);

        // Layer groups — render order: AI picks → OSM structures
        aiPickLayer      = L.layerGroup().addTo(map);
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
                'node["natural"="beach"]',
                'node["natural"="shoal"]',
                'way["natural"="shoal"]',
                'node["natural"="sandbank"]',
                'way["natural"="sandbank"]',
                'node["seamark:type"="rock_awash"]',
                'node["seamark:type"="rock_submerged"]'
            ],
            color:   '#fde68a',
            insight: 'Surf species work the wash zone along sandy beaches. Focus on troughs and cuts behind sandbars — the water digs deeper in those spots and concentrates bait. Fish low-light edges of the trough and any rip current that breaks through a sandbar.'
        },
        mangrove: {
            tags: [
                'way["natural"="wetland"]["wetland"="mangrove"]',
                'way["waterway"="tidal_channel"]',
                'way["waterway"="stream"]["tidal"="yes"]',
                'way["waterway"="drain"]["tidal"="yes"]'
            ],
            color:   '#22c55e',
            insight: 'Mangrove species ambush prey along root edges and tidal creek mouths. Work falling tides at pinch points — culverts, bends, and channel exits where bait gets squeezed out. Snook and tarpon stage at creek mouths on outgoing tide; push into the roots on the flood.'
        },
        grassflat: {
            tags: [
                'way["natural"="wetland"]["wetland"="seagrass"]',
                'way["natural"="wetland"]["wetland"="saltmarsh"]',
                'node["natural"="wetland"]["wetland"="seagrass"]',
                'way["waterway"="tidal_channel"]',
                'way["natural"="shoal"]'
            ],
            color:   '#34d399',
            insight: 'Grass-flat species patrol the edges where seagrass or marsh meets deeper water. Dawn topwater bites happen on shallow flats; mid-day fish slide to channel edges and drop-offs. Fish current-swept grass points and any pothole (sandy opening) in dense grass beds.'
        },
        estuary: {
            tags: [
                'way["natural"="wetland"]["wetland"="saltmarsh"]',
                'way["waterway"="tidal_channel"]',
                'node["natural"="shoal"]',
                'way["natural"="wetland"]["wetland"="tidalflat"]',
                'node["natural"="wetland"]["wetland"="tidalflat"]',
                'way["landuse"="aquaculture"]["produce"="oyster"]',
                'way["landuse"="aquaculture"]["product"="oysters"]',
                'node["seamark:type"="beacon_lateral"]',
                'node["seamark:type"="buoy_lateral"]'
            ],
            color:   '#2dd4bf',
            insight: 'Estuary species follow bait in and out with tidal flow. Key spots: channel bends, creek mouths, oyster bars, and shallow flat edges adjacent to deeper water. Falling tides concentrate everything at the exits — position at the creek mouth and let the current deliver the bait.'
        },
        reef: {
            tags: [
                'way["natural"="reef"]',
                'node["natural"="reef"]',
                'node["natural"="shoal"]',
                'node["seamark:type"="wreck"]',
                'node["historic"="wreck"]',
                'way["seamark:type"="wreck"]',
                'way["historic"="wreck"]',
                'node["seamark:type"="artificial_reef"]',
                'node["seamark:type"="obstruction"]',
                'node["man_made"="pier"]["access"!="private"]',
                'node["man_made"="jetty"]',
                'node["seamark:type"="rock_awash"]'
            ],
            color:   '#f59e0b',
            insight: 'Reef and structure species hold on hard bottom — rocky reefs, pinnacles, wrecks, and pier pilings. Fish the upcurrent edge where bait gets swept against structure. Drop-shot or deep jig on the uptide face; drift live bait across the downtide shadow.'
        },
        bottom: {
            tags: [
                'node["natural"="shoal"]',
                'way["natural"="shoal"]',
                'node["natural"="sandbank"]',
                'way["natural"="sandbank"]',
                'way["waterway"="tidal_channel"]',
                'way["natural"="wetland"]["wetland"="tidalflat"]',
                'node["natural"="wetland"]["wetland"="tidalflat"]'
            ],
            color:   '#fb923c',
            insight: 'Bottom feeders work sandy or muddy substrate near structure transitions. Channel edges adjacent to flats are prime ambush zones — fish depth changes with a slow bottom presentation. Look for where hard substrate meets soft mud; that seam concentrates prey.'
        },
        general: {
            tags: [
                'way["natural"="reef"]',
                'node["natural"="reef"]',
                'node["natural"="shoal"]',
                'way["natural"="wetland"]["wetland"="saltmarsh"]',
                'way["waterway"="tidal_channel"]',
                'node["man_made"="pier"]["access"!="private"]',
                'node["man_made"="breakwater"]'
            ],
            color:   '#a78bfa',
            insight: 'Fish concentrate where structure meets current — reef edges, channel bends, shoal drop-offs, and marsh creek mouths. These highlighted areas offer the best natural ambush opportunities in the current view.'
        }
    };

    // Infer habitat type from species name, bait, rig, and lures text.
    function inferHabitatType(meta) {
        var name = (meta.name || '').toLowerCase();
        var text = [meta.bait || '', meta.rig || '', meta.lures || ''].join(' ').toLowerCase();
        var all  = name + ' ' + text;

        // Pelagic / offshore species — check name first for strong signals
        if (/\b(marlin|sailfish|wahoo|mahi|dorado|yellowfin|bluefin|skipjack|albacore|false\s*albacore|little\s*tunny|bonito|spanish\s*mackerel|king\s*mackerel|kingfish\s*mac|cobia\s*(offshore|troll)|permit\s*offshore)\b/.test(name) ||
            /troll|offshore|blue\s*water|open\s*ocean|spreader\s*bar|ballyhoo|cedar\s*plug|feather|kona\s*head/.test(text)) {
            return 'pelagic';
        }
        // Surf / beach species
        if (/\b(pompano|whiting|kingfish|surf\s*perch|surfperch|barred\s*perch|corbina|spotfin\s*croaker|yellowfin\s*croaker|pismo\s*croaker|striped\s*bass.*surf|bluefish.*surf)\b/.test(name) ||
            /sand\s*(crab|flea)|mole\s*crab|pompano\s*jig|surf\s*(rod|cast|fish)/.test(text)) {
            return 'surf';
        }
        // Mangrove specialists
        if (/\b(snook|common\s*snook|tarpon|atlantic\s*tarpon|baby\s*tarpon|jack\s*crevalle|mangrove\s*snapper|gray\s*snapper)\b/.test(name) ||
            /mangrove/.test(all)) {
            return 'mangrove';
        }
        // Grass flat / seagrass species
        if (/\b(spotted\s*sea\s*trout|speckled\s*trout|seatrout|bonefish|permit|redfish|red\s*drum|puppy\s*drum)\b/.test(name) ||
            /popping[- ]?cork|grass\s*flat|seagrass|over\s*(grass|flat)|shrimp.*cork|cork.*shrimp/.test(text)) {
            return 'grassflat';
        }
        // Estuary / inshore tidal species
        if (/\b(weakfish|gray\s*trout|flounder|southern\s*flounder|summer\s*flounder|fluke|black\s*drum|sheepshead|drum|croaker|atlantic\s*croaker|spot\s*fish|white\s*perch|striped\s*bass.*inshore|white\s*bass|hybrid\s*striped)\b/.test(name) ||
            /marsh|tidal\s*(creek|channel)|estuar|finger\s*mullet|live\s*shrimp|cut\s*(menhaden|mullet)/.test(text)) {
            return 'estuary';
        }
        // Reef / structure species
        if (/\b(grouper|snapper|amberjack|tautog|blackfish|cunner|sea\s*bass|black\s*sea\s*bass|rockfish|lingcod|cabezon|kelp\s*bass|calico\s*bass|gopher\s*rockfish|copper\s*rockfish|hogfish|triggerfish|wreckfish|cobia|tripletail|yellowtail|almaco|greater\s*amber)\b/.test(name) ||
            /reef|rock\s*(fish|cod)|kelp|wreck|structure|bucktail|dropper\s*loop|hi[- ]?lo|jig.*reef|piling|bridge|dock/.test(all)) {
            return 'reef';
        }
        // Bottom feeders
        if (/\b(catfish|channel\s*catfish|flathead\s*catfish|halibut|pacific\s*halibut|atlantic\s*halibut|skate|ray|stingray|sand\s*shark|smooth\s*dogfish|spiny\s*dogfish|cusk)\b/.test(name) ||
            /bottom\s*rig|egg\s*sinker|fish\s*finder|pyramid\s*sinker|spreader\s*rig|sinker.*bottom/.test(text)) {
            return 'bottom';
        }
        // Inlet / channel species (not already caught above)
        if (/inlet|channel|current\s*seam/.test(text)) {
            return 'estuary';
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

    function makeAIPickIcon(habitatType) {
        var def   = HABITAT_DEFS[habitatType] || HABITAT_DEFS.general;
        var html  = '<span class="fmap-ai-dot" style="--ai-c:' + def.color + '"></span>';
        return L.divIcon({ className: 'fmap-ai-wrap', html: html, iconSize: [16, 16], iconAnchor: [8, 8] });
    }

    var currentSpeciesMeta = null;

    function renderAIHabitatSpots(features, habitatType) {
        if (!aiPickLayer) return;
        aiPickLayer.clearLayers();

        var tipLabel = habitatType === 'general' ? 'Habitat' : 'AI Pick';
        features.forEach(function (f) {
            if (!f.lat || !f.lng) return;
            var tipCfg = HABITAT_TYPE_LABELS[f.osmType] || { tip: 'Habitat feature' };
            var m      = L.marker([f.lat, f.lng], { icon: makeAIPickIcon(habitatType) });
            var name   = f.name ? '<strong>' + esc(f.name) + '</strong><br>' : '';
            m.bindTooltip(
                '<span class="fmap-ai-tip-label">' + tipLabel + '</span>' + name +
                '<span style="opacity:.8">' + esc(tipCfg.tip) + '</span>',
                { className: 'fmap-tooltip fmap-ai-tooltip', direction: 'top', offset: [0, -7] }
            );
            aiPickLayer.addLayer(m);
        });
    }

    function _updateHabitatInsight(habitatType, def) {
        var el = document.getElementById('fmap-habitat-insight');
        if (!el) return;
        if (habitatType === 'general' || !def || !def.insight) {
            el.hidden = true;
            return;
        }
        el.textContent = def.insight;
        el.hidden = false;
    }

    function queryAIHabitatSpots() {
        if (!map || !aiPickLayer) return;

        var habitatType = (activeSpecies && currentSpeciesMeta)
            ? inferHabitatType(currentSpeciesMeta)
            : 'general';
        var def         = HABITAT_DEFS[habitatType];

        _updateHabitatInsight(habitatType, def);

        // Pelagic / open-water: no OSM markers to place
        if (!def || !def.tags.length) {
            aiPickLayer.clearLayers();
            return;
        }

        if (map.getZoom() < 10) {
            aiPickLayer.clearLayers();
            return;
        }

        var b   = map.getBounds();
        var s   = Math.floor(b.getSouth() * 4) / 4;
        var w   = Math.floor(b.getWest()  * 4) / 4;
        var n   = Math.ceil(b.getNorth()  * 4) / 4;
        var e   = Math.ceil(b.getEast()   * 4) / 4;
        var key = habitatType + '|' + s + ',' + w + ',' + n + ',' + e;

        if (aiCache[key]) { renderAIHabitatSpots(aiCache[key], habitatType); return; }

        // Abort any in-flight AI habitat fetch before starting the new one.
        if (_aiAbort) _aiAbort.abort();
        _aiAbort = new AbortController();
        var thisAiGen = ++_aiReqGen;

        var url = '/api/v1/geo/habitats?south=' + s + '&west=' + w +
                  '&north=' + n + '&east=' + e +
                  '&habitat_type=' + encodeURIComponent(habitatType);

        fetch(url, { signal: _aiAbort.signal })
        .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function (data) {
            if (thisAiGen !== _aiReqGen) return;
            var features = ((data.data && data.data.features) || []).map(function (f) {
                return { lat: f.lat, lng: f.lng, name: f.name || '', osmType: f.osm_type || 'general' };
            });
            _aiCachePut(key, features);
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

    // LRU-bounded write for aiCache — mirrors _spotCachePut().
    function _aiCachePut(key, data) {
        if (!Object.prototype.hasOwnProperty.call(aiCache, key)) {
            if (_aiCacheKeys.length >= _AI_CACHE_MAX) {
                delete aiCache[_aiCacheKeys.shift()];
            }
            _aiCacheKeys.push(key);
        }
        aiCache[key] = data;
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
        shoal:        { label: 'Shoal',             color: '#94a3b8', habitat: false, minZoom: 10 },
        point:        { label: 'Point / Headland',  color: '#60a5fa', habitat: false, minZoom: 9  },
        beach:        { label: 'Beach / Surf Zone', color: '#fbbf24', habitat: false, minZoom: 9  },
        grass_flat:   { label: 'Grass Flat',        color: '#22c55e', habitat: true,  minZoom: 9  },
        tidal_flat:   { label: 'Tidal Flat',        color: '#6ee7b7', habitat: true,  minZoom: 9  },
        saltmarsh:    { label: 'Saltmarsh Edge',    color: '#34d399', habitat: true,  minZoom: 9  },
        mangrove:     { label: 'Mangrove',          color: '#16a34a', habitat: true,  minZoom: 9  },
        kelp:         { label: 'Kelp Forest',       color: '#4ade80', habitat: true,  minZoom: 9  },
        buoy:         { label: 'Navigation Buoy',   color: '#f43f5e', habitat: false, minZoom: 10 },
        fishing:      { label: 'Fishing Spot',      color: '#2dd4bf', habitat: false, minZoom: 9  },
        fishing_shop: { label: 'Bait & Tackle',     color: '#fb923c', habitat: false, minZoom: 11 },
        boat_ramp:    { label: 'Boat Ramp',         color: '#0ea5e9', habitat: false, minZoom: 10 },
        dive_site:    { label: 'Dive Site',         color: '#0284c7', habitat: false, minZoom: 10 },
        seawall:      { label: 'Seawall',           color: '#6b7280', habitat: false, minZoom: 11 }
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

    // Symbols rendered inside structure markers — chosen to visually suggest the feature type
    var SPOT_LABELS = {
        pier:         '⊥',   // T/dock shape from above
        jetty:        '≡',   // stacked lines = rock armour
        bridge:       '∩',   // arch = bridge span
        reef:         '≈',   // wavy = underwater relief
        oyster_reef:  '◌',   // open ring = shell cluster
        wreck:        '✕',   // X = hazard / charted wreck
        inlet:        '⇢',   // arrow = tidal flow
        marina:       '⚓',   // anchor = marina/harbor
        shoal:        '〜',   // wave = shallow break
        point:        '△',   // triangle = headland jutting out
        beach:        '∿',   // sine wave = surf break
        buoy:         '◎',   // bullseye = channel buoy
        fishing:      '✦',   // star = access point
        fishing_shop: '⚙',   // gear = tackle & bait
        boat_ramp:    '▽',   // inverted triangle = ramp into water
        dive_site:    '✚',   // cross = dive-flag reference
        seawall:      '▬'    // bar = wall face
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
        // Habitat = rotating diamond (no letter); Structure = circle with type letter
        var sz    = isHabitat ? 17 : 22;
        var br    = isHabitat ? '3px' : '50%';
        var rot   = isHabitat ? 'transform:rotate(45deg)' : '';
        var lbl   = isHabitat ? '' : (SPOT_LABELS[type] || '');
        var inner = lbl
            ? '<span style="font-size:13px;font-weight:400;color:rgba(255,255,255,0.97);' +
              'font-family:system-ui,\'Segoe UI Symbol\',\'Apple Symbols\',sans-serif;' +
              'line-height:1;pointer-events:none;">' + lbl + '</span>'
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

        // Build a render key that folds in current viewport bounds (at ~5 km
        // resolution) so panning within the same 0.5° cache grid still triggers
        // a re-render — the viewport-culled subset changes even though the
        // cached data doesn't.
        var vb = map ? map.getBounds() : null;
        var vbKey = vb
            ? (Math.floor(vb.getSouth() * 20) + ',' + Math.floor(vb.getWest()  * 20) + ',' +
               Math.ceil (vb.getNorth() * 20) + ',' + Math.ceil (vb.getEast()  * 20))
            : '';
        var currentZoom = map ? Math.floor(map.getZoom()) : 8;
        var renderKey = (cacheKey || '') + ':' + vbKey + ':z' + currentZoom;

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
            var latPad = (vb.getNorth() - vb.getSouth()) * 0.10;
            var lngPad = (vb.getEast()  - vb.getWest())  * 0.10;
            vS = vb.getSouth() - latPad;  vN = vb.getNorth() + latPad;
            vW = vb.getWest()  - lngPad;  vE = vb.getEast()  + lngPad;
            doCull = true;
        }

        // Track types whose minZoom exceeds the current zoom so we can hint.
        var _suppressedTypes = {};

        // Render OSM / NOAA spots first
        spots.filter(function (f) {
            if (f.custom) return false;
            // Hide types that require a higher zoom level than current.
            var typeDef = SPOT_TYPES[f.type];
            if (typeDef && typeDef.minZoom && currentZoom < typeDef.minZoom) {
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
            var tip  = f.tip || STRUCTURE_TIPS[f.type] || '';
            var srcLabel = f.source === 'noaa' ? 'NOAA ENC' : 'OpenStreetMap';
            var coordStr = f.lat && f.lng
                ? (Math.round(f.lat * 10000) / 10000) + ', ' + (Math.round(f.lng * 10000) / 10000)
                : '';
            var sym = SPOT_LABELS[f.type] || '';
            var tooltipHtml =
                '<strong>' + esc(name) + '</strong>' +
                '<br><span style="opacity:0.75;font-size:0.7rem">' +
                (sym ? '<span style="font-family:system-ui,\'Segoe UI Symbol\',\'Apple Symbols\',sans-serif;margin-right:3px">' + sym + '</span>' : '') +
                esc(spotTypeLabel(f.type)) + '</span>' +
                (tip ? '<br><span class="fmap-struct-tip">' + esc(tip) + '</span>' : '') +
                '<br><span style="opacity:0.45;font-size:0.65rem;margin-top:2px;display:block">' +
                esc(srcLabel) + (coordStr ? ' · ' + coordStr : '') + '</span>';

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
        // Build an O(1) lookup object once so the filter below doesn't use indexOf
        var requestedSet = null;
        if (requestedTypes) {
            requestedSet = {};
            for (var i = 0; i < requestedTypes.length; i++) requestedSet[requestedTypes[i]] = true;
        }
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
            if (!ktype && requestedSet) {
                // Cached all-types entry; filter client-side — zero server round-trip
                return spotCache[k].filter(function (sp) {
                    return requestedSet[sp.type] === true;
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
            // hint is updated inside renderFishingSpots via _updateZoomSuppressedHint
            return;
        }

        // Check whether a previously fetched (wider) bbox already contains
        // this viewport — e.g. the home-corridor pre-fetch covers zoom-12
        // viewport queries without a second Overpass trip.
        var superResult = _cachedSupersetOf(s, w, n, e, typesStr);
        if (superResult) {
            _spotCachePut(key, superResult);  // alias so next pan hits directly
            renderFishingSpots(superResult, key);
            // hint is updated inside renderFishingSpots via _updateZoomSuppressedHint
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
                _spotCachePut(key, spots);
                _ssSave();
                renderFishingSpots(spots, key);
                // hint is updated inside renderFishingSpots via _updateZoomSuppressedHint
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
            if (wetland === 'kelp')      return 'kelp';
            return null;
        }
        if (natural === 'kelp')      return 'kelp';
        if (natural === 'mud')       return 'tidal_flat';
        if (natural === 'beach')     return 'beach';
        if (natural === 'bay')       return 'inlet';
        if (natural === 'reef')      return 'reef';
        if (natural === 'shoal' || natural === 'rock' || natural === 'sandbank') return 'shoal';
        if (natural === 'cape' || natural === 'headland' ||
            natural === 'peninsula' || natural === 'promontory') return 'point';
        if (natural === 'sand' && tags.access !== 'private' && tags.access !== 'no') return 'beach';

        if (tags.harbour === 'yes') return 'inlet';

        if (tags.landuse === 'aquaculture' &&
            (tags.produce === 'oyster' || tags.product === 'oysters')) {
            return 'oyster_reef';
        }

        if (tags.historic === 'wreck' || seamark === 'wreck') return 'wreck';

        if (seamark === 'rock_awash' || seamark === 'underwater_rock' ||
            seamark === 'rock_submerged' || seamark === 'obstruction') return 'shoal';
        if (seamark === 'artificial_reef' || tags.landuse === 'artificial_reef') return 'reef';
        if ((seamark && seamark.indexOf('beacon') === 0) ||
            seamark === 'light_major' || seamark === 'light_minor') return 'buoy';

        if (waterway === 'tidal_channel' || waterway === 'river' ||
            waterway === 'canal'         || waterway === 'stream') return 'inlet';
        if (waterway === 'weir'      || waterway === 'dam'       ||
            waterway === 'waterfall' || waterway === 'rapids'    ||
            waterway === 'fish_pass' || waterway === 'lock') return 'jetty';

        if (manMade === 'pier' || tags.leisure === 'pier') {
            if (tags.access === 'private' || tags.access === 'no') return null;
            return 'pier';
        }
        if (manMade === 'jetty')                              return 'jetty';
        if (manMade === 'groyne' || manMade === 'breakwater') return 'jetty';
        if (manMade === 'seawall' || manMade === 'revetment') return 'seawall';
        if (manMade === 'lighthouse' || manMade === 'offshore_platform') return 'point';
        if (manMade === 'buoy')                               return 'buoy';

        if (tags.bridge === 'yes' && tags.highway) return 'bridge';

        if (tags.amenity === 'marina' || tags.leisure === 'marina') return 'marina';
        if (tags.amenity === 'boat_ramp' || tags.leisure === 'slipway') return 'boat_ramp';
        if (tags.leisure === 'fishing' || tags.leisure === 'fishing_stand') return 'fishing';
        if (tags.sport === 'scuba_diving' || tags.sport === 'diving') return 'dive_site';
        if (tags.sport === 'fishing') return 'fishing';
        if (tags.fishing === 'yes' && tags.amenity !== 'boat_ramp' && tags.leisure !== 'slipway') return 'fishing';

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
            h.push('way["natural"="beach"](' + bbox + ');',
                   'node["natural"="beach"](' + bbox + ');',
                   'way["natural"="sand"]["access"!="private"](' + bbox + ');');
        }
        if (has('oyster_reef')) {
            h.push('node["landuse"="aquaculture"]["produce"="oyster"](' + bbox + ');',
                   'way["landuse"="aquaculture"]["produce"="oyster"](' + bbox + ');',
                   'way["landuse"="aquaculture"]["product"="oysters"](' + bbox + ');');
        }
        if (has('kelp')) {
            h.push('way["natural"="wetland"]["wetland"="kelp"](' + bbox + ');',
                   'way["natural"="kelp"](' + bbox + ');');
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
                   'way["natural"="reef"](' + bbox + ');',
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
                   'node["leisure"="pier"]["access"!="private"]["access"!="no"](' + bbox + ');',
                   'way["leisure"="pier"]["access"!="private"]["access"!="no"](' + bbox + ');');
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
                   'node["natural"="promontory"](' + bbox + ');',
                   'node["man_made"="lighthouse"](' + bbox + ');',
                   'node["man_made"="offshore_platform"](' + bbox + ');');
        }
        if (has('fishing')) {
            s.push('node["leisure"="fishing"](' + bbox + ');',
                   'way["leisure"="fishing"](' + bbox + ');',
                   'node["leisure"="fishing_stand"](' + bbox + ');',
                   'node["fishing"="yes"]["leisure"!="slipway"]["amenity"!="boat_ramp"](' + bbox + ');',
                   'node["sport"="fishing"](' + bbox + ');');
        }
        if (has('buoy')) {
            s.push('node["seamark:type"="buoy_lateral"](' + bbox + ');',
                   'node["seamark:type"="buoy_cardinal"](' + bbox + ');',
                   'node["seamark:type"="buoy_safe_water"](' + bbox + ');',
                   'node["seamark:type"="buoy_isolated_danger"](' + bbox + ');',
                   'node["seamark:type"="beacon_lateral"](' + bbox + ');',
                   'node["seamark:type"="beacon_cardinal"](' + bbox + ');',
                   'node["seamark:type"="beacon_safe_water"](' + bbox + ');',
                   'node["seamark:type"="beacon_isolated_danger"](' + bbox + ');',
                   'node["seamark:type"="light_major"](' + bbox + ');',
                   'node["seamark:type"="light_minor"](' + bbox + ');',
                   'node["man_made"="buoy"](' + bbox + ');');
        }
        if (has('fishing_shop')) {
            s.push('node["shop"="fishing"](' + bbox + ');');
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
                   'way["sport"="scuba_diving"](' + bbox + ');');
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
            _spotCachePut(key, deduped);
            _ssSave();
            hideStructLoading(); // request chain complete; drop spinner
            hideStructError();   // fallback succeeded — dismiss the error banner
            renderFishingSpots(deduped, key);
            // hint is updated inside renderFishingSpots via _updateZoomSuppressedHint
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
                    // Bypass _spotCachePut to avoid eviction during bulk restore;
                    // rebuild _spotCacheKeys so future puts evict correctly.
                    spotCache[k] = e.data;
                    _spotCacheKeys.push(k);
                    loaded++;
                }
            });
            // Enforce cap after restore in case stored data exceeded the limit
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
            sessionStorage.setItem(_SS_KEY, JSON.stringify(obj));
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
    function showSuggestions(q) {
        if (!els.suggestions || !q || q.length < 2) { hideSuggestions(); return; }
        var lower = q.toLowerCase();
        // Use pre-built lowercase mirror so we never call .toLowerCase() on all 895 names at keystroke time
        var src   = allSpeciesLower.length === allSpecies.length ? allSpeciesLower : null;
        var hits  = [];
        for (var _i = 0; _i < allSpecies.length && hits.length < 10; _i++) {
            if ((src ? src[_i] : allSpecies[_i].toLowerCase()).indexOf(lower) !== -1) {
                hits.push(allSpecies[_i]);
            }
        }
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
    // Key is versioned — bump when adding incompatible fields so old saved data
    // is silently ignored rather than causing unexpected UI state for users.
    var LS_KEY = 'fmap_filters_v4';  // spotTypes field added in v4

    function saveFilters() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                species:    activeSpecies,
                spotTypes:  activeSpotTypes.slice()
            }));
        } catch (e) {
            console.warn('[fishing-map] saveFilters failed:', e);
        }
    }

    function loadFilters() {
        try {
            var raw = localStorage.getItem(LS_KEY);
            // No saved state → new user → leave all filters at their empty defaults.
            if (!raw) return;
            var f = JSON.parse(raw);
            if (f.species) {
                activeSpecies = f.species;
                if (els.speciesInput)  els.speciesInput.value = f.species;
                if (els.searchClear) els.searchClear.hidden = false;
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
        // Tell server to omit the 895-name species list once the client has it
        if (allSpecies.length > 0) params.set('has_species', '1');

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

                if (allSpecies.length === 0 && data.species_names && data.species_names.length) {
                    allSpecies = data.species_names;
                    // Pre-build lowercase mirror so showSuggestions never calls .toLowerCase() at keystroke time
                    allSpeciesLower = allSpecies.map(function (n) { return n.toLowerCase(); });
                }

                // Update species meta for AI habitat inference (works for all 895 species)
                currentSpeciesMeta = (data.species_meta && data.species_meta.name)
                    ? data.species_meta : null;

                // Zoom to saved location then load structure overlays and community feed
                autoZoomToSavedLocation(currentData);
                updateZoomHint();
                scheduleFishingSpotQuery();
                // Reload community map pins with the updated species filter, so pins
                // reflect the same species the user has selected in the main search.
                if (communityLayerOn) scheduleCommunityLoad();
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
                scheduleFetch();
            });
            els.speciesInput.addEventListener('change', function () {
                activeSpecies = els.speciesInput.value.trim();
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
                scheduleFetch();
            });
        }

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
                updateAdvBadge();
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

        var b    = map.getBounds();
        var sw   = b.getSouthWest();
        var ne   = b.getNorthEast();
        var url  = '/api/map/catches?sw_lat=' + Math.round(sw.lat * 100) / 100 +
                   '&sw_lng=' + Math.round(sw.lng * 100) / 100 +
                   '&ne_lat=' + Math.round(ne.lat * 100) / 100 +
                   '&ne_lng=' + Math.round(ne.lng * 100) / 100 +
                   '&limit=200';
        // When the user has filtered by species, show only matching catches on the map.
        if (activeSpecies) url += '&species=' + encodeURIComponent(activeSpecies);

        fetch(url, { signal: _communityAbort.signal })
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
                if (err && err.name === 'AbortError') return; // superseded by newer fetch
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
                    spotCache = {}; _spotCacheKeys = [];
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
                    spotCache = {}; _spotCacheKeys = [];
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
                if (!seaIceOn || !map) return;  // layer turned off while loading
                if (!data || !data.sea_ice) {
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
        if (map.getZoom() < 6) { gaugeLayer.clearLayers(); return; }
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
                    var icon  = _cachedDivIcon('gauge|' + color, {
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
                    var color    = rpt.color || '#facc15';
                    var rptEmoji = ICONS[rpt.type] || '⚡';
                    var icon  = _cachedDivIcon('srpt|' + color + '|' + (rpt.type || ''), {
                        className: '',
                        html: '<div class="fmap-storm-rpt-dot" style="background:' + color + '">' +
                              rptEmoji + '</div>',
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
                        esc(rpt.type.charAt(0).toUpperCase() + rpt.type.slice(1)) +
                        (rpt.magnitude ? ' · ' + esc(String(rpt.magnitude)) : '') + '</strong>' +
                        (rpt.location ? '<div>' + esc(rpt.location) + (rpt.state ? ', ' + esc(rpt.state) : '') + '</div>' : '') +
                        (timeStr ? '<div class="fmap-storm-rpt-time">' + timeStr + '</div>' : '') +
                        (rpt.comments ? '<div class="fmap-storm-rpt-comments">' + esc(rpt.comments) + '</div>' : '') +
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

    // ─── AQI / PM2.5 overlay (ArcGIS Live Feeds) ──────────────────────────────

    function wireAqiLayer() {
        if (!map) return;
        aqiLayer = L.layerGroup();

        var btn = document.getElementById('fmap-aqi-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                aqiOn = !aqiOn;
                btn.classList.toggle('fmap-ctrl-btn--active', aqiOn);
                btn.setAttribute('aria-pressed', aqiOn ? 'true' : 'false');
                if (aqiOn) { aqiLayer.addTo(map); doFetchAqi(); }
                else { map.removeLayer(aqiLayer); aqiLayer.clearLayers(); }
            });
        }
        map.on('moveend zoomend', function () { if (aqiOn) { clearTimeout(aqiTimer); aqiTimer = setTimeout(doFetchAqi, 700); } });
    }

    function doFetchAqi() {
        if (!aqiOn || !map) return;
        if (map.getZoom() < 5) { aqiLayer.clearLayers(); return; }
        if (aqiAbort) { try { aqiAbort.abort(); } catch (e) {} }
        aqiAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/air-quality?south=' + b.getSouth().toFixed(3) +
                  '&west='  + b.getWest().toFixed(3) +
                  '&north=' + b.getNorth().toFixed(3) +
                  '&east='  + b.getEast().toFixed(3);

        fetch(url, { signal: aqiAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!aqiOn || !map || !data) return;
                aqiLayer.clearLayers();
                (data.stations || []).forEach(function (s) {
                    // title attr excluded from icon HTML so icons are cacheable by color.
                    // Station name is available in the popup bindPopup below.
                    var icon = _cachedDivIcon('aqi|' + s.color, {
                        className: '',
                        html: '<div class="fmap-aqi-dot" style="background:' + s.color + '"></div>',
                        iconSize: [14, 14], iconAnchor: [7, 7],
                    });
                    var updStr = '';
                    if (s.updated) { try { updStr = new Date(s.updated).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}); } catch(e) {} }
                    L.marker([s.lat, s.lng], { icon: icon })
                     .bindPopup('<div class="fmap-aqi-popup">' +
                        '<strong>' + esc(s.name || 'AQI Station') + '</strong>' +
                        '<div class="fmap-aqi-val" style="color:' + s.color + '">' + s.pm25 + ' µg/m³</div>' +
                        '<div class="fmap-aqi-cat" style="background:' + s.color + '">' + esc(s.category) + '</div>' +
                        (updStr ? '<div style="opacity:.5;font-size:.72rem;margin-top:4px">' + updStr + '</div>' : '') +
                        '<div style="opacity:.4;font-size:.68rem;margin-top:3px">OpenAQ PM2.5 via ArcGIS Live Feeds</div>' +
                        '</div>', { maxWidth: 220 })
                     .addTo(aqiLayer);
                });
                if (!data.stations || !data.stations.length) showToast('No AQI stations in view');
            })
            .catch(function (err) { if (err && err.name !== 'AbortError') console.warn('[fishing-map] AQI fetch failed:', err); });
    }

    // ─── US Drought Monitor overlay (ArcGIS Live Feeds) ──────────────────────

    function wireDroughtLayer() {
        if (!map) return;
        droughtLayer = L.layerGroup();

        var btn = document.getElementById('fmap-drought-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                droughtOn = !droughtOn;
                btn.classList.toggle('fmap-ctrl-btn--active', droughtOn);
                btn.setAttribute('aria-pressed', droughtOn ? 'true' : 'false');
                if (droughtOn) { droughtLayer.addTo(map); doFetchDrought(); }
                else { map.removeLayer(droughtLayer); droughtLayer.clearLayers(); }
            });
        }
        map.on('moveend zoomend', function () { if (droughtOn) { clearTimeout(droughtTimer); droughtTimer = setTimeout(doFetchDrought, 800); } });
    }

    function doFetchDrought() {
        if (!droughtOn || !map) return;
        if (droughtAbort) { try { droughtAbort.abort(); } catch (e) {} }
        droughtAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/drought?south=' + b.getSouth().toFixed(3) +
                  '&west='  + b.getWest().toFixed(3) +
                  '&north=' + b.getNorth().toFixed(3) +
                  '&east='  + b.getEast().toFixed(3);

        fetch(url, { signal: droughtAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!droughtOn || !map || !data) return;
                droughtLayer.clearLayers();
                (data.polygons || []).forEach(function (p) {
                    if (!p.rings || !p.rings.length) return;
                    p.rings.forEach(function (ring) {
                        L.polygon(ring, {
                            color:       p.color,
                            fillColor:   p.color,
                            fillOpacity: _getLayerOpacity('fmap-drought-btn') / 100,
                            weight:      1,
                            opacity:     0.6,
                            interactive: true,
                        }).bindTooltip('<strong>' + esc(p.code) + ' – ' + esc(p.label) + '</strong>' +
                                       '<br><small style="opacity:.65">US Drought Monitor</small>',
                                       { sticky: true, opacity: 0.92 })
                          .addTo(droughtLayer);
                    });
                });
                if (!data.polygons || !data.polygons.length) showToast('No drought data in view (CONUS only)');
            })
            .catch(function (err) { if (err && err.name !== 'AbortError') console.warn('[fishing-map] drought fetch failed:', err); });
    }

    // ─── NDFD Precipitation overlay (ArcGIS Live Feeds) ──────────────────────

    function wirePrecipLayer() {
        if (!map) return;
        precipLayer = L.layerGroup();

        var btn = document.getElementById('fmap-precip-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                precipOn = !precipOn;
                btn.classList.toggle('fmap-ctrl-btn--active', precipOn);
                btn.setAttribute('aria-pressed', precipOn ? 'true' : 'false');
                if (precipOn) { precipLayer.addTo(map); doFetchPrecip(); }
                else { map.removeLayer(precipLayer); precipLayer.clearLayers(); }
            });
        }
        map.on('moveend zoomend', function () { if (precipOn) { clearTimeout(precipTimer); precipTimer = setTimeout(doFetchPrecip, 800); } });
    }

    function doFetchPrecip() {
        if (!precipOn || !map) return;
        if (precipAbort) { try { precipAbort.abort(); } catch (e) {} }
        precipAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/precipitation?south=' + b.getSouth().toFixed(3) +
                  '&west='  + b.getWest().toFixed(3) +
                  '&north=' + b.getNorth().toFixed(3) +
                  '&east='  + b.getEast().toFixed(3);

        fetch(url, { signal: precipAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!precipOn || !map || !data) return;
                precipLayer.clearLayers();
                (data.polygons || []).forEach(function (p) {
                    var label = p.label || 'Precipitation';
                    if (!p.rings || !p.rings.length) return;
                    var timeStr = '';
                    if (p.from_time) {
                        try {
                            var d = new Date(p.from_time);
                            timeStr = d.toLocaleDateString([], {month:'short',day:'numeric'}) + ' ' +
                                      d.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
                        } catch(e) {}
                    }
                    p.rings.forEach(function (ring) {
                        L.polygon(ring, {
                            color:       p.color,
                            fillColor:   p.color,
                            fillOpacity: _getLayerOpacity('fmap-precip-btn') / 100,
                            weight:      1,
                            opacity:     0.5,
                        }).bindTooltip('<strong>Precip: ' + esc(label) + '</strong>' +
                                       (timeStr ? '<br><small>' + timeStr + '</small>' : '') +
                                       '<br><small style="opacity:.65">NOAA NDFD · 6-hr forecast</small>',
                                       { sticky: true, opacity: 0.92 })
                          .addTo(precipLayer);
                    });
                });
                if (!data.polygons || !data.polygons.length) showToast('No precipitation forecast in view');
            })
            .catch(function (err) { if (err && err.name !== 'AbortError') console.warn('[fishing-map] precip fetch failed:', err); });
    }

    // ─── NDFD Temperature polygons overlay (ArcGIS Live Feeds) ──────────────

    function wireNdfdTempLayer() {
        if (!map) return;
        ndfdTempLayer = L.layerGroup();

        var btn = document.getElementById('fmap-ndfd-temp-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                ndfdTempOn = !ndfdTempOn;
                btn.classList.toggle('fmap-ctrl-btn--active', ndfdTempOn);
                btn.setAttribute('aria-pressed', ndfdTempOn ? 'true' : 'false');
                if (ndfdTempOn) { ndfdTempLayer.addTo(map); doFetchNdfdTemp(); }
                else { map.removeLayer(ndfdTempLayer); ndfdTempLayer.clearLayers(); }
            });
        }
        map.on('moveend zoomend', function () {
            if (ndfdTempOn) { clearTimeout(ndfdTempTimer); ndfdTempTimer = setTimeout(doFetchNdfdTemp, 800); }
        });
    }

    function doFetchNdfdTemp() {
        if (!ndfdTempOn || !map) return;
        if (ndfdTempAbort) { try { ndfdTempAbort.abort(); } catch (e) {} }
        ndfdTempAbort = new AbortController();
        var b   = map.getBounds();
        var url = '/api/map/temperature?south=' + b.getSouth().toFixed(3) +
                  '&west='  + b.getWest().toFixed(3) +
                  '&north=' + b.getNorth().toFixed(3) +
                  '&east='  + b.getEast().toFixed(3);

        fetch(url, { signal: ndfdTempAbort.signal })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!ndfdTempOn || !map || !data) return;
                ndfdTempLayer.clearLayers();
                // Render max temperature polygons by default; min as dashed overlay
                var tempOpacity = _getLayerOpacity('fmap-ndfd-temp-btn') / 100;
                var layers = [
                    { key: 'max', opacity: tempOpacity,       weight: 0.5, label: 'High' },
                    { key: 'min', opacity: tempOpacity * 0.5, weight: 0,   label: 'Low',  dash: '4 3' },
                ];
                var total = 0;
                layers.forEach(function (cfg) {
                    (data[cfg.key] || []).forEach(function (p) {
                        if (!p.rings || !p.rings.length) return;
                        total++;
                        var tip = '<strong>' + cfg.label + ' Temp: ' + p.temp_f + '°F</strong>' +
                                  (p.period ? '<br><small>' + p.period + '</small>' : '') +
                                  '<br><small style="opacity:.65">NOAA NDFD · daily forecast</small>';
                        p.rings.forEach(function (ring) {
                            L.polygon(ring, {
                                color:        p.color,
                                fillColor:    p.color,
                                fillOpacity:  cfg.opacity,
                                weight:       cfg.weight,
                                dashArray:    cfg.dash || null,
                            }).bindTooltip(tip, { sticky: true, opacity: 0.92 })
                              .addTo(ndfdTempLayer);
                        });
                    });
                });
                if (!total) showToast('No temperature data in view');
            })
            .catch(function (err) {
                if (err && err.name !== 'AbortError') console.warn('[fishing-map] ndfd-temp fetch failed:', err);
            });
    }

    // ─── NDBC Buoy overlay (ArcGIS Live Feeds) ────────────────────────────────

    function wireBuoyLayer() {
        if (!map) return;
        buoyLayer = L.layerGroup();

        var btn = document.getElementById('fmap-buoy-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                buoyOn = !buoyOn;
                btn.classList.toggle('fmap-ctrl-btn--active', buoyOn);
                btn.setAttribute('aria-pressed', buoyOn ? 'true' : 'false');
                if (buoyOn) { buoyLayer.addTo(map); doFetchBuoys(); }
                else { map.removeLayer(buoyLayer); buoyLayer.clearLayers(); }
            });
        }
        map.on('moveend zoomend', function () { if (buoyOn) { clearTimeout(buoyTimer); buoyTimer = setTimeout(doFetchBuoys, 700); } });
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
                        (updStr ? '<div style="opacity:.5;font-size:.72rem">' + updStr + '</div>' : '') +
                        '<table class="fmap-gauge-table">' +
                        '<tr><td>Water Temp</td><td><span style="color:' + clr + '">' + wt + '</span></td></tr>' +
                        '<tr><td>Wave Height</td><td>' + wh + '</td></tr>' +
                        '<tr><td>Wave Period</td><td>' + pr + '</td></tr>' +
                        '<tr><td>Wind</td><td>' + ws + (b.wind_dir != null ? ' @ ' + b.wind_dir + '°' : '') + '</td></tr>' +
                        (b.pressure_mb != null ? '<tr><td>Pressure</td><td>' + b.pressure_mb + ' mb</td></tr>' : '') +
                        '</table>' +
                        '<div style="opacity:.4;font-size:.68rem;margin-top:3px">NDBC via ArcGIS Live Feeds</div>' +
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

    function wireHfradarLayer() {
        if (!map) return;
        hfradarLayer = L.layerGroup();

        var btn = document.getElementById('fmap-hfradar-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                hfradarOn = !hfradarOn;
                btn.classList.toggle('fmap-ctrl-btn--active', hfradarOn);
                btn.setAttribute('aria-pressed', hfradarOn ? 'true' : 'false');
                if (hfradarOn) { hfradarLayer.addTo(map); doFetchHfradar(); }
                else { map.removeLayer(hfradarLayer); hfradarLayer.clearLayers(); }
            });
        }
        map.on('moveend zoomend', function () {
            if (hfradarOn) { clearTimeout(hfradarTimer); hfradarTimer = setTimeout(doFetchHfradar, 700); }
        });
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
                        (updStr ? '<br><small style="opacity:.6">' + updStr + '</small>' : '') +
                        '<br><small style="opacity:.5">NOAA HF Radar</small>',
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
                            '<div style="font-size:.75rem;opacity:.7;margin-top:2px">Basin: ' + esc(a.basin) + '</div>' +
                            (a.discussion ? '<p style="font-size:.74rem;margin:6px 0 2px;opacity:.85">' + esc(a.discussion) + '</p>' : '') +
                            '<div style="font-size:.68rem;opacity:.45;margin-top:4px">NHC Tropical Weather Outlook · ArcGIS Live Feeds</div>' +
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
    // IDs of all layer-row buttons inside the popup (must match the HTML ids).
    var LAYER_BTN_IDS = [
        'fmap-marine-warn-btn', 'fmap-storm-tracker-btn', 'fmap-recent-storms-btn',
        'fmap-storm-rpt-btn', 'fmap-tropical-btn',
        'fmap-sst-btn', 'fmap-sea-ice-btn', 'fmap-wildfire-btn', 'fmap-seismic-btn',
        'fmap-drought-btn', 'fmap-precip-btn', 'fmap-ndfd-temp-btn',
        'fmap-buoy-btn', 'fmap-hfradar-btn',
        'fmap-metar-btn', 'fmap-gauge-btn', 'fmap-terminator-btn', 'fmap-aqi-btn'
    ];
    var LS_LAYERS_KEY   = 'fmap_layers_v3';   // bumped to clear old saved state
    var LS_SECTIONS_KEY = 'fmap_sections_v1'; // stores array of collapsed section ids

    // Map from section data-section value → layer button IDs it contains
    var SECTION_LAYER_MAP = {
        weather: ['fmap-marine-warn-btn', 'fmap-storm-tracker-btn',
                  'fmap-recent-storms-btn', 'fmap-storm-rpt-btn', 'fmap-tropical-btn'],
        ocean:   ['fmap-sst-btn', 'fmap-sea-ice-btn',
                  'fmap-wildfire-btn', 'fmap-seismic-btn',
                  'fmap-drought-btn', 'fmap-precip-btn', 'fmap-ndfd-temp-btn',
                  'fmap-buoy-btn', 'fmap-hfradar-btn'],
        obs:     ['fmap-metar-btn', 'fmap-gauge-btn', 'fmap-terminator-btn', 'fmap-aqi-btn']
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
        'fmap-drought-btn':   30,
        'fmap-precip-btn':    35,
        'fmap-ndfd-temp-btn': 28,
        'fmap-tropical-btn':  20,
    };
    var _layerOpacities = {};
    try {
        var _opRaw = localStorage.getItem('fmap_opacities_v1');
        if (_opRaw) _layerOpacities = JSON.parse(_opRaw) || {};
    } catch(e) {}

    function _getPolygonLayers() {
        return {
            'fmap-drought-btn':   droughtLayer,
            'fmap-precip-btn':    precipLayer,
            'fmap-ndfd-temp-btn': ndfdTempLayer,
            'fmap-tropical-btn':  tropicalLayer,
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
        drought: {
            label: 'Drought (NDMC)',
            items: [
                { color: '#FFFF00', text: 'D0 Abnormally Dry' },
                { color: '#FCD37F', text: 'D1 Moderate' },
                { color: '#FFAA00', text: 'D2 Severe' },
                { color: '#E60000', text: 'D3 Extreme' },
                { color: '#730000', text: 'D4 Exceptional', dark: true },
            ]
        },
        aqi: {
            label: 'Air Quality (PM2.5)',
            items: [
                { color: '#22c55e', text: 'Good',                    dark: true },
                { color: '#eab308', text: 'Moderate' },
                { color: '#f97316', text: 'Unhealthy · Sensitive',   dark: true },
                { color: '#ef4444', text: 'Unhealthy',               dark: true },
                { color: '#a855f7', text: 'Very Unhealthy',          dark: true },
                { color: '#7c3aed', text: 'Hazardous',               dark: true },
            ]
        },
        precip: {
            label: 'Precipitation (NDFD)',
            items: [
                { color: '#c6e3f5', text: 'Light' },
                { color: '#74b9e8', text: 'Moderate' },
                { color: '#2563eb', text: 'Heavy',   dark: true },
                { color: '#1e3a8a', text: 'Extreme', dark: true },
            ]
        },
        'ndfd-temp': {
            label: 'Temp Forecast (°F High)',
            items: [
                { color: '#3b82f6', text: '<32°F (freeze)', dark: true },
                { color: '#06b6d4', text: '32–50°F',        dark: true },
                { color: '#34d399', text: '50–65°F',        dark: true },
                { color: '#fbbf24', text: '65–80°F' },
                { color: '#f97316', text: '80–95°F',        dark: true },
                { color: '#ef4444', text: '>95°F',          dark: true },
            ]
        },
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
        seismic: {
            label: 'Earthquakes',
            items: [
                { color: '#fde68a', text: 'M 2.5–3.5' },
                { color: '#f59e0b', text: 'M 3.5–5.0' },
                { color: '#ef4444', text: 'M 5.0+',    dark: true },
            ]
        },
    };

    // Map from layer key → the boolean var that tracks "is this layer on?"
    function _legendLayerOn(key) {
        switch (key) {
            case 'drought':   return droughtOn;
            case 'aqi':       return aqiOn;
            case 'precip':    return precipOn;
            case 'ndfd-temp': return ndfdTempOn;
            case 'hfradar':   return hfradarOn;
            case 'tropical':  return tropicalOn;
            case 'metar':     return metarOn;
            case 'seismic':   return seismicOn;
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
            allRows.forEach(function (row) {
                var nameEl = row.querySelector('.fmap-layer-row-name');
                var descEl = row.querySelector('.fmap-layer-row-desc');
                var text = ((nameEl ? nameEl.textContent : '') + ' ' + (descEl ? descEl.textContent : '')).toLowerCase();
                var show = !lq || text.indexOf(lq) !== -1;
                row.style.display = show ? '' : 'none';
            });
            // Hide section headers when all their rows are hidden; show otherwise.
            // Sections with no .fmap-layer-row elements (e.g. Spot Filters) are always shown.
            sections.forEach(function (sec) {
                var allSectionRows = sec.querySelectorAll('.fmap-layer-row');
                if (!allSectionRows.length) { sec.style.display = ''; return; }
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
        }

        if (searchInput) {
            searchInput.addEventListener('input', function () { _applyLayerSearch(searchInput.value); });
            searchInput.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') { searchInput.value = ''; _applyLayerSearch(''); }
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
                wireFilters();
                wireMapControls();
                wireSpotTypeFilters();
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
                wireAqiLayer();
                wireDroughtLayer();
                wirePrecipLayer();
                wireNdfdTempLayer();
                wireBuoyLayer();
                wireHfradarLayer();
                wireTropicalOutlook();
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
        els.speciesInput   = document.getElementById('fmap-species-input');
        els.searchClear    = document.getElementById('fmap-search-clear');
        els.suggestions    = document.getElementById('fmap-suggestions');
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
        boot();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
