import { el } from "./analysis_ui.js";

export function createRowSelection(options) {
  const startSelector = options.startSelector || options.rowSelector;
  const selection = {
    keys: new Set(),
    anchor: null,
    clear() {
      selection.keys = new Set();
      selection.anchor = null;
    },
    set(keys) {
      selection.keys = new Set(keys);
    },
    retain(known) {
      selection.keys = new Set([...selection.keys].filter((key) => known.has(key)));
      if (!known.has(selection.anchor)) selection.anchor = null;
    },
    click(key, event) {
      if (event.shiftKey && selection.anchor !== null) {
        const order = options.order();
        const [a, b] = [order.indexOf(selection.anchor), order.indexOf(key)].sort(
          (x, y) => x - y,
        );
        if (a >= 0) for (const each of order.slice(a, b + 1)) selection.keys.add(each);
      } else if (event.ctrlKey || event.metaKey) {
        if (!selection.keys.delete(key)) selection.keys.add(key);
        selection.anchor = key;
      } else {
        const only = selection.keys.size === 1 && selection.keys.has(key);
        selection.keys = only ? new Set() : new Set([key]);
        selection.anchor = key;
      }
      options.onChange();
    },
  };
  let drag = null;
  let suppressDragClick = false;

  function start(event) {
    suppressDragClick = false;
    const origin = event.target.closest(startSelector);
    if (!origin || event.button !== 0 || event.shiftKey) return;
    const box = options.box();
    const bounds = box.getBoundingClientRect();
    drag = {
      box,
      band: null,
      y0: event.clientY - bounds.top + box.scrollTop,
      base: event.ctrlKey || event.metaKey ? new Set(selection.keys) : new Set(),
      last: null,
      moved: false,
    };
    event.preventDefault();
    box.focus({ preventScroll: true });
  }
  function move(event) {
    if (!drag) return;
    const { box } = drag;
    const bounds = box.getBoundingClientRect();
    const y = event.clientY - bounds.top + box.scrollTop;
    if (!drag.moved && Math.abs(y - drag.y0) < 4) return;
    if (!drag.moved) {
      drag.moved = true;
      drag.band = el("div", undefined, "select-band");
      box.append(drag.band);
    }
    if (event.clientY > bounds.bottom - 28) box.scrollTop += 14;
    else if (event.clientY < bounds.top + 28) box.scrollTop -= 14;
    const y1 = Math.max(
      0,
      Math.min(event.clientY - bounds.top + box.scrollTop, box.scrollHeight),
    );
    const top = Math.min(drag.y0, y1);
    const bottom = Math.max(drag.y0, y1);
    drag.band.style.top = `${top}px`;
    drag.band.style.height = `${bottom - top}px`;
    const hit = new Set(drag.base);
    for (const tr of box.querySelectorAll(options.rowSelector)) {
      const rect = tr.getBoundingClientRect();
      const a = rect.top - bounds.top + box.scrollTop;
      if (a + rect.height > top && a < bottom) {
        const key = options.keyOf(tr);
        hit.add(key);
        drag.last = key;
      }
    }
    selection.keys = hit;
    options.onChange();
  }
  function end() {
    if (!drag) return;
    const ended = drag;
    drag = null;
    ended.band?.remove();
    if (ended.moved && ended.last !== null) {
      selection.anchor = ended.last;
      suppressDragClick = true;
    }
  }
  function onClick(event) {
    const tr = event.target.closest(options.rowSelector);
    if (!tr || !event.target.closest(startSelector)) return;
    if (suppressDragClick && event.detail > 0) {
      suppressDragClick = false;
      return;
    }
    selection.click(options.keyOf(tr), event);
  }
  selection.bind = (rows) => {
    rows.addEventListener("pointerdown", start);
    rows.addEventListener("click", onClick);
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", end);
  };
  return selection;
}
