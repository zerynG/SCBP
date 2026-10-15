(function () {
    'use strict';

    function setCount(id, value) {
        var el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function loadCounters() {
        fetch('/api/sidebar/counters/', { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) return;
                setCount('sidebar-count-employees', data.employees || 0);
                setCount('sidebar-count-executors', data.executors || 0);
                setCount('sidebar-count-subcontractors', data.subcontractors || 0);
                setCount('sidebar-count-customers', data.customers || 0);
                setCount('sidebar-count-equipment', data.equipment || 0);
            })
            .catch(function () { /* silently ignore */ });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadCounters);
    } else {
        loadCounters();
    }
})();