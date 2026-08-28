// Curate admin JS: image dropzone, modal control, HTMX inline-create.

(function () {
  'use strict';

  // ---------- Image dropzone + preview ----------
  function wireDropzone(zone) {
    const input = zone.querySelector('input[type="file"]');
    const preview = zone.querySelector('[data-dropzone-preview]');
    if (!input) return;

    function showPreview(file) {
      if (!preview || !file || !file.type.startsWith('image/')) return;
      const reader = new FileReader();
      reader.onload = function (e) {
        preview.innerHTML = '';
        const img = document.createElement('img');
        img.src = e.target.result;
        img.alt = '';
        preview.appendChild(img);
      };
      reader.readAsDataURL(file);
    }

    input.addEventListener('change', function () {
      if (input.files && input.files[0]) showPreview(input.files[0]);
    });

    ['dragenter', 'dragover'].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        zone.classList.add('is-dragover');
      });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        zone.classList.remove('is-dragover');
      });
    });
    zone.addEventListener('drop', function (e) {
      // Defensive: also stop the browser's default "open file" navigation,
      // independent of the dragleave/drop loop above.
      e.preventDefault();
      const dt = e.dataTransfer;
      if (!dt || !dt.files || !dt.files.length) return;
      input.files = dt.files;
      // Notify any listeners (e.g. forms tracking dirty state).
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  document.querySelectorAll('[data-dropzone]').forEach(wireDropzone);

  // ---------- Modal control ----------
  const modal = document.getElementById('curate-modal');
  let modalTrigger = null; // the "+ New …" button that opened the modal

  function closeModal() {
    if (!modal) return;
    modal.innerHTML = '';
    document.body.classList.remove('curate-modal-open');
    // Restore focus to whatever opened the modal (a11y: return to the invoker).
    if (modalTrigger && typeof modalTrigger.focus === 'function') modalTrigger.focus();
    modalTrigger = null;
  }

  function modalFocusables() {
    return Array.prototype.slice
      .call(modal.querySelectorAll('a[href], button:not([disabled]), input:not([type="hidden"]), select, textarea'))
      .filter(function (el) {
        return el.offsetParent !== null;
      });
  }

  function openModalIfPopulated() {
    if (!modal) return;
    if (modal.children.length > 0) {
      document.body.classList.add('curate-modal-open');
      // Focus the first real field, not the × close button that precedes it.
      const field = modal.querySelector('input:not([type="hidden"]), select, textarea');
      (field || modal.querySelector('button')).focus();
    } else {
      document.body.classList.remove('curate-modal-open');
    }
  }

  if (modal) {
    // Remember the opener (an hx-get button targeting the modal) so focus can
    // be restored on close. The modal's own form uses hx-post, so it's skipped.
    document.body.addEventListener('htmx:beforeRequest', function (e) {
      const elt = e.detail && e.detail.elt;
      if (elt && elt.hasAttribute('hx-get') && elt.getAttribute('hx-target') === '#curate-modal') {
        modalTrigger = elt;
      }
    });

    document.addEventListener('keydown', function (e) {
      if (modal.children.length === 0) return;
      if (e.key === 'Escape') {
        closeModal();
        return;
      }
      if (e.key !== 'Tab') return;
      // Trap Tab within the open modal so focus can't drift to the page behind.
      const items = modalFocusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });

    // Close on explicit close affordances inside the modal.
    modal.addEventListener('click', function (e) {
      if (e.target.closest('[data-curate-modal-close]')) {
        e.preventDefault();
        closeModal();
      }
    });

    // When HTMX swaps content into the modal, mark it open + focus a field.
    modal.addEventListener('htmx:afterSwap', openModalIfPopulated);
  }

  // ---------- modelCreated: splice new option into target <select> ----------
  // Note: the JSON key is 'select_id' (not 'target') because htmx reserves
  // the 'target' key for event-retargeting.
  document.body.addEventListener('modelCreated', function (event) {
    const detail = event.detail;
    if (!detail || !detail.select_id) { closeModal(); return; }
    const select = document.getElementById(detail.select_id);
    if (select) {
      const option = new Option(detail.name, detail.id, true, true);
      select.add(option, 0);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    closeModal();
  });

  // ---------- Tidy filter URLs ----------
  // The GET filter forms submit every control, so unset ones become empty
  // query params (?q=&artist=&has_x=&...). Disable the empties just before
  // submit — disabled controls aren't serialised — for short, shareable URLs.
  document.querySelectorAll('form.curate-filters').forEach(function (form) {
    form.addEventListener('submit', function () {
      form.querySelectorAll('input[name], select[name]').forEach(function (field) {
        if (field.value === '') field.disabled = true;
      });
    });
  });

  // The disabling above is permanent for the page instance, so a Back/Forward
  // that restores this page from the bfcache would show the filter form frozen
  // with greyed-out controls. Re-enable them on a persisted pageshow.
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    document.querySelectorAll('form.curate-filters input[name][disabled], form.curate-filters select[name][disabled]').forEach(
      function (field) {
        field.disabled = false;
      }
    );
  });

  // ---------- "More" advanced-filters toggle ----------
  // The panel is server-rendered open when advanced filters are active, so a
  // filtered view still shows its controls if this never runs.
  document.querySelectorAll('[data-toggle-advanced]').forEach(function (btn) {
    const panel = document.getElementById(btn.getAttribute('aria-controls'));
    if (!panel) return;
    btn.addEventListener('click', function () {
      const opening = panel.hasAttribute('hidden');
      if (opening) {
        panel.removeAttribute('hidden');
      } else {
        panel.setAttribute('hidden', '');
      }
      btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
      // When collapsing with filters still active, signal how many are hidden.
      const count = parseInt(btn.getAttribute('data-active-count'), 10) || 0;
      btn.textContent = opening ? 'Less' : count > 0 ? 'More (' + count + ')' : 'More';
    });
  });

  // ---------- Unsaved-changes guard ----------
  // Warn before leaving a piece/lookup edit form with unsaved edits. Scoped to
  // the main forms only — not the modal, filter, login, or delete-confirm forms.
  document.querySelectorAll('form.curate-form:not(.curate-form--modal)').forEach(function (form) {
    let dirty = false;
    // Only count edits to controls this form actually owns. The Documents
    // fieldset nests a file input + Upload button that belong to a separate
    // upload form (via form="doc-add-form"); their change/input events bubble
    // up through this form's DOM but must NOT mark it dirty — otherwise
    // uploading a document (which submits that other form and navigates away)
    // trips the beforeunload warning even when nothing here was touched.
    const markDirty = function (e) { if (e.target.form === form) dirty = true; };
    form.addEventListener('input', markDirty);
    form.addEventListener('change', markDirty);
    form.addEventListener('submit', function () { dirty = false; });
    window.addEventListener('beforeunload', function (e) {
      if (dirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    });
  });
})();
