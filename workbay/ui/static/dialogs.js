// The Workspaces view's batch bar and dialogs: west update, custom command, manifest
// panel; and the auto refresh.
"use strict";

// --- batch bar: the checked repos of one workspace ---
function paintBatch() {
  const bar = $("#batch");
  if (!bar) return;
  const entry = Object.entries(S.selected).find(([, s]) => s.size);
  if (!entry) { bar.replaceChildren(); bar.className = ""; return; }
  const [wsId, sel] = entry;
  const ws = ws_by_id(wsId);
  if (!ws) { delete S.selected[wsId]; return paintBatch(); }
  // one batch at a time: other workspaces' selections go
  for (const [other, s] of Object.entries(S.selected)) {
    if (other !== wsId && s.size) { s.clear(); const c = $(`.ws[data-id="${CSS.escape(other)}"]`); if (c && ws_by_id(other)) c.replaceWith(card(ws_by_id(other))); }
  }
  const ids = [...sel];
  const names = ws.repos.filter(r => sel.has(r.id) && r.role === "project").map(r => r.name);
  bar.className = "batchbar";
  bar.replaceChildren(
    h("b", ws.name), h("span", `已选 ${ids.length} 个仓库`),
    h("button.btn", { onclick: () => act("fetch", { ws: wsId, repos: ids }).then(r => r && toast(`已开始：${r.job.title}`)) }, "⇣ Fetch"),
    h("button.btn", { onclick: () => confirmed("pull", { ws: wsId, repos: ids }, "pull --rebase") }, "⤓ Pull --rebase"),
    ws.kind === "west" && names.length ? h("button.btn", { onclick: () => syncDialog(ws, names) }, `⇄ 同步这 ${names.length} 个`) : null,
    ws.kind === "west" && names.length ? h("button.btn", { onclick: () => updateDialog(ws, names) }, `west update 这 ${names.length} 个`) : null,
    ws.kind === "west" && names.length ? h("button.btn.danger", { onclick: () => confirmed("align", { ws: wsId, repos: ws.repos.filter(r => sel.has(r.id) && r.role === "project").map(r => r.id) }, `强制对齐 ${names.length} 个`) }, "强制对齐") : null,
    h("button.btn", { onclick: () => commandDialog(ws, ids) }, "命令…"),
    h("div.spacer"),
    h("button.ghost", { onclick: () => { sel.clear(); paintCard(wsId); } }, "清除选择"));
}

// --- west update ---
async function updateDialog(ws, projects) {
  const opts = { keep_descendants: true, rebase: false, narrow: false };
  const body = h("div");
  let risky = false;
  const fill = async () => {
    body.replaceChildren(h("span.spin"), " 预检中…");
    const r = await act("update_check", { ws: ws.id, projects, ...opts });
    if (!r) { body.replaceChildren("预检失败"); return; }
    risky = r.risks.length > 0;
    const rows = r.rows.map(x => h("tr",
      h("td", x.name), h("td.mono", x.revision),
      h("td", x.cloned ? (x.branch || h("span.muted", "detached")) : h("span.muted", "未 clone")),
      h("td", x.on_manifest == null ? "–" : x.on_manifest ? h("span.mf-ok", "✓") : h("span.mf-off", "⚑")),
      x.notes.length ? h("td.note", x.notes.join("；")) : h("td.muted.small", x.info || "")));
    body.replaceChildren(
      risky ? h("div", h("b", "需要注意："), r.risks.map(x => h("div.risk", x)))
        : h("div.pill.ok", "✓ 没有发现本地改动或本地 commit"),
      h("div.row", { style: "margin:10px 0" },
        [["keep_descendants", "--keep-descendants"], ["rebase", "--rebase"], ["narrow", "--narrow"]].map(([k, t]) =>
          h("label.row", h("input", { type: "checkbox", checked: opts[k], onchange: e => { opts[k] = e.target.checked; fill(); } }), h("code", t)))),
      h("div", { style: "max-height:46vh;overflow:auto" },
        h("table.grid", h("thead", h("tr", ["project", "revision", "当前", "manifest", "说明"].map(t => h("th", t)))), h("tbody", rows))));
  };
  modal(`west update · ${ws.name}` + (projects ? ` · ${projects.length} 个 project` : ""), body, close => [
    h("span.muted.small.grow", projects ? projects.join(", ") : "全部 active project"),
    h("button.ghost", { onclick: close }, "取消"),
    h("button.primary", { onclick: async () => {
      const r = await act("update", { ws: ws.id, projects, ...opts, confirm: risky });
      if (r && r.need_confirm) { toast("状态有变化，请重新确认", true); fill(); return; }
      if (r && r.job) { close(); toast(`已开始：${r.job.title}`); show("jobs"); }
    } }, "执行 west update"),
  ], true);
  fill();
}

