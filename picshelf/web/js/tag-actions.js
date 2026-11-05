// The existing edit button stays in the tab order. Touch has a discoverable
// reveal control; it moves focus to that same edit button, never another tag.
export function attachTagActions(view) {
  const rowFor = target => target instanceof Element ? target.closest('.entity-row') : null;
  const triggerFor = row => row?.querySelector('[data-tag-actions]');
  const editFor = row => row?.querySelector('[data-edit-tag]');
  function focusin(event) {
    const row = rowFor(event.target), trigger = triggerFor(row);
    if (!trigger) return;
    trigger.setAttribute('aria-expanded', 'true');
    if (event.target === trigger) editFor(row).focus();
  }
  function focusout(event) {
    const row = rowFor(event.target);
    if (row && !row.contains(event.relatedTarget)) triggerFor(row)?.setAttribute('aria-expanded', 'false');
  }
  function click(event) {
    const trigger = event.target.closest('[data-tag-actions]');
    if (trigger && view.contains(trigger)) editFor(rowFor(trigger)).focus();
  }
  function pointerdown(event) {
    const trigger = event.target.closest('[data-tag-actions]');
    if (trigger && view.contains(trigger)) {
      // Keep the reveal control visible until click; pointer focus must not
      // swap it for the edit button before the first tap is complete.
      event.preventDefault();
      return;
    }
    const focused = document.activeElement, row = rowFor(focused);
    if (row && view.contains(row) && !row.contains(event.target)) focused.blur();
  }
  function keydown(event) {
    if (event.key === 'Escape' && triggerFor(rowFor(event.target))) {
      event.preventDefault();
      event.stopPropagation();
      view.querySelector('input[type=search]').focus();
    }
  }
  view.addEventListener('focusin', focusin);
  view.addEventListener('focusout', focusout);
  view.addEventListener('click', click);
  view.addEventListener('keydown', keydown);
  document.addEventListener('pointerdown', pointerdown);
  return () => {
    view.removeEventListener('focusin', focusin);
    view.removeEventListener('focusout', focusout);
    view.removeEventListener('click', click);
    view.removeEventListener('keydown', keydown);
    document.removeEventListener('pointerdown', pointerdown);
  };
}
