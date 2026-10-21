// Shared focus and keyboard handling for nested dialogs, viewers and slideshows.
const stack = [];
const hasOpenSelector = CSS.supports("selector(select:open)");
const focusable = 'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';

export function isTopDialog(root) { return stack.at(-1)?.root === root; }

// Authentication loss must also release background media, timers and focus locks.
export function dismissDialogs() {
  for (const entry of [...stack].reverse()) entry.dismiss();
}

export function activateDialog(root, close, {dismiss = close} = {}) {
  const opener = document.activeElement;
  const cardId = opener?.closest?.("[data-id]")?.dataset.id;
  const inert = new Map();
  for (let node = root; node.parentElement; node = node.parentElement) {
    // A nested modal may live in a container made inert by its parent viewer.
    if (node.inert) {
      inert.set(node, true);
      node.inert = false;
    }
    for (const sibling of node.parentElement.children) {
      if (sibling !== node && sibling instanceof HTMLElement) {
        inert.set(sibling, sibling.inert);
        sibling.inert = true;
      }
    }
    if (node.parentElement === document.body) break;
  }
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  root.tabIndex = -1;
  const entry = {root, dismiss};
  stack.push(entry);
  document.body.classList.add("dialog-open");
  const targets = () => [...root.querySelectorAll(focusable)]
    .filter(el => !el.disabled && !el.closest("[hidden], [inert]") && el.getClientRects().length);
  const focus = () => (targets()[0] || root).focus({ preventScroll: true });
  function onKey(event) {
    if (!isTopDialog(root) || event.isComposing || event.keyCode === 229) return;
    // The picker is a layer within this dialog; Escape dismisses it first.
    if (hasOpenSelector && root.querySelector('select:open')) {
      if (event.key === "Escape") event.stopImmediatePropagation();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      close();
    } else if (event.key === "Tab") {
      const items = targets();
      const index = items.indexOf(document.activeElement);
      if (!items.length || index < 0 || (event.shiftKey ? index === 0 : index === items.length - 1)) {
        event.preventDefault();
        (event.shiftKey ? items.at(-1) || root : items[0] || root).focus();
      }
    }
  }
  document.addEventListener("keydown", onKey, true);
  queueMicrotask(() => {
    if (root.isConnected && isTopDialog(root) && !root.contains(document.activeElement)) focus();
  });
  let released = false;
  return () => {
    if (released) return;
    released = true;
    document.removeEventListener("keydown", onKey, true);
    stack.splice(stack.indexOf(entry), 1);
    for (const [node, previous] of inert) node.inert = previous;
    if (!stack.length) document.body.classList.remove("dialog-open");
    const target = opener?.isConnected ? opener : cardId
      ? document.querySelector(`[data-id="${CSS.escape(cardId)}"]`) : null;
    target?.focus({ preventScroll: true });
  };
}