// --- sync to the manifest: check, run, then the stuck ones ---
async function syncDialog(ws, projects) {
  const autostash = new Set();
  const body = h("div");
  let plan = null;
  const fill = async () => {
    body.replaceChildren(h("span.spin"), " 预检中…");
    plan = await act("sync_check", { ws: ws.id, projects, autostash: [...autostash] });
    if (!plan) { body.replaceChildren("预检失败"); return; }
    const run = plan.rows.filter(x => !x.skip).length, skip = plan.rows.length - run;
    const rows = plan.rows.map(x => h("tr" + (x.skip ? ".skip" : ""),
      h("td", h("b", x.name)), h("td.mono", x.revision),
      h("td", x.cloned ? (x.branch || h("span.muted", `(${x.head || "detached"})`)) : h("span.muted", "未 clone")),
      h("td", x.stuck ? h("span.st-err", "⛔ " + x.action) : x.skip ? h("span.muted", x.action) : x.action,
        x.notes.length ? h("div.note", x.notes.join("；")) : null),
      h("td", x.dirty ? h("label.row.small", h("input", { type: "checkbox", checked: autostash.has(x.name),
        onchange: e => { e.target.checked ? autostash.add(x.name) : autostash.delete(x.name); fill(); } }), "autostash") : null)));
    const dirty = plan.rows.filter(x => x.dirty).map(x => x.name);
    body.replaceChildren(...[
      h("div.row", h("span.pill.ok", `${run} 个会同步`), skip ? h("span.pill", `${skip} 个跳过`) : null, h("div.spacer"),
        dirty.length ? h("button.ghost.small", { onclick: () => { const all = dirty.every(n => autostash.has(n));
          dirty.forEach(n => all ? autostash.delete(n) : autostash.add(n)); fill(); } }, "有改动的全部 autostash / 取消") : null),
      h("div.muted.small", { style: "margin:6px 0" }, "每个仓库只 fetch 一次；分支：切到 yml 写的分支（没有就从远端建）再 rebase；tag / commit：detach 过去。出错的仓库自动回滚到同步前。"),
      plan.risks.length ? h("div", h("b", "需要注意："), plan.risks.map(x => h("div.risk", x))) : null,
      h("div", { style: "max-height:48vh;overflow:auto;margin-top:8px" },
        h("table.grid", h("thead", h("tr", ["project", "revision", "当前", "会做什么", "改动"].map(t => h("th", t)))), h("tbody", rows)))].filter(Boolean));
  };
  modal(`同步到 manifest · ${ws.name}`, body, close => [
    h("span.muted.small.grow", shortManifest(ws)),
    h("button.ghost", { onclick: close }, "取消"),
    h("button.primary", { onclick: async () => {
      const r = await act("sync", { ws: ws.id, projects, autostash: [...autostash], confirm: plan && plan.risks.length > 0 });
      if (r && r.need_confirm) { toast("状态有变化，请重新确认", true); fill(); return; }
      if (r && r.job) { close(); toast(`已开始：${r.job.title}`); syncResult(ws, r.job.id); }
    } }, "开始同步"),
  ], true);
  fill();
}

