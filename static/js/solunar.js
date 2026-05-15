// Live solunar + tide countdowns
// Data globals set by the inline script block before this file loads:
//   window._SOL_MAJOR, window._SOL_MINOR, window._TIDES
(function () {
    var SOL_MAJOR = window._SOL_MAJOR || [];
    var SOL_MINOR = window._SOL_MINOR || [];
    var TIDES     = window._TIDES     || [];

    // Parse "6:15 AM" / "12:30 PM" into a Date set to today
    function parseLocalTime(str) {
        if (!str) return null;
        var m = str.trim().match(/^(\d{1,2}):(\d{2})\s*(AM|PM)$/i);
        if (!m) return null;
        var h = parseInt(m[1], 10), min = parseInt(m[2], 10), ampm = m[3].toUpperCase();
        if (ampm === 'AM' && h === 12) h = 0;
        if (ampm === 'PM' && h !== 12) h += 12;
        var d = new Date();
        d.setHours(h, min, 0, 0);
        return d;
    }

    function fmtCountdown(ms) {
        var totalMin = Math.round(ms / 60000);
        if (totalMin <= 0) return null;
        if (totalMin < 60) return totalMin + ' min';
        var h = Math.floor(totalMin / 60), mn = totalMin % 60;
        return mn > 0 ? h + 'h ' + mn + 'm' : h + 'h';
    }

    function timeToMinuteOfDay(str) {
        var d = parseLocalTime(str);
        if (!d) return null;
        return (d.getHours() * 60) + d.getMinutes();
    }

    function drawSolunarChart() {
        var canvas = document.getElementById('solunar-chart-canvas');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        if (!ctx) return;

        var rect = canvas.getBoundingClientRect();
        var width = Math.max(320, Math.floor(rect.width || 0));
        var height = Math.max(120, Math.floor(rect.height || 0));
        var dpr = window.devicePixelRatio || 1;
        if (canvas.width !== Math.floor(width * dpr) || canvas.height !== Math.floor(height * dpr)) {
            canvas.width = Math.floor(width * dpr);
            canvas.height = Math.floor(height * dpr);
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        var left = 14, right = width - 14, top = 18, bottom = height - 22;
        var usableW = Math.max(1, right - left);
        var laneH = Math.max(14, bottom - top);
        function xFromMin(m) { return left + (usableW * (m / 1440)); }

        ctx.fillStyle = 'rgba(255,255,255,0.06)';
        ctx.fillRect(left, top, usableW, laneH);

        // Hour markers.
        ctx.strokeStyle = 'rgba(255,255,255,0.12)';
        ctx.lineWidth = 1;
        for (var h = 0; h <= 24; h += 3) {
            var x = xFromMin(h * 60);
            ctx.beginPath();
            ctx.moveTo(x, top);
            ctx.lineTo(x, bottom);
            ctx.stroke();
        }

        function drawPeriods(periods, fill, alpha) {
            ctx.fillStyle = fill;
            ctx.globalAlpha = alpha;
            periods.forEach(function (p) {
                var start = timeToMinuteOfDay(p.start);
                var end = timeToMinuteOfDay(p.end);
                if (start == null || end == null) return;
                if (end <= start) end += 1440;
                var segments = end > 1440
                    ? [{ s: start, e: 1440 }, { s: 0, e: end - 1440 }]
                    : [{ s: start, e: end }];
                segments.forEach(function (seg) {
                    var x = xFromMin(seg.s), w = Math.max(2, xFromMin(seg.e) - x);
                    ctx.fillRect(x, top, w, laneH);
                });
            });
            ctx.globalAlpha = 1;
        }

        drawPeriods(SOL_MINOR, '#38BDF8', 0.45);
        drawPeriods(SOL_MAJOR, '#F59E0B', 0.82);

        // Current time marker.
        var now = new Date();
        var nowMin = now.getHours() * 60 + now.getMinutes();
        var nx = xFromMin(nowMin);
        ctx.strokeStyle = '#FFFFFF';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(nx, top - 5);
        ctx.lineTo(nx, bottom + 5);
        ctx.stroke();

        // Labels.
        ctx.fillStyle = 'rgba(255,255,255,0.85)';
        ctx.font = '11px system-ui, -apple-system, Segoe UI, Roboto, sans-serif';
        ctx.fillText('12A', left, height - 7);
        ctx.fillText('6A', xFromMin(360) - 9, height - 7);
        ctx.fillText('12P', xFromMin(720) - 11, height - 7);
        ctx.fillText('6P', xFromMin(1080) - 9, height - 7);
        ctx.fillText('12A', right - 22, height - 7);
    }

    // Solunar countdown
    var solChip = document.getElementById('sol-countdown');
    function updateSolunar() {
        if (!solChip) return;
        var now = Date.now();

        for (var i = 0; i < SOL_MAJOR.length; i++) {
            var s = parseLocalTime(SOL_MAJOR[i].start), e = parseLocalTime(SOL_MAJOR[i].end);
            if (s && e && now >= s.getTime() && now <= e.getTime()) {
                var rem = fmtCountdown(e.getTime() - now);
                solChip.textContent = '★ Major feeding window' + (rem ? ' — ends in ' + rem : ' now!');
                solChip.className = 'sol-countdown-chip sol-countdown-chip--active';
                solChip.hidden = false;
                return;
            }
        }
        for (var j = 0; j < SOL_MINOR.length; j++) {
            var ms = parseLocalTime(SOL_MINOR[j].start), me = parseLocalTime(SOL_MINOR[j].end);
            if (ms && me && now >= ms.getTime() && now <= me.getTime()) {
                var rem2 = fmtCountdown(me.getTime() - now);
                solChip.textContent = '● Minor feeding window' + (rem2 ? ' — ends in ' + rem2 : ' now!');
                solChip.className = 'sol-countdown-chip sol-countdown-chip--minor-active';
                solChip.hidden = false;
                return;
            }
        }
        var all = SOL_MAJOR.map(function (p) { return { t: parseLocalTime(p.start), major: true }; })
                  .concat(SOL_MINOR.map(function (p) { return { t: parseLocalTime(p.start), major: false }; }));
        all = all.filter(function (p) { return p.t && p.t.getTime() > now; });
        if (!all.length) { solChip.hidden = true; return; }
        all.sort(function (a, b) { return a.t - b.t; });
        var next = all[0], cd = fmtCountdown(next.t.getTime() - now);
        if (!cd) { solChip.hidden = true; return; }
        solChip.textContent = (next.major ? '★ Major period' : '● Minor period') + ' in ' + cd;
        solChip.className = 'sol-countdown-chip' + (next.major ? '' : ' sol-countdown-chip--minor');
        solChip.hidden = false;
    }

    // Tide countdown in conditions stat card
    var tideDetail = document.getElementById('tide-next-detail');
    function updateTide() {
        if (!tideDetail || !TIDES.length) return;
        var now = Date.now();
        var next = null;
        for (var i = 0; i < TIDES.length; i++) {
            var t = parseLocalTime(TIDES[i].time);
            if (t && t.getTime() > now) { next = { tide: TIDES[i], d: t }; break; }
        }
        if (!next) { tideDetail.textContent = 'current tidal flow'; return; }
        var cd = fmtCountdown(next.d.getTime() - now);
        if (!cd) { tideDetail.textContent = 'current tidal flow'; return; }
        var htStr = next.tide.height_ft != null ? ' (' + next.tide.height_ft + ' ft)' : '';
        tideDetail.textContent = next.tide.type + ' tide in ' + cd + htStr;
    }

    updateSolunar();
    updateTide();
    drawSolunarChart();
    setInterval(function () { updateSolunar(); updateTide(); drawSolunarChart(); }, 30000);
    window.addEventListener('resize', drawSolunarChart);
})();
