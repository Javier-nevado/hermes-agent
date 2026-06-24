<?php
// PWA head tags: installable manifest, theme-color (light/dark), touch icon,
// and service-worker registration. The SW is served at its plugin-asset path
// with `Service-Worker-Allowed: /` (nginx sw-location) so it can control "/".
$base = rtrim($this->url->dir(), '/');
$pwa  = $base . '/plugins/OpteiaSkin/Asset/pwa';
?>
<link rel="manifest" href="<?= $pwa ?>/manifest.json">
<link rel="apple-touch-icon" href="<?= $pwa ?>/apple-touch-icon.png">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#7b68ee">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0b1219">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Opteia Kanban">
<script>
if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
        navigator.serviceWorker.register('<?= $pwa ?>/sw.js', { scope: '/' })
            .catch(function (e) { console.warn('Opteia SW registration failed:', e); });
    });
}
</script>
