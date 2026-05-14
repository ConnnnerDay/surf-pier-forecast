/* Water Quality section — fetches EPA WQP data and renders metrics */
(function () {
    var section    = document.getElementById('wq-section');
    if (!section) return;
    var lat        = section.dataset.lat;
    var lng        = section.dataset.lng;
    var state      = section.dataset.state || '';
    var loadingEl  = document.getElementById('wq-loading');
    var metricsEl  = document.getElementById('wq-metrics-grid');
    var advisoryEl = document.getElementById('wq-advisory');
    var advisoryTx = document.getElementById('wq-advisory-text');
    var sourceEl   = document.getElementById('wq-source');

    function hideSection() {
        var block = section.closest('.section-block');
        if (block) block.hidden = true;
    }

    if (!lat || !lng) { hideSection(); return; }

    var url = '/api/v1/geo/environmental?lat=' + lat + '&lng=' + lng +
              (state ? '&state=' + encodeURIComponent(state) : '');

    fetch(url)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            var wq = d && d.data && d.data.water_quality;
            var metrics = [];
            if (wq) {
                if (wq.temp_f        != null) metrics.push({ label: 'Water Temp', value: wq.temp_f + '°F' });
                if (wq.turbidity_ntu != null) metrics.push({ label: 'Clarity',    value: wq.turbidity_ntu + ' NTU' });
                if (wq.do_mg_l       != null) metrics.push({ label: 'Oxygen',     value: wq.do_mg_l + ' mg/L' });
                if (wq.salinity_ppt  != null) metrics.push({ label: 'Salinity',   value: wq.salinity_ppt + ' ppt' });
                if (wq.ph            != null) metrics.push({ label: 'pH',         value: wq.ph });
            }
            if (!metrics.length) { hideSection(); return; }

            if (metricsEl) {
                metricsEl.innerHTML = metrics.map(function (m) {
                    return '<div class="wq-metric">' +
                        '<span class="wq-metric-value">' + m.value + '</span>' +
                        '<span class="wq-metric-label">' + m.label + '</span>' +
                        '</div>';
                }).join('');
                metricsEl.hidden = false;
            }

            if (wq && wq.enterococcus_flag === 'advisory' && advisoryEl && advisoryTx) {
                advisoryTx.textContent = 'Beach advisory — enterococcus ' +
                    (wq.enterococcus_cfu_100ml != null ? wq.enterococcus_cfu_100ml + ' CFU/100 mL' : 'elevated') +
                    ' (EPA limit: 104)';
                advisoryEl.hidden = false;
            }

            if (sourceEl && wq && wq.source) {
                sourceEl.textContent = 'Source: ' + wq.source +
                    (wq.station_count ? ' · ' + wq.station_count + ' station' + (wq.station_count > 1 ? 's' : '') : '');
                sourceEl.hidden = false;
            }

            if (loadingEl) loadingEl.hidden = true;
        })
        .catch(function () { hideSection(); });
})();