// The sync's outcome: done / skipped / failed per repo, and the fixes for the rest.
async function syncResult(ws, jobId) {
  const body = h("div", h("span.spin"), " 同步中…（输出在 Jobs 页）");
  const close = modal(`同步结果 · ${ws.name}`, body, c => [
    h("button.ghost", { onclick: () => { c(); show("jobs"); } }, "看输出"),
    h("button.primary", { onclick: c }, "关闭")], true);
  const state = await waitJob(jobId);
  const jobs = await act("jobs", {}, "GET");
  const job = jobs && jobs.jobs.find(j => j.id === jobId);
  if (!job) return;
  refreshSoon(ws.id);
  const by = st => job.steps.filter(s => s.state === st);
  const bad = [...by("failed"), ...by("skipped")];
  const repoOf = name => ws.repos.find(r => r.name === name);
  body.replaceChildren(...[
    h("div.row", h("span.pill.ok", `✓ ${by("ok").length} 完成`), by("skipped").length ? h("span.pill.warn", `– ${by("skipped").length} 跳过`) : null,
      by("failed").length ? h("span.pill.bad", `✗ ${by("failed").length} 失败`) : null, state === "cancelled" ? h("span.pill", "已取消") : null),
    bad.length ? h("div", { style: "margin-top:10px" }, h("b", "需要处理："),
      h("table.grid", h("tbody", bad.map(s => {
        const r = repoOf(s.label);
        return h("tr", h("td", h("span.st-" + s.state, stateIcon(s.state)), " ", h("b", s.label)), h("td", s.note || `rc ${s.rc}`),
          h("td.row", r ? [
            h("button.btn.small", { onclick: () => fixStuck(ws, r, "sync").then(() => syncResult(ws, jobId)) }, "重试"),
            h("button.btn.small.danger", { title: FIX.align[1], onclick: () => fixStuck(ws, r, "align") }, "强制对齐"),
            h("button.btn.small", { onclick: () => act("open", { what: "gitbash", ws: ws.id, repo: r.id }) }, "Git Bash"),
            h("button.btn.small.claude", { onclick: () => act("open", { what: "claude", ws: ws.id, repo: r.id }) }, "✳"),
            h("button.ghost.small", { onclick: () => { close(); openRepo(ws, r); } }, "详情")] : null));
      }))),
      bad.length > 1 ? h("div.row", { style: "margin-top:8px" }, h("div.spacer"),
        h("button.danger", { onclick: async () => {
          const ids = bad.map(s => repoOf(s.label)).filter(Boolean).map(r => r.id);
          const r = await confirmed("align", { ws: ws.id, repos: ids }, `强制对齐这 ${ids.length} 个`);
          if (r && r.job) { await waitJob(r.job.id); refreshSoon(ws.id); close(); }
        } }, `这 ${bad.length} 个都不关心：全部强制对齐`)) : null)
      : h("div.pill.ok", { style: "margin-top:10px" }, "全部对齐到 manifest 了"),
    job.steps.filter(s => s.state === "ok" && s.note && s.note.includes("；")).length
      ? h("details.small", { style: "margin-top:10px" }, h("summary.muted", "完成但有说明的"),
        job.steps.filter(s => s.state === "ok" && s.note && s.note.includes("；")).map(s => h("div", h("b", s.label), "：", s.note))) : null].filter(Boolean));
}

