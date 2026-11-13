// Workbay - the core: helpers, the API, shared state, tabs, events.
// The views are in ws.js (Workspaces + manifest), drawer.js (one repo), jobs.js, settings.js;
// each registers itself in VIEWS.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// h("div.a.b#id", {onclick}, child, ...): an element; strings become text, null is skipped.
function h(tag, attrs, ...kids) {
  const [base, id] = tag.split("#");
  const [name, ...classes] = base.split(".");
  const el = document.createElement(name || "div");
  if (classes.length) el.className = classes.join(" ");
  if (id) el.id = id;
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    kids.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className += (el.className ? " " : "") + v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "value") el.value = v;           // a textarea has no value attribute
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function ago(t) {
  if (!t) return "从未";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

function toast(msg, bad = false) {
  const t = h("div.t" + (bad ? ".bad" : ""), msg);
  $("#toast").append(t);
  setTimeout(() => t.remove(), bad ? 7000 : 3000);
}

// --- the API ---
class ApiError extends Error {}

async function call(name, args = {}, method = "POST") {
  let url = "/api/" + name, opts = { method, headers: {} };
  if (method === "GET") {
    const q = new URLSearchParams(Object.entries(args).filter(([, v]) => v != null && v !== false));
    if ([...q].length) url += "?" + q;
  } else {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(args);
  }
  let res, data;
  try {
    res = await fetch(url, opts);
    data = await res.json();
  } catch (e) {
    throw new ApiError("连不上 Workbay 服务（窗口关了？）");
  }
  if (!res.ok || data.error) throw new ApiError(data.error || `HTTP ${res.status}`);
  return data;
}
const get = (name, args) => call(name, args, "GET");

// call(), and the error as a toast; null on error.
async function act(name, args, method) {
  try {
    return await call(name, args, method);
  } catch (e) {
    toast(e.message, true);
    return null;
  }
}

// A write that may answer {need_confirm}: ask, then call again with confirm.
async function confirmed(name, args, title = "确认执行？") {
  let r = await act(name, args);
  if (r && r.need_confirm) {
    const ok = await ask(title, h("div", r.need_confirm.map(x => h("div.risk", x)),
      r.skipped && r.skipped.length ? h("div.muted.small", "跳过：" + r.skipped.map(s => `${s[0]}（${s[1]}）`).join("；")) : null),
      "仍然执行", true);
    if (!ok) return null;
    r = await act(name, { ...args, confirm: true });
  }
  if (r && r.job) {
    toast(`已开始：${r.job.title}`);
    if (r.skipped && r.skipped.length) toast("跳过：" + r.skipped.map(s => `${s[0]}（${s[1]}）`).join("；"));
  }
  return r;
}

// --- modal / menu ---
function modal(title, body, buttons, wide = false) {
  const m = $("#modal");
  const close = () => { m.hidden = true; m.replaceChildren(); document.removeEventListener("keydown", onKey); };
  const onKey = e => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  const foot = h("div.dlg-foot", buttons(close));
  m.replaceChildren(h("div.dlg" + (wide ? ".wide" : ""), { onclick: e => e.stopPropagation() },
    h("div.dlg-head", title, h("div.spacer"), h("button.icon", { onclick: close }, "✕")),
    h("div.dlg-body", body), foot));
  m.onclick = close;
  m.hidden = false;
  return close;
}

function ask(title, body, okText = "确定", danger = false) {
  return new Promise(resolve => {
    modal(title, body, close => [
      h("button.ghost", { onclick: () => { close(); resolve(false); } }, "取消"),
      h(danger ? "button.danger" : "button.primary", { onclick: () => { close(); resolve(true); } }, okText),
    ]);
  });
}

function menu(anchor, items) {
  closeMenu();
  const m = h("div.menu", items.map(it => it === "-" ? h("hr") : it.label && !it.run ? h("div.label", it.label)
    : h("button", { onclick: () => { closeMenu(); it.run(); }, disabled: it.disabled }, it.text)));
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  const left = Math.min(r.left, innerWidth - m.offsetWidth - 8);
  const top = r.bottom + m.offsetHeight > innerHeight ? r.top - m.offsetHeight : r.bottom + 2;
  Object.assign(m.style, { left: left + "px", top: Math.max(4, top) + "px" });
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }));
}
function closeMenu() { $$(".menu").forEach(m => m.remove()); }

