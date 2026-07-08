// Live tide countdown
// Data global set by the inline script block before this file loads:
//   window._TIDES
(function () {
    var TIDES = window._TIDES || [];

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

    updateTide();
    setInterval(updateTide, 30000);
})();
