(function () {
    'use strict';

    var DEFAULT_LAT = 35.0;
    var DEFAULT_LNG = -77.0;
    var DEFAULT_ZOOM = 5;

    function init() {
        var mapEl = document.getElementById('map');
        if (!mapEl || typeof L === 'undefined') return;

        var latInput = document.getElementById('location_lat');
        var lonInput = document.getElementById('location_lon');
        var submitBtn = document.getElementById('map-submit-btn');
        var hint = document.getElementById('map-hint');

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
            latInput.value = lat.toFixed(6);
            lonInput.value = lng.toFixed(6);
            submitBtn.disabled = false;
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
