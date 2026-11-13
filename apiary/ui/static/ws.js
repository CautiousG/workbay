// Workspaces view: the cards, their repo tables, the batch bar, west update / command
// dialogs and the manifest panel.
"use strict";

const F = { text: localStorage.getItem("apiary.filter") || "", kind: "", attention: false };
let lastRead = 0, reading = false;

async function loadWorkspaces(rescan = false) {
  const r = await act(rescan ? "rescan" : "workspaces", {}, rescan ? "POST" : "GET");
  if (!r) return;
  S.workspaces = r.workspaces;
  rerender("workspaces");
  await readStatus();
}

// Statuses, a workspace per request so the first cards fill in at once.
async function readStatus(ids) {
  if (reading) return;
  reading = true;
  if (!ids) lastFull = Date.now();
  const todo = ids || S.workspaces.map(w => w.id);
  todo.forEach(id => { S.loading.add(id); paintCard(id); });
  // the open / troubled ones first
  todo.sort((a, b) => (S.open.has(b) - S.open.has(a)) || (attention(b) - attention(a)));
  const queue = [...todo];
  const worker = async () => {
    while (queue.length) {
      const id = queue.shift();
      const r = await act("status", { ws: [id] });
      S.loading.delete(id);
      if (r) Object.assign(S.status, r.status);
      paintCard(id);
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  lastRead = Date.now();
  reading = false;
  const stamp = $("#stamp");
  if (stamp) stamp.textContent = "状态：" + new Date(lastRead).toLocaleTimeString();
}

function attention(id) {
  const st = S.status[id];
  return st ? st.summary.attention : 0;
}

function shown() {
  const t = F.text.trim().toLowerCase();
  return S.workspaces.filter(w =>
    (!F.kind || w.kind === F.kind) &&
    (!F.attention || attention(w.id) > 0 || w.error) &&
    (!t || w.name.toLowerCase().includes(t) || w.repos.some(r => r.name.toLowerCase().includes(t))))
    .sort((a, b) => (attention(b.id) > 0) - (attention(a.id) > 0) || a.name.localeCompare(b.name));
}

VIEWS.workspaces = {
  title: "Workspaces",
  render(main) {
    const filter = h("input", { type: "text", placeholder: "过滤 workspace / repo", value: F.text,
      oninput: e => { F.text = e.target.value; localStorage.setItem("apiary.filter", F.text); paintList(); } });
    const kind = h("select", { onchange: e => { F.kind = e.target.value; paintList(); } },
      [["", "全部类型"], ["west", "west"], ["multi", "multi"], ["single", "single"]].map(([v, t]) =>
        h("option", { value: v, selected: F.kind === v }, t)));
    const att = h("label.row", h("input", { type: "checkbox", checked: F.attention,
      onchange: e => { F.attention = e.target.checked; paintList(); } }), "只看有问题");
    main.append(
      h("div.toolbar", filter, kind, att, h("div.spacer"),
        h("span.stamp#stamp", lastRead ? "状态：" + new Date(lastRead).toLocaleTimeString() : ""),
        h("button.ghost", { onclick: () => readStatus(), title: "重新读本地状态（不联网）" }, "⟳ 刷新"),
        h("button.ghost", { onclick: () => loadWorkspaces(true), title: "重新扫描目录" }, "⌕ 重新扫描"),
        h("button.primary", { onclick: fetchAll }, "⇣ Fetch 全部")),
      h("div#batch"),
      h("div#wslist"));
    paintList();
  },
};

async function fetchAll() {
  if (!await ask("Fetch 全部", h("div", `对 ${S.workspaces.length} 个 workspace 的所有仓库执行 git fetch --all --prune，可能需要几分钟。`))) return;
  const r = await act("fetch_all");
  if (r) toast(`已开始 ${r.jobs.length} 个 fetch 任务` + (r.skipped.length ? `，跳过忙碌的：${r.skipped.join("、")}` : ""));
}

function paintList() {
  const list = $("#wslist");
  if (!list) return;
  const wss = shown();
  if (!S.workspaces.length) {
    list.replaceChildren(h("div.empty", "没有找到 workspace。去 ", h("a", { href: "#", onclick: () => show("settings") }, "Settings"), " 配置扫描目录。"));
    return;
  }
  list.replaceChildren(...wss.map(card));
  if (!wss.length) list.append(h("div.empty", "没有符合过滤条件的 workspace"));
  paintBatch();
}

function paintCard(id) {
  const old = $(`.ws[data-id="${CSS.escape(id)}"]`);
  const ws = ws_by_id(id);
  if (old && ws) old.replaceWith(card(ws));
  paintBatch();
}

function badges(ws) {
  const st = S.status[ws.id];
  if (S.loading.has(ws.id) && !st) return [h("span.spin")];
  if (!st) return [];
  const s = st.summary, out = [];
  if (S.loading.has(ws.id)) out.push(h("span.spin"));
  if (st.busy) out.push(h("span.pill.info", "⏵ 执行中"));
  if (s.stuck) out.push(h("span.pill.bad", { title: "rebase / merge 做了一半或有冲突" }, `⛔ ${s.stuck} 卡住`));
  if (s.errors) out.push(h("span.pill.bad", `⚠ ${s.errors} 读取失败`));
  if (s.dirty) out.push(h("span.pill.warn", `✎ ${s.dirty} dirty`));
  if (s.off_manifest) out.push(h("span.pill.warn", { title: "HEAD 与 manifest-rev 分叉或落后" }, `⚑ ${s.off_manifest} 偏离 manifest`));
  if (s.not_cloned) out.push(h("span.pill", `○ ${s.not_cloned} 未 clone`));
  if (s.behind) out.push(h("span.pill.honey", `↓ ${s.behind} behind`));
  if (s.ahead) out.push(h("span.pill.info", `↑ ${s.ahead} ahead`));
  if (!s.attention && !s.errors && !s.ahead) out.push(h("span.pill.ok", "✓ clean"));
  out.push(h("span.pill", { title: "最久未 fetch 的仓库" }, `fetch ${ago(s.fetched_at)}`));
  return out;
}

function card(ws) {
  const st = S.status[ws.id];
  const isOpen = S.open.has(ws.id);
  const stop = fn => e => { e.stopPropagation(); fn(e); };
  const head = h("div.ws-head", {
    onclick: () => { isOpen ? S.open.delete(ws.id) : S.open.add(ws.id); saveOpen(); paintCard(ws.id); },
  },
    h("span.caret", "▶"),
    h("span.ws-name", ws.name),
    h("span.kind." + ws.kind, ws.kind),
    h("span.muted.small", `${ws.repos.length} repos`),
    ws.manifest ? h("button.ws-path.mono.ymlpick", { title: ws.manifest.file + "\n点击切换 yml（改 .west/config 的 manifest.file）",
      onclick: stop(e => pickManifest(e.currentTarget, ws)) }, shortManifest(ws), " ▾") : h("span.ws-path", { title: ws.path }, ws.path),
    h("div.ws-badges", badges(ws)),
    h("div.ws-actions",
      ws.kind === "west" && !ws.error ? [
        h("button.btn.primary", { onclick: stop(() => syncDialog(ws)), title: "fetch 一次，切到 manifest 的分支并 rebase；出错的仓库回滚、标出" }, "⇄ 同步到 manifest"),
        h("button.btn", { onclick: stop(() => updateDialog(ws)) }, "west update"),
        h("button.btn", { onclick: stop(() => manifestPanel(ws)) }, "Manifest"),
      ] : null,
      h("button.btn", { onclick: stop(() => act("fetch", { ws: ws.id }).then(r => r && toast(`已开始：${r.job.title}`))) }, "Fetch"),
      h("button.btn", { title: "gcc 编译这个 workspace 的 IC 的 defconfig 工程（RTL87x2L 可 J-Link 烧录）",
        onclick: stop(() => buildPanel(ws)) }, "⚒ 编译"),
      h("button.btn", { onclick: stop(() => commandDialog(ws)) }, "命令…"),
      h("button.btn.claude", { title: "在 workspace 根目录打开 Claude Code",
        onclick: stop(() => act("open", { what: "claude", ws: ws.id }).then(r => r && toast("已打开 Claude Code"))) }, "✳ Claude"),
      h("button.btn", { onclick: stop(e => openMenu(e.currentTarget, ws)) }, "打开 ▾")));
  const el = h("div.ws" + (isOpen ? ".open" : "") + (st && st.summary.attention ? ".attention" : "")
    + (ws.error || (st && st.summary.errors) ? ".broken" : "") + (st && st.busy ? ".busy" : ""),
    { "data-id": ws.id }, head,
    ws.error ? h("div.ws-err", ws.error) : null,
    isOpen ? h("div.ws-body", stuckBar(ws), repoTable(ws)) : null);
  return el;
}

function shortManifest(ws) {
  const f = ws.manifest.file, r = ws.manifest.repo;
  return "manifest: " + (f.toLowerCase().startsWith(r.toLowerCase()) ? f.slice(r.length + 1) : f);
}

async function openMenu(anchor, ws) {
  const items = openItems(ws, null);
  const files = await act("open_files", { ws: ws.id }, "GET");
  if (files && files.files.length) {
    items.push("-", { label: "工程文件" });
    for (const f of files.files.slice(0, 12))
      items.push({ text: "▸ " + f.slice(ws.path.length + 1),
        run: () => act("open", { ws: ws.id, file: f }).then(r => r && toast("已打开")) });
  }
  items.push("-", { text: "🙈 隐藏这个 workspace", run: () => hideWs(ws) });
  menu(anchor, items);
}

async function hideWs(ws) {
  if (!await ask("隐藏 workspace", `${ws.path} 将不再列出（可在 Settings 恢复）。`)) return;
  const r = await act("hide", { ws: ws.id });
  if (r) { S.settings = r.settings; loadWorkspaces(); }
}

function repoTable(ws) {
  const st = (S.status[ws.id] || {}).repos || {};
  const sel = S.selected[ws.id] || (S.selected[ws.id] = new Set());
  const west = ws.kind === "west";
  const all = h("input", { type: "checkbox", checked: sel.size && sel.size === ws.repos.filter(r => r.cloned).length,
    onclick: e => e.stopPropagation(),
    onchange: e => { ws.repos.forEach(r => r.cloned && (e.target.checked ? sel.add(r.id) : sel.delete(r.id))); paintCard(ws.id); } });
  const rows = ws.repos.map(r => repoRow(ws, r, st[r.id], sel, west));
  return h("table.repos",
    h("thead", h("tr", h("th", all), h("th", "repo"), h("th", "分支 / HEAD"), h("th", "改动"), h("th", "↑↓"),
      west ? h("th", "manifest") : null, h("th", "fetch"), h("th", ""))),
    h("tbody", rows));
}

function repoRow(ws, r, st, sel, west) {
  const stop = fn => e => { e.stopPropagation(); fn(e); };
  const check = h("input", { type: "checkbox", checked: sel.has(r.id), disabled: !r.cloned, onclick: e => e.stopPropagation(),
    onchange: e => { e.target.checked ? sel.add(r.id) : sel.delete(r.id); paintBatch(); e.target.closest("tr").classList.toggle("sel", e.target.checked); } });
  const name = h("td.name", r.name, r.path !== r.name && r.path !== "." ? h("span.p", r.path) : null,
    r.role === "manifest" ? h("span.pill.honey", { style: "margin-left:6px" }, "manifest") : null,
    r.role === "top" ? h("span.pill", { style: "margin-left:6px" }, "顶层") : null);
  if (!r.cloned) {
    const why = r.declared && !r.declared.active ? "被 group-filter 排除" : "未 clone（west update 会 clone）";
    return h("tr.notcloned", h("td", check), name, h("td", { colspan: west ? 6 : 5 }, "○ " + why));
  }
  if (!st) return h("tr", h("td", check), name, h("td", { colspan: west ? 6 : 5 }, h("span.spin")));
  if (st.error) return h("tr", { onclick: () => openRepo(ws, r) }, h("td", check), name,
    h("td", { colspan: west ? 6 : 5 }, h("span.st-err", { title: st.error }, "⚠ " + st.error.split("\n")[0])));
  const branch = h("span", st.branch ? h("span", st.branch) : h("span.muted", `(detached ${st.head})`),
    st.stuck ? h("span.pill.bad.stuckpill", { title: st.stuck.text + "（点击选择处理方式）",
      onclick: stop(e => menu(e.currentTarget, stuckItems(ws, r, st.stuck))) }, "⛔ " + st.stuck.text) : null);
  const chg = [];
  if (st.conflicted) chg.push(h("span.pill.bad", `冲突 ${st.conflicted}`));
  if (st.staged) chg.push(h("span.pill.ok", `S ${st.staged}`));
  if (st.modified) chg.push(h("span.pill.warn", `M ${st.modified}`));
  if (st.untracked) chg.push(h("span.pill", `? ${st.untracked}`));
  if (!chg.length) chg.push(h("span.muted", "clean"));
  const ab = st.ahead == null ? h("span.muted.small", st.branch ? "无 upstream" : "–")
    : h("span.ab", h("span" + (st.ahead ? ".up" : ""), `↑${st.ahead}`), " ", h("span" + (st.behind ? ".down" : ""), `↓${st.behind}`));
  let mf = null;
  if (west) {
    mf = r.role !== "project" ? h("td.mf-none", "–")
      : st.on_manifest == null ? h("td.mf-none", { title: "没有 manifest-rev，可能从未 west update" }, "○")
      : st.manifest_ahead ? h("td.mf-ok", { title: `HEAD 在 manifest-rev ${st.manifest_rev} 之后继续前进（没有丢东西）` }, "↗ 领先")
      : st.on_manifest ? h("td.mf-ok", { title: `manifest-rev ${st.manifest_rev}` }, "✓")
      : h("td.mf-off", { title: `HEAD ${st.head} ≠ manifest-rev ${st.manifest_rev}` }, "⚑ 偏离");
  }
  return h("tr" + (sel.has(r.id) ? ".sel" : ""), { onclick: () => openRepo(ws, r) },
    h("td", check), name, h("td", branch), h("td", chg), h("td", ab), mf,
    h("td.muted.small", ago(st.fetched_at)),
    h("td.act",
      h("button.icon", { title: "fetch", onclick: stop(() => act("fetch", { ws: ws.id, repos: [r.id] }).then(x => x && toast(`已开始：${x.job.title}`))) }, "⇣"),
      h("button.icon", { title: "pull --rebase", onclick: stop(() => confirmed("pull", { ws: ws.id, repos: [r.id] }, "pull --rebase")) }, "⤓"),
      h("button.icon", { title: "打开…", onclick: stop(e => menu(e.currentTarget, openItems(ws, r))) }, "⋯")));
}