// The open menu of a workspace / repo.
function openItems(ws, repo) {
  const o = what => () => act("open", { what, ws: ws.id, repo: repo && repo.id }).then(r => r && toast("已打开"));
  const tools = S.env ? S.env.tools : {};
  return [
    { text: "✳ Claude Code", run: o("claude") },
    { text: "🐚 Git Bash", run: o("gitbash"), disabled: tools && !tools.gitbash },
    { text: "⌨ 终端（设置里的）", run: o("terminal") },
    { text: "📝 VS Code", run: o("vscode"), disabled: tools && !tools.vscode },
    { text: "📁 资源管理器", run: o("explorer") },
    ...(repo ? ["-", { text: "gitk --all", run: o("gitk"), disabled: tools && !tools.gitk }, { text: "git gui", run: o("gitgui") }] : []),
    "-",
    { text: "📋 复制路径", run: () => navigator.clipboard.writeText(repo ? repo.abspath : ws.path).then(() => toast("已复制")) },
  ];
}

// the page's own state from when this tool was called Apiary (same origin, same port)
for (const k of Object.keys(localStorage))
  if (k.startsWith("apiary.")) {
    const nk = "workbay." + k.slice(7);
    if (localStorage.getItem(nk) == null) localStorage.setItem(nk, localStorage.getItem(k));
    localStorage.removeItem(k);
  }

// --- shared state ---
const S = {
  workspaces: [],        // from /api/workspaces
  status: {},            // ws_id -> {summary, busy, repos: {repo_id: status}}
  loading: new Set(),    // ws ids being read
  open: new Set(JSON.parse(localStorage.getItem("workbay.open") || "[]")),
  selected: {},          // ws_id -> Set(repo_id)
  settings: null,
  env: null,
  jobs: [],
  view: localStorage.getItem("workbay.view") || "workspaces",
};
const ws_by_id = id => S.workspaces.find(w => w.id === id);
const saveOpen = () => localStorage.setItem("workbay.open", JSON.stringify([...S.open]));

const VIEWS = {};       // name -> {title, render(main), badge?()}

function render() {
  const nav = $("#tabs");
  nav.replaceChildren(...Object.entries(VIEWS).map(([name, v]) =>
    h("button" + (S.view === name ? ".on" : ""), { onclick: () => show(name) }, v.title,
      v.badge && v.badge() ? h("span.pill.count.info", v.badge()) : null)));
  const main = $("#views");
  main.replaceChildren();
  if (!VIEWS[S.view]) S.view = "workspaces";
  VIEWS[S.view].render(main);
}

function show(name) {
  S.view = name;
  localStorage.setItem("workbay.view", name);
  render();
}

function rerender(view) {
  if (!view || S.view === view) render();
  else renderTabs();
}
function renderTabs() {
  const v = S.view;
  $$("#tabs button").forEach((b, i) => {
    const [name, def] = Object.entries(VIEWS)[i];
    const pill = $(".count", b);
    const n = def.badge && def.badge();
    if (pill && !n) pill.remove();
    else if (n) (pill || b.appendChild(h("span.pill.count.info"))).textContent = n;
    b.classList.toggle("on", name === v);
  });
}

// --- job events (SSE) ---
const JOB_LISTENERS = [];
function onJobEvent(fn) { JOB_LISTENERS.push(fn); }

function followEvents() {
  let from = Number(sessionStorage.getItem("workbay.ev") || 0);
  const es = new EventSource("/api/events?from=" + from);
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    for (const fn of JOB_LISTENERS) fn(ev);
  };
  es.addEventListener("index", e => sessionStorage.setItem("workbay.ev", e.data));
  es.onerror = () => { es.close(); setTimeout(followEvents, 3000); };
}

// Resolves when job id ends (its last state).
function waitJob(id) {
  return new Promise(resolve => {
    const fn = ev => {
      if (ev.job === id && ev.type === "job" && ["ok", "failed", "cancelled"].includes(ev.state)) {
        JOB_LISTENERS.splice(JOB_LISTENERS.indexOf(fn), 1);
        resolve(ev.state);
      }
    };
    onJobEvent(fn);
  });
}

// --- theme ---
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("workbay.theme", t);
}
$("#theme").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
setTheme(localStorage.getItem("workbay.theme") || "dark");

// --- start ---
async function boot() {
  const [env, settings] = await Promise.all([act("env", {}, "GET"), act("settings", {}, "GET")]);
  S.env = env;
  S.settings = settings && settings.settings;
  if (env && !env.git) {
    const b = $("#banner");
    b.hidden = false;
    b.textContent = "找不到 git：请安装 Git for Windows 并加入 PATH，然后重启 Workbay。";
  }
  followEvents();
  render();
  for (const fn of BOOT) fn();
}
const BOOT = [];        // views add their start-up work
window.addEventListener("load", boot);
