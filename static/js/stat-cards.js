/* ArcGIS Live Feeds: Air Quality, NDFD Wind/Temp, Drought, Buoys, METAR, Wildfires, Gauges, Tropical */
(function () {
    var lat = typeof CURRENT_LOC_LAT !== 'undefined' ? CURRENT_LOC_LAT : 0;
    var lng = typeof CURRENT_LOC_LNG !== 'undefined' ? CURRENT_LOC_LNG : 0;

    function showCard(card) {
        if (!card) return;
        card.classList.remove('stat-card--loading');
    }
    function hideCard(card) {
        if (!card) return;
        card.hidden = true;
    }

    if (!lat || !lng) {
        // No location — immediately hide all location-dependent live cards
        ['aqi-stat-card','drought-stat-card','ndbc-stat-card',
         'gauge-stat-card','metar-stat-card','wildfire-stat-card','tropical-stat-card'].forEach(function (id) {
            hideCard(document.getElementById(id));
        });
    } else {
        // ── Air Quality + Drought stat cards (one combined request) ───────────
        (function () {
            var aqiCard   = document.getElementById('aqi-stat-card');
            var aqiVal    = document.getElementById('aqi-stat-value');
            var aqiDet    = document.getElementById('aqi-stat-detail');
            var drtCard   = document.getElementById('drought-stat-card');
            var drtVal    = document.getElementById('drought-stat-value');
            var drtDet    = document.getElementById('drought-stat-detail');
            fetch('/api/weather/env-context?lat=' + lat + '&lng=' + lng)
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data) { hideCard(aqiCard); hideCard(drtCard); return; }

                    var aqi = data.aqi;
                    if (aqi) {
                        aqiVal.innerHTML =
                            '<span class="aqi-badge" style="background:' + aqi.color + '">' +
                            aqi.category + '</span>';
                        var dist = aqi.distance_km ? ' &middot; ' + aqi.distance_km + ' km' : '';
                        aqiDet.innerHTML =
                            aqi.value + ' ' + (aqi.unit || 'µg/m³') + ' PM2.5' + dist;
                        showCard(aqiCard);
                    } else {
                        hideCard(aqiCard);
                    }

                    var d = data.drought;
                    if (d && d.dm !== -1) {
                        drtVal.innerHTML =
                            '<span class="drought-badge" style="background:' + d.color + ';color:' +
                            (d.dm >= 2 ? '#fff' : '#1a1a1a') + '">' + d.code + '</span>';
                        drtDet.textContent = d.label + (d.date ? ' · ' + d.date : '');
                        showCard(drtCard);
                    } else {
                        hideCard(drtCard);
                    }
                })
                .catch(function () { hideCard(aqiCard); hideCard(drtCard); });
        })();

        // ── Buoys, METAR, Wildfires, Gauges, Tropical (one combined request) ─────
        (function () {
            var buoyCard = document.getElementById('ndbc-stat-card');
            var buoyVal  = document.getElementById('ndbc-stat-value');
            var buoyDet  = document.getElementById('ndbc-stat-detail');
            var metCard  = document.getElementById('metar-stat-card');
            var metVal   = document.getElementById('metar-stat-value');
            var metDet   = document.getElementById('metar-stat-detail');
            var fireCard = document.getElementById('wildfire-stat-card');
            var fireVal  = document.getElementById('wildfire-stat-value');
            var fireDet  = document.getElementById('wildfire-stat-detail');
            var gaugeCard = document.getElementById('gauge-stat-card');
            var gaugeVal  = document.getElementById('gauge-stat-value');
            var gaugeDet  = document.getElementById('gauge-stat-detail');
            var tropCard = document.getElementById('tropical-stat-card');
            var tropVal  = document.getElementById('tropical-stat-value');
            var tropDet  = document.getElementById('tropical-stat-detail');

            function renderTropical(data) {
                if (!data || !data.areas || !data.areas.length) { hideCard(tropCard); return; }
                var areas = data.areas;
                var high  = areas.filter(function (a) { return a.probability === 'high'; }).length;
                var med   = areas.filter(function (a) { return a.probability === 'medium'; }).length;
                var total = areas.length;
                var topColor = high ? '#ef4444' : med ? '#f97316' : '#eab308';
                var topLabel = high ? 'High' : med ? 'Medium' : 'Low';
                tropVal.innerHTML = '<span class="drought-badge" style="background:' + topColor + ';color:#fff">' + topLabel + '</span>';
                tropDet.textContent = total + ' active area' + (total !== 1 ? 's' : '') +
                    (high ? ' · ' + high + ' high prob' : med ? ' · ' + med + ' medium prob' : '');
                showCard(tropCard);
            }

            fetch('/api/map/stat-cards?lat=' + lat + '&lng=' + lng)
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data) {
                        [buoyCard, metCard, fireCard, gaugeCard, tropCard].forEach(hideCard);
                        return;
                    }

                    // ── Buoy ──────────────────────────────────────────────────
                    var buoys = data.buoys && data.buoys.buoys;
                    if (buoys && buoys.length) {
                        var best = null, bestD = Infinity;
                        buoys.forEach(function (b) {
                            var d = Math.pow(b.lat - lat, 2) + Math.pow(b.lng - lng, 2);
                            if (d < bestD) { bestD = d; best = b; }
                        });
                        if (best) {
                            if (best.wave_ht_ft != null) buoyVal.textContent = best.wave_ht_ft.toFixed(1) + ' ft waves';
                            else if (best.water_temp_f != null) buoyVal.textContent = best.water_temp_f.toFixed(1) + '°F water';
                            else buoyVal.textContent = best.name || best.id;
                            var bparts = [];
                            if (best.wave_ht_ft != null && best.water_temp_f != null) bparts.push(best.water_temp_f.toFixed(0) + '°F water');
                            if (best.wind_kt != null) bparts.push((best.wind_dir || '') + ' ' + best.wind_kt.toFixed(0) + ' kt wind');
                            if (best.period_s != null) bparts.push(best.period_s.toFixed(0) + 's period');
                            bparts.push((Math.sqrt(bestD) * 111).toFixed(0) + ' km away');
                            buoyDet.textContent = bparts.join(' · ');
                            showCard(buoyCard);
                        } else { hideCard(buoyCard); }
                    } else { hideCard(buoyCard); }

                    // ── METAR ─────────────────────────────────────────────────
                    var stations = data.metar && data.metar.stations;
                    if (stations && stations.length) {
                        var best = null, bestD = Infinity;
                        stations.forEach(function (st) {
                            var d = Math.pow(st.lat - lat, 2) + Math.pow(st.lng - lng, 2);
                            if (d < bestD) { bestD = d; best = st; }
                        });
                        if (best) {
                            var catColor = best.cat_color || '#9ca3af';
                            metVal.innerHTML = '<span class="drought-badge" style="background:' + catColor + ';color:#fff">' + (best.flight_cat || best.cat || 'N/A') + '</span>';
                            var mparts = [];
                            if (best.temp_f != null) mparts.push(best.temp_f.toFixed(0) + '°F');
                            if (best.wind_kt != null) mparts.push((best.wind_dir || '') + ' ' + best.wind_kt.toFixed(0) + ' kt');
                            if (best.vis_mi != null) mparts.push(best.vis_mi.toFixed(0) + ' mi vis');
                            mparts.push((Math.sqrt(bestD) * 111).toFixed(0) + ' km away');
                            metDet.textContent = mparts.join(' · ');
                            showCard(metCard);
                        } else { hideCard(metCard); }
                    } else { hideCard(metCard); }

                    // ── Wildfires ─────────────────────────────────────────────
                    var fires = data.fires && data.fires.fires;
                    if (fires && fires.length) {
                        var largest = fires.reduce(function (a, b) { return (b.acres || 0) > (a.acres || 0) ? b : a; }, fires[0]);
                        var totalAcres = fires.reduce(function (s, f) { return s + (f.acres || 0); }, 0);
                        fireVal.innerHTML = '<span class="drought-badge" style="background:#f97316;color:#fff">' + fires.length + (fires.length === 1 ? ' fire' : ' fires') + '</span>';
                        var fdet = totalAcres > 0 ? (totalAcres >= 1000 ? (totalAcres / 1000).toFixed(0) + 'k' : totalAcres.toFixed(0)) + ' total acres' : '';
                        if (largest && largest.name) fdet += (fdet ? ' · ' : '') + largest.name;
                        fireDet.textContent = fdet || 'active wildfires in area';
                        showCard(fireCard);
                    } else { hideCard(fireCard); }

                    // ── Stream Gauge ──────────────────────────────────────────
                    var gauges = data.gauges && data.gauges.gauges;
                    if (gauges && gauges.length) {
                        var best = null, bestD = Infinity;
                        gauges.forEach(function (g) {
                            if (g.status_class === undefined || g.status_class === null) return;
                            var d = Math.pow(g.lat - lat, 2) + Math.pow(g.lng - lng, 2);
                            if (d < bestD) { bestD = d; best = g; }
                        });
                        if (best) {
                            var sc = best.status_class || 0;
                            var color = best.status_color || '#9ca3af';
                            var label = best.status || 'Normal';
                            gaugeVal.innerHTML = '<span class="drought-badge" style="background:' + color + ';color:' + (sc >= 2 ? '#fff' : '#1a1a1a') + '">' + label + '</span>';
                            var gdet = best.name ? best.name.substring(0, 28) : best.id;
                            if (best.stage_ft != null) gdet += ' · ' + best.stage_ft.toFixed(1) + ' ft';
                            gdet += ' · ' + (Math.sqrt(bestD) * 111).toFixed(0) + ' km away';
                            gaugeDet.textContent = gdet;
                            showCard(gaugeCard);
                        } else { hideCard(gaugeCard); }
                    } else { hideCard(gaugeCard); }

                    // ── Tropical Outlook ──────────────────────────────────────
                    renderTropical(data.tropical);
                })
                .catch(function () {
                    [buoyCard, metCard, fireCard, gaugeCard, tropCard].forEach(hideCard);
                });
        })();
    }

    // ── NDFD Wind + Precipitation + Temperature Forecast (single combined request) ──
    var windBlock = document.getElementById('wind-outlook-block');
    var windGrid  = document.getElementById('wind-outlook-grid');
    var tempBlock = document.getElementById('temp-forecast-block');
    var tempGrid  = document.getElementById('temp-forecast-grid');
    if (lat && lng) fetch('/api/weather/combined-forecast?lat=' + lat + '&lng=' + lng)
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (combined) {
        if (!combined) { combined = {}; }
        var windData   = combined.wind;
        var precipData = combined.precip;
        var tempData   = combined.temp;

        // ── Render temperature strip ────────────────────────────────────────
        if (tempData && tempData.days && tempData.days.length) {
            var DAY_SHORT = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
            var sub = document.getElementById('temp-forecast-sub');
            var tHtml = '';
            tempData.days.forEach(function (d) {
                var dayLabel = '';
                if (d.date) {
                    try {
                        var parts = d.date.split('-');
                        var dt = new Date(Date.UTC(+parts[0], +parts[1]-1, +parts[2]));
                        dayLabel = DAY_SHORT[dt.getUTCDay()] + '<br><small>' + (dt.getUTCMonth()+1) + '/' + dt.getUTCDate() + '</small>';
                    } catch(e) { dayLabel = d.date; }
                }
                var hiStr = d.max_f !== null && d.max_f !== undefined ? d.max_f + '°' : '–';
                var loStr = d.min_f !== null && d.min_f !== undefined ? d.min_f + '°' : '–';
                var hiColor = d.max_f >= 90 ? '#ef4444' : d.max_f >= 80 ? '#f97316'
                            : d.max_f >= 70 ? '#eab308' : d.max_f >= 55 ? '#22c55e'
                            : d.max_f >= 40 ? '#38bdf8' : '#818cf8';
                tHtml += '<div class="temp-fc-cell"><span class="temp-fc-day">' + dayLabel + '</span>'
                       + '<span class="temp-fc-hi" style="color:' + hiColor + '">' + hiStr + '</span>'
                       + '<span class="temp-fc-lo">' + loStr + '</span></div>';
            });
            if (tempGrid) tempGrid.innerHTML = tHtml;
            if (sub && tempData.days[0]) {
                var d0 = tempData.days[0];
                sub.textContent = (d0.max_f !== null ? 'Hi ' + d0.max_f + '°' : '')
                                + (d0.min_f !== null ? ' · Lo ' + d0.min_f + '°' : '');
            }
            if (tempBlock) tempBlock.style.display = '';
        } else {
            if (tempBlock) tempBlock.style.display = 'none';
        }

        // ── Render wind + precip grid ────────────────────────────────────────
        if (!windData || !windData.periods || !windData.periods.length) {
            if (windBlock) windBlock.style.display = 'none';
            return;
        }

        var block = windBlock;
        var grid  = windGrid;
        if (!block || !grid) return;

        var COMPASS_ARROWS = {
            N:'↓', NE:'↙', E:'←', SE:'↖', S:'↑', SW:'↗', W:'→', NW:'↘'
        };

        // Build a lookup of precipitation by approximate start hour
        var precipByHour = {};
        if (precipData && precipData.periods) {
            precipData.periods.forEach(function (pp) {
                if (pp.from_time) {
                    try {
                        var h = new Date(pp.from_time).getTime();
                        precipByHour[h] = pp;
                    } catch (e) {}
                }
            });
        }

        // Find the closest precip period (6-h) for each wind period (3-h)
        function nearestPrecip(isoTime) {
            if (!isoTime || !Object.keys(precipByHour).length) return null;
            try {
                var t   = new Date(isoTime).getTime();
                var best = null, bestDiff = Infinity;
                Object.keys(precipByHour).forEach(function (h) {
                    var diff = Math.abs(t - Number(h));
                    if (diff < bestDiff) { bestDiff = diff; best = precipByHour[h]; }
                });
                // Only associate if within 6 hours
                return bestDiff <= 6 * 3600 * 1000 ? best : null;
            } catch (e) { return null; }
        }

        var html = '';
        windData.periods.forEach(function (p) {
            var arrow = COMPASS_ARROWS[p.wind_dir] || '·';
            var timeStr = '';
            if (p.interval_start) {
                try {
                    var d = new Date(p.interval_start);
                    timeStr = d.toLocaleString([], { weekday:'short', hour:'numeric' });
                } catch (e) {}
            }
            var speedClass = p.wind_speed >= 25 ? 'wind-fc--gale'
                           : p.wind_speed >= 15 ? 'wind-fc--strong'
                           : p.wind_speed >= 8  ? 'wind-fc--moderate'
                           : 'wind-fc--light';
            var pp = nearestPrecip(p.interval_start);
            var rainHtml = pp && pp.rain
                ? '<span class="wind-fc-rain" title="' + (pp.label || '') + '">🌧</span>'
                : '';
            html +=
                '<div class="wind-fc-cell ' + speedClass + '">' +
                '<span class="wind-fc-time">' + timeStr + '</span>' +
                '<span class="wind-fc-arrow" title="' + (p.wind_dir || '') + '">' + arrow + '</span>' +
                '<span class="wind-fc-dir">' + (p.wind_dir || '–') + '</span>' +
                '<span class="wind-fc-speed">' + p.wind_speed + ' kt</span>' +
                (p.wind_gust ? '<span class="wind-fc-gust">G ' + p.wind_gust + '</span>' : '') +
                rainHtml +
                '</div>';
        });
        grid.innerHTML = html;

        // Update subtitle with first period summary
        var sub = document.getElementById('wind-outlook-sub');
        if (sub && windData.periods[0]) {
            var p0 = windData.periods[0];
            sub.textContent = (p0.wind_dir || '') + ' ' + p0.wind_speed + ' kt'
                + (p0.wind_gust ? ' G' + p0.wind_gust : '');
        }

        block.style.display = '';
    })
    .catch(function () {
        if (windGrid) windGrid.innerHTML = '<p class="section-unavailable">Wind forecast unavailable</p>';
    });
})();
