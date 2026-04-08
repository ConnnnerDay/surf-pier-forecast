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
    var userCoords       = null;      // {lat, lng} set after Near Me fires
    var sortByDist       = false;    // legacy (kept for localStorage compat)
    var fishingSpotLayer = null;     // L.layerGroup for OSM piers/jetties/spots
    var spotQueryTimer   = null;     // debounce timer for Overpass queries
    var spotCache        = {};       // bbox-key → array of spot objects
    var aiPickLayer      = null;     // L.layerGroup for AI habitat picks
    var aiQueryTimer     = null;     // debounce timer for AI habitat queries
    var aiCache          = {};       // bbox-key+species → array of habitat features

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

        map = L.map(els.mapEl, { zoomControl: true }).setView(DEFAULT_CENTER, DEFAULT_ZOOM);

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

        // Wire zoom/pan → refresh both layers
        map.on('moveend zoomend', function () {
            updateZoomHint();
            scheduleFishingSpotQuery();
            scheduleAIQuery();
        });

        setTimeout(function () { if (map) map.invalidateSize(); }, 350);

        map.on('moveend zoomend', function () {
            if (structureMode) scheduleStructureFetch();
        });
    }

    // ─── Map overlay controls ─────────────────────────────────────────────────
    function wireMapControls() {
        // Near Me — fly to user location, select nearest active forecast loc
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
                        map.flyTo([userCoords.lat, userCoords.lng], 12, { duration: 1 });
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
                hasAutoZoomed = false; // allow re-centering on saved location
                map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { duration: 0.8 });
                autoZoomToSavedLocation(currentData);
            });
        }

        // Satellite / street tile toggle
        var tileBtn = document.getElementById('fmap-tile-toggle');
        if (tileBtn) {
            tileBtn.addEventListener('click', function () {
                if (!map) return;
                isSatellite = !isSatellite;
                map.removeLayer(activeTileLayer);
                var t = isSatellite ? TILE_SATELLITE : TILE_STREET;
                activeTileLayer = L.tileLayer(t.url, t.opts).addTo(map);
                tileBtn.classList.toggle('fmap-ctrl-btn--active', isSatellite);
                tileBtn.title = isSatellite ? 'Switch to street view' : 'Switch to satellite view';
            });
        }

        var structureBtn = document.getElementById('fmap-structure-btn');
        if (structureBtn) {
            structureBtn.addEventListener('click', toggleStructureMode);
        }

        var aiBtn = document.getElementById('fmap-ai-btn');
        if (aiBtn) {
            aiBtn.addEventListener('click', toggleAiMode);
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
        hint.classList.toggle('fmap-zoom-hint--hidden', map.getZoom() >= 11);
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

        fetch(OVERPASS_URL, {
            method:  'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body:    'data=' + encodeURIComponent(query)
        })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) {
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
        .catch(function () {});
    }

    function scheduleAIQuery() {
        clearTimeout(aiQueryTimer);
        aiQueryTimer = setTimeout(queryAIHabitatSpots, 600);
    }

    // ─── OSM Fishing Spots (Overpass API) ─────────────────────────────────────
    var OVERPASS_URL = 'https://overpass-api.de/api/interpreter';

    var SPOT_TYPES = {
        pier:    { label: 'Fishing Pier',    color: '#a78bfa' },
        jetty:   { label: 'Jetty',           color: '#818cf8' },
        fishing: { label: 'Fishing Spot',    color: '#2dd4bf' },
        'fishing_shop': { label: 'Bait & Tackle', color: '#fb923c' }
    };

    function spotTypeLabel(type) {
        return (SPOT_TYPES[type] || {}).label || 'Fishing Spot';
    }
    function spotTypeColor(type) {
        return (SPOT_TYPES[type] || {}).color || '#2dd4bf';
    }

    function makeFishingSpotIcon(type) {
        var color = spotTypeColor(type);
        var html = '<span class="fmap-spot-dot" style="background:' + color +
                   ';box-shadow:0 0 6px ' + color + '55"></span>';
        return L.divIcon({ className: 'fmap-spot-wrap', html: html, iconSize: [10, 10], iconAnchor: [5, 5] });
    }

    function renderFishingSpots(spots) {
        if (!fishingSpotLayer) return;
        fishingSpotLayer.clearLayers();
        spots.forEach(function (f) {
            if (!f.lat || !f.lng) return;
            var m = L.marker([f.lat, f.lng], { icon: makeFishingSpotIcon(f.type) });
            var name = f.name || spotTypeLabel(f.type);
            m.bindTooltip(
                '<strong>' + esc(name) + '</strong>' +
                '<br><span style="opacity:0.75;font-size:0.7rem">' + esc(spotTypeLabel(f.type)) + '</span>',
                { className: 'fmap-tooltip', direction: 'top', offset: [0, -5] }
            );
            fishingSpotLayer.addLayer(m);
        });
    }

    function queryFishingSpots() {
        if (!map || !fishingSpotLayer) return;
        var zoom = map.getZoom();
        if (zoom < 11) {
            fishingSpotLayer.clearLayers();
            return;
        }
        var b   = map.getBounds();
        // Round to 0.2° grid for cache hits when panning slightly
        var s = Math.floor(b.getSouth() * 5) / 5;
        var w = Math.floor(b.getWest()  * 5) / 5;
        var n = Math.ceil(b.getNorth()  * 5) / 5;
        var e = Math.ceil(b.getEast()   * 5) / 5;
        var key = s + ',' + w + ',' + n + ',' + e;

        if (spotCache[key]) {
            renderFishingSpots(spotCache[key]);
            return;
        }

        var bbox = s + ',' + w + ',' + n + ',' + e;
        var q = '[out:json][timeout:20];(' +
            'node["leisure"="fishing"](' + bbox + ');' +
            'node["man_made"="pier"](' + bbox + ');' +
            'node["man_made"="jetty"](' + bbox + ');' +
            'way["man_made"="pier"](' + bbox + ');' +
            'way["man_made"="jetty"](' + bbox + ');' +
            'node["shop"="fishing"](' + bbox + ');' +
            ');out center;';

        fetch(OVERPASS_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'data=' + encodeURIComponent(q)
        })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) {
            var spots = (data.elements || []).map(function (el) {
                var lat = el.lat || (el.center && el.center.lat);
                var lng = el.lon || (el.center && el.center.lon);
                var tags = el.tags || {};
                var type = tags.man_made || tags.leisure || tags.shop || tags.amenity || 'fishing';
                return { lat: lat, lng: lng, name: tags.name || '', type: type };
            }).filter(function (f) { return f.lat && f.lng; });
            spotCache[key] = spots;
            renderFishingSpots(spots);
        })
        .catch(function () {}); // silently fail — not critical
    }

    function scheduleFishingSpotQuery() {
        clearTimeout(spotQueryTimer);
        spotQueryTimer = setTimeout(queryFishingSpots, 800);
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

    function renderAiHotspots(locations) {
        if (!els.hotspotsList) return;

        var picks = locations
            .filter(function (l) { return l.ai_pick_rank; })
            .sort(function (a, b) { return (a.ai_pick_rank || 99) - (b.ai_pick_rank || 99); });

        if (els.hotspotCount) {
            els.hotspotCount.textContent = picks.length ? picks.length : '';
            els.hotspotCount.style.display = picks.length ? '' : 'none';
        }

        if (!picks.length) {
            els.hotspotsList.innerHTML = '<li class="fmap-hotspot-empty">No AI picks available</li>';
            return;
        }

        var html = '';
        picks.forEach(function (loc) {
            var sp     = loc.top_species && loc.top_species[0];
            var spName = sp ? (sp.name || '') : '';
            var reasoning = loc.ai_reasoning || '';
            // First sentence only for the snippet
            var snippet = reasoning.split('.')[0];
            if (snippet && snippet.length < reasoning.length) snippet += '.';
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
        els.hotspotsList.innerHTML = html;

        els.hotspotsList.querySelectorAll('.fmap-hotspot-item').forEach(function (li) {
            li.addEventListener('click', function () {
                var id  = li.getAttribute('data-loc-id');
                var loc = currentData.find(function (l) { return l.id === id; });
                if (!loc) return;
                map.flyTo([loc.lat, loc.lng], Math.max(map.getZoom(), 7), { duration: 0.5 });
                setTimeout(function () { showAiPickPopup(loc); }, 600);
            });
        });
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
    var LS_KEY = 'fmap_filters_v3';  // bumped for new filter fields

    function saveFilters() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                species:  activeSpecies,
                coast:    activeCoast,
                cat:      activeCat,
                season:   activeSeason,
                time:     activeTime,
                tide:     activeTide,
                minTemp:  activeMinTemp,
                maxTemp:  activeMaxTemp
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
            if (f.season) {
                activeSeason = f.season;
                document.querySelectorAll('.fmap-pill--season').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-season') === f.season);
                });
            }
            if (f.time) {
                activeTime = f.time;
                document.querySelectorAll('.fmap-pill--time').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-time') === f.time);
                });
            }
            if (f.tide) {
                activeTide = f.tide;
                document.querySelectorAll('.fmap-pill--tide').forEach(function (b) {
                    b.classList.toggle('fmap-pill--active', b.getAttribute('data-tide') === f.tide);
                });
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
            updateAdvBadge();
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
        // Advanced filters
        if (activeSeason) params.set('season', activeSeason);
        if (activeTime)   params.set('time_of_day', activeTime);
        if (activeTide)   params.set('tide_phase', activeTide);
        if (activeMinTemp) params.set('min_water_temp', activeMinTemp);
        if (activeMaxTemp) params.set('max_water_temp', activeMaxTemp);

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

                // Update species meta for AI habitat inference (works for all 851 species)
                currentSpeciesMeta = (data.species_meta && data.species_meta.name)
                    ? data.species_meta : null;

                monthlySummary = data.monthly_summary || [];
                drawMarkers(currentData);
                autoZoomToSavedLocation(currentData);
                updateZoomHint();
                scheduleFishingSpotQuery();
                scheduleAIQuery();
                renderMonthPlanner(monthlySummary, data.month);
                renderTrendingChips(data.trending_species || []);
                updateInsight(data);

                if (aiMode) {
                    markers.forEach(function (m) { m.leaflet.setOpacity(0); });
                    if (window.L && window.L.heatLayer) {
                        renderAiOverlay(currentData);
                    } else {
                        ensureLeafletHeat().then(function () {
                            if (aiMode) renderAiOverlay(currentData);
                        }).catch(function () {});
                    }
                }
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

    // ─── AI picks list (rendered into dedicated AI tab panel) ─────────────────

    function renderAiPicksList(locations, container) {
        var picks = locations
            .filter(function (l) { return l.ai_pick_rank; })
            .sort(function (a, b) { return (a.ai_pick_rank || 99) - (b.ai_pick_rank || 99); });

        if (!picks.length) {
            container.innerHTML = '<li class="fmap-hotspot-empty">No AI picks for current filters</li>';
            return;
        }
        var html = '';
        picks.forEach(function (loc) {
            var sp     = loc.top_species && loc.top_species[0];
            var spName = sp ? (sp.name || '') : '';
            var snippet = (loc.ai_reasoning || '').split('.')[0];
            if (snippet && snippet.length < (loc.ai_reasoning || '').length) snippet += '.';
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
        container.innerHTML = html;
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
            .catch(function () {});
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
                wireTabs();
                wireCommunityLayer();
                wireLogCatch();
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
            if (activeCat)    hashParts.push('cat=' + activeCat);
            if (activeMonth)  hashParts.push('month=' + activeMonth);
            if (activeSeason)   hashParts.push('season=' + activeSeason);
            if (activeTime)     hashParts.push('time=' + activeTime);
            if (activeTide)     hashParts.push('tide=' + activeTide);
            if (activeMinTemp)  hashParts.push('min_temp=' + activeMinTemp);
            if (activeMaxTemp)  hashParts.push('max_temp=' + activeMaxTemp);
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
