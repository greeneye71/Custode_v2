/**
 * MedInventory - Minimal JavaScript
 * Handles: flash message auto-dismiss, HTMX configuration
 */

document.addEventListener('DOMContentLoaded', function () {

    // ---- Auto-dismiss flash messages after 5 seconds ----
    const alerts = document.querySelectorAll('.flash-container .alert');
    alerts.forEach(function (alert) {
        setTimeout(function () {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            if (bsAlert) {
                bsAlert.close();
            }
        }, 5000);
    });

});

// ---- HTMX: add loading class to body during requests ----
document.body.addEventListener('htmx:beforeRequest', function () {
    // Could add a global spinner here if needed
});

document.body.addEventListener('htmx:afterRequest', function () {
    // Could remove a global spinner here
});

// ---- HTMX: re-initialize Bootstrap tooltips after content swap ----
document.body.addEventListener('htmx:afterSwap', function (event) {
    // Re-init tooltips in the swapped content
    const tooltipTriggerList = event.target.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggerList.forEach(function (el) {
        new bootstrap.Tooltip(el);
    });
});
