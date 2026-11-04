// Optional mobile search mode; desktop keeps the persistent input.
export function createSearchDisclosure({enabled}) {
  const bar = document.querySelector('#topbar');
  const input = document.querySelector('#search-input');
  const trigger = document.querySelector('#search-toggle');
  const dismiss = document.querySelector('#search-close');
  const mobile = matchMedia('(max-width: 600px)');
  let expanded = false, editing = false;

  function paint() {
    bar.dataset.searchOpen = String(expanded);
    trigger.setAttribute('aria-expanded', String(expanded));
  }
  function open() {
    if (!enabled()) return;
    expanded = mobile.matches;
    paint();
    input.focus();
  }
  function close({restoreFocus = true} = {}) {
    const wasExpanded = expanded;
    expanded = false;
    editing = false;
    paint();
    if (wasExpanded && restoreFocus && mobile.matches && enabled()) trigger.focus();
  }
  function sync() {
    const focused = document.activeElement;
    if (!enabled()) {
      close({restoreFocus: false});
    } else if (!mobile.matches) {
      const keepEditing = focused === input;
      close({restoreFocus: false});
      editing = keepEditing;
      if (focused === trigger || focused === dismiss) input.focus();
    } else if (expanded || editing || focused === input) {
      expanded = true;
      paint();
      if (editing) input.focus();
    }
  }
  trigger.addEventListener('click', open);
  dismiss.addEventListener('click', () => close());
  input.addEventListener('focus', () => { editing = true; });
  input.addEventListener('blur', () => {
    // A media query can hide and blur the input before its change event runs.
    if (!mobile.matches || expanded) editing = false;
  });
  mobile.addEventListener('change', sync);
  paint();
  return {open, close, sync, get isOpen() { return expanded; }};
}
