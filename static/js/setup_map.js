(function () {
    'use strict';

    var DEFAULT_LAT = 35.0;
    var DEFAULT_LNG = -77.0;
    var DEFAULT_ZOOM = 5;

    function init() {
        var mapEl = document.getElementById('map');
        if (!mapEl) return;

        var latInput = document.getElementById('location_lat');
        var lonInput = document.getElementById('location_lon');
        var submitBtn = document.getElementById('map-submit-btn');
        var hint = document.getElementById('map-hint');

        function setHiddenCoords(lat, lng) {
            latInput.value = lat.toFixed(6);
            lonInput.value = lng.toFixed(6);
            submitBtn.disabled = false;
        }

        if (typeof L === 'undefined') {
            mapEl.classList.add('map-fallback');
            mapEl.innerHTML = '' +
                '<p><strong>Map unavailable.</strong> Your network or browser blocked the map library.</p>' +
                '<p>Enter coordinates instead, or use zip search below.</p>' +
                '<div class="map-fallback-controls">' +
                '<label>Latitude <input id="fallback-lat" type="number" step="0.000001" min="-90" max="90" placeholder="e.g. 34.2257"></label>' +
                '<label>Longitude <input id="fallback-lng" type="number" step="0.000001" min="-180" max="180" placeholder="e.g. -77.9447"></label>' +
                '<button type="button" id="fallback-coords-btn">Use Coordinates</button>' +
                '</div>';

            var fallbackLat = document.getElementById('fallback-lat');
            var fallbackLng = document.getElementById('fallback-lng');
            var fallbackBtn = document.getElementById('fallback-coords-btn');

            fallbackBtn.addEventListener('click', function () {
                var lat = Number(fallbackLat.value);
                var lng = Number(fallbackLng.value);
                var valid = Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180;
                if (!valid) {
                    if (hint) hint.textContent = 'Enter valid latitude (-90 to 90) and longitude (-180 to 180).';
                    return;
                }
                setHiddenCoords(lat, lng);
                if (hint) hint.textContent = 'Coordinates set at ' + lat.toFixed(4) + '\u00b0, ' + lng.toFixed(4) + '\u00b0. Press \u201cFind Nearest Location\u201d to continue.';
            });

            return;
        }

        var map = L.map('map').setView([DEFAULT_LAT, DEFAULT_LNG], DEFAULT_ZOOM);

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            maxZoom: 18
        }).addTo(map);

        var marker = null;

        function setPin(lat, lng) {
            if (marker) {
                marker.setLatLng([lat, lng]);
            } else {
                marker = L.marker([lat, lng]).addTo(map);
            }
            setHiddenCoords(lat, lng);
            if (hint) hint.textContent = 'Pin set at ' + lat.toFixed(4) + '\u00b0, ' + lng.toFixed(4) + '\u00b0. Press \u201cFind Nearest Location\u201d to continue.';
        }

        map.on('click', function (e) {
            setPin(e.latlng.lat, e.latlng.lng);
        });

        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                function (pos) {
                    var lat = pos.coords.latitude;
                    var lng = pos.coords.longitude;
                    map.setView([lat, lng], 8);
                },
                function () { /* permission denied or unavailable — stay at default */ }
            );
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
