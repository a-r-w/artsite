// Cycling theme switch (Auto → Light → Dark → Auto). The pre-paint snippet in
// art/_theme-init.html applies a saved choice before first paint; this wires the
// toggle button(s), persists the choice in localStorage, and keeps the browser
// address-bar colour (the theme-color meta) in step. "auto" = follow the OS,
// expressed as the absence of data-theme so CSS's color-scheme handles it.

(function () {
  'use strict';

  var MODES = ['auto', 'light', 'dark'];
  var ICON = { auto: '◐', light: '☀', dark: '☾' };
  var LABEL = { auto: 'Auto', light: 'Light', dark: 'Dark' };
  var root = document.documentElement;

  function currentMode() {
    var t = root.getAttribute('data-theme');
    return t === 'light' || t === 'dark' ? t : 'auto';
  }

  function apply(mode) {
    if (mode === 'light' || mode === 'dark') {
      root.setAttribute('data-theme', mode);
    } else {
      root.removeAttribute('data-theme');
      mode = 'auto';
    }
    try { localStorage.setItem('theme', mode); } catch (e) {}
    render(mode);
  }

  function render(mode) {
    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
      btn.hidden = false;
      var icon = btn.querySelector('[data-theme-icon]');
      var label = btn.querySelector('[data-theme-label]');
      if (icon) icon.textContent = ICON[mode];
      if (label) label.textContent = LABEL[mode];
      btn.setAttribute('aria-label', 'Theme: ' + LABEL[mode] + ' — activate to change');
      btn.title = 'Theme: ' + LABEL[mode];
    });
    updateThemeColor();
  }

  function updateThemeColor() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta || !document.body) return;
    // The resolved page background, so the browser chrome matches the theme.
    var bg = getComputedStyle(document.body).backgroundColor;
    if (bg) meta.setAttribute('content', bg);
  }

  document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      apply(MODES[(MODES.indexOf(currentMode()) + 1) % MODES.length]);
    });
  });

  // While following the OS (auto), track its light/dark flips for theme-color.
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
      if (currentMode() === 'auto') updateThemeColor();
    });
  }

  render(currentMode());
})();