// --- the yml in use (.west/config manifest.file) ---
async function pickManifest(anchor, ws) {
  const r = await act("manifest", { ws: ws.id }, "GET");
  if (!r) return;
  const cur = r.current.replace(/\\/g, "/");
  const dir = cur.includes("/") ? cur.slice(0, cur.lastIndexOf("/") + 1) : "";
  const top = cur.includes("/") ? cur.slice(0, cur.indexOf("/") + 1) : "";
  const files = r.files.map(f => f.replace(/\\/g, "/"));
  const input = h("input", { type: "text", placeholder: "搜索 yml…", style: "width:100%" });
  const list = h("div.ymllist");
  const paint = () => {
    const t = input.value.trim().toLowerCase();
    const hits = files.filter(f => !t || f.toLowerCase().includes(t));
    // the current one's folder first, then the rest; archive/ last
    const rank = f => f === cur ? 0 : /(^|\/)archive\//.test(f) ? 4
      : f.startsWith(dir) && !f.slice(dir.length).includes("/") ? 1 : top && f.startsWith(top) ? 2 : 3;
    hits.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    list.replaceChildren(...hits.slice(0, 200).map(f => h("button.yml" + (f === cur ? ".on" : ""),
      { onclick: () => choose(f) }, f === cur ? "● " : "", f)),
      hits.length > 200 ? h("div.muted.small", `还有 ${hits.length - 200} 个，输入关键字缩小`) : null);
  };
  input.addEventListener("input", paint);
  let closeIt;
  const choose = async f => {
    if (f === cur) return closeIt();
    const x = await act("set_manifest", { ws: ws.id, file: f });
    if (!x) return;
    closeIt();
    const i = S.workspaces.findIndex(w => w.id === ws.id);
    if (i >= 0) S.workspaces[i] = x.workspace;
    paintCard(ws.id);
    refreshSoon(ws.id);
    if (await ask("yml 已切换", h("div", h("div.mono", f), h("div", { style: "margin-top:8px" }, "现在同步到这个 manifest？")), "同步…")) syncDialog(x.workspace);
  };
  closeIt = modal(`切换 yml · ${ws.name}`, h("div", h("div.muted.small", { style: "margin-bottom:6px" },
    "改的是 .west/config 里的 manifest.file，等于 west config manifest.file <yml>"), input, list), c => [h("button.ghost", { onclick: c }, "取消")], true);
  paint();
  setTimeout(() => input.focus(), 30);
}

// --- stuck repos: above the repo table, with the fixes ---
function stuckItems(ws, r, s) {
  return [{ label: s.text }, ...s.fixes.map(f => ({ text: FIX[f][0] + (f === "align" ? "（stash + 备份）" : ""), run: () => fixStuck(ws, r, f) })),
    "-", { text: "🐚 Git Bash", run: () => act("open", { what: "gitbash", ws: ws.id, repo: r.id }) },
    { text: "✳ Claude Code", run: () => act("open", { what: "claude", ws: ws.id, repo: r.id }) },
    { text: "详情…", run: () => openRepo(ws, r) }];
}

function stuckBar(ws) {
  const st = (S.status[ws.id] || {}).repos || {};
  const list = ws.repos.filter(r => st[r.id] && st[r.id].stuck);
  if (!list.length) return null;
  const projects = list.filter(r => r.role === "project");
  return h("div.stuckbar", h("b", `⛔ ${list.length} 个仓库卡住`),
    list.map(r => h("button.btn.small", { title: st[r.id].stuck.text, onclick: e => menu(e.currentTarget, stuckItems(ws, r, st[r.id].stuck)) },
      r.name, " ▾")),
    h("div.spacer"),
    projects.length > 1 ? h("button.danger.small", { onclick: async () => {
      const r = await confirmed("align", { ws: ws.id, repos: projects.map(p => p.id) }, `强制对齐 ${projects.length} 个卡住的仓库`);
      if (r && r.job) { await waitJob(r.job.id); refreshSoon(ws.id); }
    } }, "全部强制对齐") : null);
}

