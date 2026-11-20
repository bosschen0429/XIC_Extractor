const $ = (id) => document.getElementById(id);
function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", `icon${cls ? ` ${cls}` : ""}`);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}
function toast(message, kind = "info", timeout) {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.setAttribute("role", kind === "error" ? "alert" : "status");
  const text = document.createElement("p");
  text.textContent = message;
  const close = document.createElement("button");
  close.type = "button";
  close.setAttribute("aria-label", "關閉訊息");
  close.append(icon("x"));
  close.addEventListener("click", () => node.remove());
  node.append(
    icon(kind === "error" ? "alert" : kind === "ok" ? "check-circle" : "info"),
    text,
    close,
  );
  const host = $("toasts");
  host.append(node);
  while (host.children.length > 4) host.firstElementChild.remove();
  const delay = timeout ?? (kind === "error" ? 0 : 5000);
  if (delay) setTimeout(() => node.remove(), delay);
  return node;
}
function confirm({ title, message, accept = "確定", danger = false }) {
  const dialog = $("confirm-dialog");
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = message;
  $("confirm-accept").textContent = accept;
  $("confirm-accept").className = danger ? "danger-button" : "primary";
  dialog.returnValue = "cancel";
  dialog.showModal();
  (danger
    ? dialog.querySelector('button[value="cancel"]')
    : $("confirm-accept")
  ).focus();
  return new Promise((resolve) =>
    dialog.addEventListener(
      "close",
      () => resolve(dialog.returnValue === "ok"),
      { once: true },
    ),
  );
}
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("xic-theme", theme);
  } catch (_) {}
}
function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h} 小時 ${m} 分`;
  if (m) return `${m} 分 ${String(s).padStart(2, "0")} 秒`;
  return `${s} 秒`;
}
const locations = new Map();
const readers = new Map();
const session = `${Date.now()}-${Math.random()}`;
let position = 0;
let revision = 0;
let restoring = false;
const capture = () => ({
  parts: [...readers].map(([name, reader]) => [name, reader.capture()]),
  scroll: window.scrollY,
});
const mark = () => ({ xicNavigation: session, position });
history.replaceState(mark(), "");
history.scrollRestoration = "manual";
const navigation = {
  get revision() { return revision; },
  register(name, reader) { readers.set(name, reader); },
  push() {
    if (restoring) return;
    locations.set(position, capture());
    for (const key of locations.keys()) if (key > position) locations.delete(key);
    position += 1;
    history.pushState(mark(), "");
  },
  back(fallback, matches = () => true) {
    for (let target = position - 1; target >= 0; target -= 1) {
      const location = locations.get(target);
      if (location && matches(Object.fromEntries(location.parts))) {
        history.go(target - position);
        return;
      }
    }
    fallback();
  },
};
window.addEventListener("popstate", (event) => {
  if (event.state?.xicNavigation !== session) return;
  revision += 1;
  locations.set(position, capture());
  position = event.state.position;
  const location = locations.get(position);
  if (!location) return;
  const currentRevision = revision;
  setTimeout(() => {
    if (revision !== currentRevision) return;
    restoring = true;
    try {
      for (const [name, value] of location.parts) readers.get(name)?.restore(value);
      requestAnimationFrame(() => window.scrollTo(0, location.scroll));
    } finally { restoring = false; }
  }, 0);
});
function trackWindow() {
  const windowId = crypto.randomUUID();
  let timer;
  const notify = (closed = false) => fetch("/api/window", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-XIC-Client": "workbench" },
    body: JSON.stringify({ window: windowId, closed }),
    keepalive: true,
  }).catch(() => {});
  const resume = () => {
    clearInterval(timer);
    notify();
    timer = setInterval(() => notify(), 30000);
  };
  const leave = () => { clearInterval(timer); notify(true); };
  const visible = () => { if (!document.hidden) notify(); };
  window.addEventListener("pageshow", resume);
  window.addEventListener("pagehide", leave);
  document.addEventListener("visibilitychange", visible);
  resume();
  return () => {
    clearInterval(timer);
    window.removeEventListener("pageshow", resume);
    window.removeEventListener("pagehide", leave);
    document.removeEventListener("visibilitychange", visible);
  };
}
async function api(path, values, signal, requestHeaders = {}) {
  const response = await fetch(path, {
    signal,
    method: values === undefined ? "GET" : "POST",
    headers: {
      "X-XIC-Client": "workbench",
      ...(values === undefined ? {} : { "Content-Type": "application/json" }),
      ...requestHeaders,
    },
    ...(values === undefined ? {} : { body: JSON.stringify(values) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw Object.assign(new Error(data.error || `要求失敗（${response.status}）`), {
      detail: data.detail || "",
    });
  return data;
}
async function openSession() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const capability = fragment.get("launch");
  const opened = await api(
    "/api/session",
    undefined,
    undefined,
    capability ? { "X-XIC-Launch": capability } : {},
  );
  if (capability !== null) {
    fragment.delete("launch");
    const remaining = fragment.toString();
    history.replaceState(
      history.state,
      "",
      `${window.location.pathname}${window.location.search}${remaining ? `#${remaining}` : ""}`,
    );
  }
  return opened;
}
export {
  api,
  icon,
  toast,
  confirm,
  setTheme,
  formatDuration,
  navigation,
  trackWindow,
  openSession,
};
