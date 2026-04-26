/* ============================================================
   contextmenu.js — Minimal vanilla JS context menu
   Supports one level of submenu, checkmarks, separators.
   ============================================================ */

const ContextMenu = (() => {

  let _root = null;
  let _submenuEl = null;
  let _submenuTimeout = null;

  function _getRoot() {
    if (!_root) {
      _root = document.createElement('div');
      _root.className = 'ctx-menu';
      _root.style.display = 'none';
      document.body.appendChild(_root);
    }
    return _root;
  }

  function hide() {
    if (_root) {
      _root.style.display = 'none';
      _root.innerHTML = '';
    }
    _hideSubmenu();
  }

  function _hideSubmenu() {
    if (_submenuEl) {
      _submenuEl.remove();
      _submenuEl = null;
    }
    if (_submenuTimeout) {
      clearTimeout(_submenuTimeout);
      _submenuTimeout = null;
    }
  }

  function _clamp(el, x, y) {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // Temporarily show to measure
    el.style.visibility = 'hidden';
    el.style.display = 'block';
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    el.style.display = 'none';
    el.style.visibility = '';
    return {
      left: (x + w > vw) ? Math.max(0, vw - w - 4) : x,
      top:  (y + h > vh) ? Math.max(0, vh - h - 4) : y,
    };
  }

  function _buildItems(container, items, depth) {
    items.forEach(item => {
      if (item.separator) {
        const sep = document.createElement('div');
        sep.className = 'ctx-menu-separator';
        container.appendChild(sep);
        return;
      }

      const el = document.createElement('div');
      el.className = 'ctx-menu-item' + (item.disabled ? ' disabled' : '');
      if (item.colorClass) el.classList.add(item.colorClass);

      const left = document.createElement('span');
      const check = document.createElement('span');
      check.className = 'ctx-menu-check';
      check.textContent = item.checked ? '\u2713' : '';
      left.appendChild(check);
      left.appendChild(document.createTextNode(item.label));

      el.appendChild(left);

      if (item.submenu && item.submenu.length) {
        const arrow = document.createElement('span');
        arrow.className = 'ctx-menu-submenu-arrow';
        arrow.textContent = '\u25B6';
        el.appendChild(arrow);

        el.addEventListener('mouseenter', (e) => {
          _hideSubmenu();
          const sub = document.createElement('div');
          sub.className = 'ctx-menu ctx-submenu';
          _buildItems(sub, item.submenu, depth + 1);
          el.style.position = 'relative';
          el.appendChild(sub);
          _submenuEl = sub;

          // Position: right of parent item, viewport-clamped
          const rect = el.getBoundingClientRect();
          sub.style.display = 'block';
          const sw = sub.offsetWidth;
          const sh = sub.offsetHeight;
          const vw = window.innerWidth;
          const vh = window.innerHeight;
          let left = rect.width;
          let top = 0;
          if (rect.right + sw > vw) left = -sw;
          if (rect.bottom + sh > vh) top = rect.height - sh;
          sub.style.left = left + 'px';
          sub.style.top = top + 'px';
        });

        el.addEventListener('mouseleave', (e) => {
          // Delay hide so user can move into submenu
          _submenuTimeout = setTimeout(() => {
            if (_submenuEl && !_submenuEl.matches(':hover')) {
              _hideSubmenu();
            }
          }, 120);
        });
      } else {
        el.addEventListener('mouseleave', () => {
          // Only clear submenu if not hovering into it
          _submenuTimeout = setTimeout(() => {
            if (_submenuEl && !_submenuEl.matches(':hover') && !el.contains(_submenuEl)) {
              _hideSubmenu();
            }
          }, 120);
        });
      }

      if (!item.disabled && !item.submenu && item.onClick) {
        el.addEventListener('click', (e) => {
          e.stopPropagation();
          hide();
          item.onClick();
        });
      }

      container.appendChild(el);
    });
  }

  function show(x, y, items) {
    const root = _getRoot();
    root.innerHTML = '';
    _hideSubmenu();

    _buildItems(root, items, 0);

    const pos = _clamp(root, x, y);
    root.style.left = pos.left + 'px';
    root.style.top = pos.top + 'px';
    root.style.display = 'block';
  }

  // Dismiss on outside click, Escape, scroll, resize
  document.addEventListener('mousedown', (e) => {
    if (_root && _root.style.display !== 'none' && !_root.contains(e.target)) {
      hide();
    }
  }, true);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hide();
  });

  window.addEventListener('scroll', hide, { capture: true, passive: true });
  window.addEventListener('resize', hide, { passive: true });

  return { show, hide };
})();


// Shared helper — builds the disposition submenu items for a drone.
// Used by both table.js and map.js so the menu is identical in both places.
function buildDispositionMenu(drone, onTag) {
  const disp = drone.disposition || 'unknown';
  return [{
    label: 'Disposition',
    submenu: [
      {
        label: 'Friendly',
        checked: disp === 'friendly',
        colorClass: 'disposition-friendly',
        onClick: () => onTag(drone, 'friendly'),
      },
      {
        label: 'Threat',
        checked: disp === 'threat',
        colorClass: 'disposition-threat',
        onClick: () => onTag(drone, 'threat'),
      },
      {
        label: 'Unknown',
        checked: disp === 'unknown',
        colorClass: 'disposition-unknown',
        onClick: () => onTag(drone, 'unknown'),
      },
    ],
  }];
}
