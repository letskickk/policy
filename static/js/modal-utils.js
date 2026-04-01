(function() {
  const modalState = new WeakMap();

  function getFocusableElements(root) {
    if (!root) return [];
    const selector = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    return Array.from(root.querySelectorAll(selector)).filter((element) => {
      if (element.hidden) return false;
      if (element.getAttribute('aria-hidden') === 'true') return false;
      return element.offsetParent !== null || getComputedStyle(element).position === 'fixed';
    });
  }

  function hasOpenModal() {
    return Array.from(document.querySelectorAll('[data-modal-open="true"]')).length > 0;
  }

  function open(modal, options) {
    if (!modal) return;
    const settings = Object.assign({
      activeClass: 'open',
      container: null,
      initialFocus: null,
      display: null,
      lockScroll: true,
      onEscape: null,
      restoreFocusTo: null,
    }, options || {});
    const state = modalState.get(modal) || {};
    const container = settings.container
      ? (typeof settings.container === 'string' ? modal.querySelector(settings.container) : settings.container)
      : modal;

    state.restoreFocusTo = settings.restoreFocusTo
      || (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    state.lockScroll = settings.lockScroll;
    state.onEscape = settings.onEscape;

    state.keyHandler = function(event) {
      if (event.key === 'Escape') {
        if (typeof state.onEscape === 'function') {
          event.preventDefault();
          state.onEscape();
        }
        return;
      }
      if (event.key !== 'Tab') return;
      const focusables = getFocusableElements(container);
      if (!focusables.length) {
        event.preventDefault();
        if (container && typeof container.focus === 'function') {
          container.focus();
        }
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      const activeInside = active instanceof HTMLElement && container.contains(active);
      if (event.shiftKey && (!activeInside || active === first)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (!activeInside || active === last)) {
        event.preventDefault();
        first.focus();
      }
    };

    modal.dataset.modalOpen = 'true';
    if (settings.activeClass) {
      modal.classList.add(settings.activeClass);
    }
    if (typeof settings.display === 'string') {
      modal.style.display = settings.display;
    } else if (modal.style.display === 'none') {
      modal.style.display = '';
    }
    modal.setAttribute('aria-hidden', 'false');
    if (container && !container.hasAttribute('tabindex')) {
      container.setAttribute('tabindex', '-1');
    }

    if (state.lockScroll) {
      document.body.style.overflow = 'hidden';
    }

    document.addEventListener('keydown', state.keyHandler, true);
    modalState.set(modal, state);

    setTimeout(function() {
      let target = null;
      if (settings.initialFocus) {
        target = typeof settings.initialFocus === 'string'
          ? modal.querySelector(settings.initialFocus)
          : settings.initialFocus;
      }
      if (!target) {
        const focusables = getFocusableElements(container);
        target = focusables[0] || container;
      }
      if (target && typeof target.focus === 'function') {
        target.focus({ preventScroll: true });
      }
    }, 24);
  }

  function close(modal, options) {
    if (!modal) return;
    const settings = Object.assign({
      activeClass: 'open',
      display: null,
      restoreFocus: true,
    }, options || {});
    const state = modalState.get(modal);

    modal.dataset.modalOpen = 'false';
    if (settings.activeClass) {
      modal.classList.remove(settings.activeClass);
    }
    if (typeof settings.display === 'string') {
      modal.style.display = settings.display;
    }
    modal.setAttribute('aria-hidden', 'true');

    if (state && state.keyHandler) {
      document.removeEventListener('keydown', state.keyHandler, true);
    }
    if (!hasOpenModal()) {
      document.body.style.overflow = '';
    }

    if (settings.restoreFocus !== false && state && state.restoreFocusTo && typeof state.restoreFocusTo.focus === 'function') {
      setTimeout(function() {
        state.restoreFocusTo.focus({ preventScroll: true });
      }, 24);
    }
    modalState.delete(modal);
  }

  window.PolicyModal = { open, close };
})();