// --- custom command ---
function commandDialog(ws, repoIds) {
  const west = ws.kind === "west" && !ws.error;
  let mode = west && !repoIds ? "forall" : "git";
  const input = h("input", { type: "text", style: "width:100%" });
  const hint = h("div.muted.small", { style: "margin-top:6px" });
  const tabs = h("div.subtabs");
  const recent = h("div.row", { style: "margin-top:10px" });
  const paint = () => {
    tabs.replaceChildren(
      h("button" + (mode === "git" ? ".on" : ""), { onclick: () => { mode = "git"; paint(); } }, "git <args>"),
      west ? h("button" + (mode === "forall" ? ".on" : ""), { onclick: () => { mode = "forall"; paint(); } }, "west forall -c") : null);
    input.placeholder = mode === "git" ? "例如：log --oneline -3" : "例如：git log --oneline -1";
    const n = repoIds ? repoIds.length : ws.repos.filter(r => r.cloned).length;
    hint.textContent = mode === "git" ? `在 ${n} 个仓库里逐个执行 git <args>（不经过 shell）`
      : "在 workspace 根目录执行 west forall -c \"<cmd>\"" + (repoIds ? "，限选中的 project" : "，所有 active project");
    const pre = mode + ": ";
    recent.replaceChildren(...(S.settings ? S.settings.recent_cmds : []).filter(c => c.startsWith(pre))
      .map(c => h("button.btn.small", { onclick: () => { input.value = c.slice(pre.length); input.focus(); } }, c.slice(pre.length))));
  };
  const run = async close => {
    const cmd = input.value.trim();
    if (!cmd) return;
    const projects = repoIds ? ws.repos.filter(r => repoIds.includes(r.id) && r.role === "project").map(r => r.name) : null;
    const r = mode === "git" ? await act("git", { ws: ws.id, cmd, repos: repoIds })
      : await act("forall", { ws: ws.id, cmd, projects });
    if (r && r.job) {
      close();
      toast(`已开始：${r.job.title}`);
      const s = await act("settings", {}, "GET");
      if (s) S.settings = s.settings;
      show("jobs");
    }
  };
  let closeFn;
  input.addEventListener("keydown", e => { if (e.key === "Enter") run(closeFn); });
  closeFn = modal(`命令 · ${ws.name}`, h("div", tabs, input, hint, recent), close => [
    h("button.ghost", { onclick: close }, "取消"),
    h("button.primary", { onclick: () => run(close) }, "执行"),
  ]);
  paint();
  setTimeout(() => input.focus(), 30);
}

// --- manifest panel ---
async function manifestPanel(ws) {
  const r = await act("manifest", { ws: ws.id }, "GET");
  if (!r) return;
  let tab = "projects";
  const body = h("div");
  const paint = () => {
    const st = (S.status[ws.id] || {}).repos || {};
    const tabs = h("div.subtabs",
      h("button" + (tab === "projects" ? ".on" : ""), { onclick: () => { tab = "projects"; paint(); } }, `Projects (${r.projects.length})`),
      h("button" + (tab === "raw" ? ".on" : ""), { onclick: () => { tab = "raw"; paint(); } }, "Raw"));
    const info = h("div.kv", { style: "margin-bottom:10px" },
      h("div.k", "manifest 文件"), h("div.mono", r.file),
      h("div.k", "group-filter"), h("div.mono", r.group_filter.join(", ") || "（无）"));
    if (tab === "projects") {
      const rows = r.projects.map(p => {
        const s = st[p.id], d = p.declared || {};
        let local;
        if (!p.cloned) local = h("span.mf-none", d.active === false ? "○ 被 group-filter 排除" : "○ 未 clone");
        else if (!s) local = h("span.muted", "–");
        else if (s.error) local = h("span.st-err", "⚠ " + s.error.split("\n")[0]);
        else local = h("span", s.on_manifest === false ? h("span.mf-off", "⚑ 偏离 ") : s.manifest_ahead ? h("span.mf-ok", "↗ ")
          : s.on_manifest ? h("span.mf-ok", "✓ ") : null,
          s.branch || `(${s.head})`, s.dirty ? h("span.pill.warn", { style: "margin-left:6px" }, "dirty") : null);
        return h("tr", { style: p.cloned ? "cursor:pointer" : "", onclick: () => { if (p.cloned) { closeIt(); openRepo(ws, p); } } },
          h("td", h("b", p.name)), h("td.mono", p.path), h("td.mono", d.revision || (p.role === "manifest" ? "HEAD" : "")),
          h("td.mono.small", { style: "max-width:360px;overflow:hidden;text-overflow:ellipsis" }, d.url || ""),
          h("td.small", (d.groups || []).join(", ")), h("td", local));
      });
      body.replaceChildren(tabs, info, h("table.grid",
        h("thead", h("tr", ["project", "path", "revision", "url", "groups", "本地状态"].map(t => h("th", t)))), h("tbody", rows)));
    } else {
      const pre = h("pre.raw", "…");
      const loadRaw = async f => {
        const x = await act("manifest_raw", { ws: ws.id, file: f }, "GET");
        pre.innerHTML = x ? yamlHtml(x.text) : "读取失败";
      };
      const pick = h("select", { onchange: e => loadRaw(e.target.value) },
        r.files.map(f => h("option", { value: f, selected: f === r.current }, f)));
      const repoOfManifest = r.projects.find(p => p.role === "manifest");
      body.replaceChildren(tabs, info, h("div.row", { style: "margin-bottom:8px" }, pick,
        repoOfManifest ? h("button.btn", { onclick: () => act("open", { what: "vscode", ws: ws.id, repo: repoOfManifest.id }).then(x => x && toast("已在 VS Code 打开")) }, "在 VS Code 中打开 manifest 仓库") : null), pre);
      loadRaw(pick.value);
    }
  };
  const closeIt = modal(`Manifest · ${ws.name}`, body, close => [h("button.ghost", { onclick: close }, "关闭")], true);
  paint();
}

