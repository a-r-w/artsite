// Click (or keyboard-activate) the piece image to view it enlarged in a modal
// overlay. The overlay is an accessible dialog: focus moves into it on open,
// is trapped while it's open, and returns to the trigger on close.

var overlayEl = document.createElement('div');
var imageEl = document.createElement('img');
overlayEl.id = 'overlay';
overlayEl.setAttribute('role', 'dialog');
overlayEl.setAttribute('aria-modal', 'true');
overlayEl.setAttribute('aria-hidden', 'true');
overlayEl.tabIndex = -1;
overlayEl.appendChild(imageEl);
document.body.appendChild(overlayEl);

var lastTrigger = null;

function openLightbox(trigger) {
  imageEl.src = trigger.src;
  imageEl.alt = trigger.alt;
  overlayEl.setAttribute('aria-label', trigger.alt || 'Enlarged image');
  overlayEl.removeAttribute('aria-hidden');
  overlayEl.classList.add('show');
  lastTrigger = trigger;
  overlayEl.focus();
}

function closeLightbox() {
  if (!overlayEl.classList.contains('show')) return;
  overlayEl.classList.remove('show');
  overlayEl.setAttribute('aria-hidden', 'true');
  // Return focus to whatever opened the overlay (a11y: don't strand the user).
  if (lastTrigger && typeof lastTrigger.focus === 'function') lastTrigger.focus();
  lastTrigger = null;
}

document.querySelectorAll('#lightBox img').forEach(function (item) {
  // Make the thumbnail operable (and focusable, so focus can return here).
  item.setAttribute('role', 'button');
  if (!item.hasAttribute('tabindex')) item.tabIndex = 0;
  item.addEventListener('click', function (e) {
    e.preventDefault();
    openLightbox(item);
  });
  item.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      openLightbox(item);
    }
  });
});

overlayEl.addEventListener('click', function (e) {
  e.preventDefault();
  closeLightbox();
});

document.addEventListener('keydown', function (e) {
  if (!overlayEl.classList.contains('show')) return;
  if (e.key === 'Escape' || e.keyCode === 27) {
    e.preventDefault();
    closeLightbox();
  } else if (e.key === 'Tab') {
    // The overlay is the only focusable element while open — keep focus on it.
    e.preventDefault();
    overlayEl.focus();
  }
});