function yamlHtml(text) {
  return text.split("\n").map(line => {
    if (/^\s*#/.test(line)) return `<span class="c">${esc(line)}</span>`;
    const i = line.search(/\s#/);
    const code = i >= 0 ? line.slice(0, i) : line, comment = i >= 0 ? line.slice(i) : "";
    return esc(code).replace(/^(\s*-?\s*)([\w.-]+)(:)/, '$1<span class="k">$2</span>$3') +
      (comment ? `<span class="c">${esc(comment)}</span>` : "");
  }).join("\n");
}

// --- auto refresh: local state only, never a fetch ---
// Reading every repo (~190 git status) loads the machine for seconds: the timed refresh
// reads only the open cards and the ones with something to look at; all of them at most
// every FULL_MS. Only while the page is in front.
const FULL_MS = 5 * 60 * 1000;
let lastFull = Date.now();
function autoIds(full) {
  return S.workspaces.filter(w => !(S.status[w.id] || {}).busy &&
    (full || S.open.has(w.id) || attention(w.id) > 0 || (S.selected[w.id] || new Set()).size)).map(w => w.id);
}
function autoRead() {
  if (reading || !S.workspaces.length || S.view !== "workspaces") return;
  const full = Date.now() - lastFull >= FULL_MS;
  const ids = autoIds(full);
  if (full) lastFull = Date.now();
  if (ids.length) readStatus(ids);
}
setInterval(() => {
  const every = (S.settings ? S.settings.refresh_s : 60) * 1000;
  if (!every || document.hidden || !document.hasFocus()) return;
  if (Date.now() - lastRead >= every) autoRead();
}, 5000);
window.addEventListener("focus", () => {
  if (Date.now() - lastRead > 30000) autoRead();
});

// a job starting / ending: mark its workspace, re-read it at the end
onJobEvent(ev => {
  if (ev.type !== "job") return;
  if (ev.state === "queued" && ev.ws_id) {
    JOB_WS[ev.job] = ev.ws_id;
    if (S.status[ev.ws_id]) { S.status[ev.ws_id].busy = true; paintCard(ev.ws_id); }
  }
  if (["ok", "failed", "cancelled"].includes(ev.state) && JOB_WS[ev.job]) {
    const wsId = JOB_WS[ev.job];
    if (S.status[wsId]) S.status[wsId].busy = false;
    refreshSoon(wsId);
  }
});
const JOB_WS = {};
const pendingRefresh = new Set();
function refreshSoon(wsId) {
  pendingRefresh.add(wsId);
  const tick = () => {
    if (reading) return setTimeout(tick, 500);
    const ids = [...pendingRefresh];
    pendingRefresh.clear();
    if (ids.length) readStatus(ids);
  };
  setTimeout(tick, 300);
}

BOOT.push(() => loadWorkspaces());
