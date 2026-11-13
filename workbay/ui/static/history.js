// Claude history view: Claude Code's sessions from every directory, a preview of one,
// resume it where it ran, delete the checked ones to a trash that can be put back.
"use strict";

const CH = { list: null, sel: null, checked: new Set(), text: "", ws: "", small: false, old: 0, trash: false };

VIEWS.history = {
  title: "Claude History",
  render(main) {
    main.append(h("div.toolbar#chbar"), h("div.chist", h("div.chlist#chlist"), h("div.chview#chview")));
    if (CH.list) paintHistory(); else loadHistory();
  },
};

async function loadHistory() {
  const box = $("#chlist");
  if (box) box.replaceChildren(h("div.empty", h("span.spin"), " 读取会话…"));
  const r = await act("claude_sessions", {}, "GET");
  if (!r) return;
  CH.list = r.sessions;
  CH.home = r.home;
  CH.busy = r.busy_s;
  const ids = new Set(CH.list.map(s => s.id));
  CH.checked.forEach(id => ids.has(id) || CH.checked.delete(id));
  paintHistory();
}

function chShown() {
  const t = CH.text.trim().toLowerCase(), now = Date.now() / 1000;
  return CH.list.filter(s => (!CH.ws || (CH.ws === "-" ? !s.ws : s.ws === CH.ws)) &&
    (!CH.small || !s.first || s.size < 50 * 1024) &&
    (!CH.old || now - s.ended > CH.old * 86400) &&
    (!t || [s.title, s.first, s.last_prompt, s.cwd, s.id].some(x => x && x.toLowerCase().includes(t))));
}

function paintHistory() {
  const bar = $("#chbar");
  if (!bar || !CH.list) return;
  const wss = [...new Set(CH.list.map(s => s.ws).filter(Boolean))].sort();
  const shown = chShown();
  const allOn = shown.length && shown.every(s => CH.checked.has(s.id));
  bar.replaceChildren(
    h("input", { type: "text", placeholder: "搜索标题 / 内容 / 目录", value: CH.text,
      oninput: e => { CH.text = e.target.value; paintChList(); } }),
    h("select", { onchange: e => { CH.ws = e.target.value; paintHistory(); } },
      h("option", { value: "" }, `全部目录（${CH.list.length}）`),
      wss.map(w => h("option", { value: w, selected: CH.ws === w }, w)),
      h("option", { value: "-", selected: CH.ws === "-" }, "不在 workspace 里的")),
    h("label.row", h("input", { type: "checkbox", checked: CH.small, onchange: e => { CH.small = e.target.checked; paintHistory(); } }),
      "只看很短的（<50 KB 或没说话）"),
    h("select", { onchange: e => { CH.old = Number(e.target.value); paintHistory(); } },
      [[0, "任何时间"], [7, "7 天前的"], [30, "30 天前的"], [90, "90 天前的"]].map(([v, t]) => h("option", { value: v, selected: CH.old === v }, t))),
    h("div.spacer"),
    h("button.ghost", { onclick: () => trashDialog() }, "🗑 回收站"),
    h("button.ghost", { onclick: loadHistory, title: "重新读 ~/.claude/projects" }, "⟳ 刷新"));
  const batch = h("div.row.chbatch",
    h("label.row", h("input", { type: "checkbox", checked: allOn,
      onchange: e => { shown.forEach(s => e.target.checked ? CH.checked.add(s.id) : CH.checked.delete(s.id)); paintHistory(); } }),
      `${shown.length} 个会话`),
    h("div.spacer"),
    CH.checked.size ? [h("span.small", `已选 ${CH.checked.size}`),
      h("button.ghost.small", { onclick: () => { CH.checked.clear(); paintHistory(); } }, "清除"),
      h("button.danger.small", { onclick: deleteChecked }, `删除 ${CH.checked.size} 个`)] : null);
  const list = $("#chlist");
  list.replaceChildren(batch, h("div#chrows"));
  paintChList();
  paintPreview();
}

function paintChList() {
  const rows = $("#chrows");
  if (!rows) return;
  const shown = chShown();
  let day = "";
  const out = [];
  for (const s of shown) {
    const d = new Date(s.ended * 1000).toLocaleDateString();
    if (d !== day) { day = d; out.push(h("div.chday", d)); }
    out.push(sessionRow(s));
  }
  rows.replaceChildren(...(out.length ? out : [h("div.empty", "没有符合条件的会话")]));
}

// "claude-opus-5-5[1m]" -> "opus 5.5 · 1M"
function shortModel(m) {
  if (!m) return "";
  const big = /\[1m\]$/i.test(m);
  const base = m.replace(/\[1m\]$/i, "").replace(/^claude-/, "").replace(/-(\d+)-(\d+)(-\d{8})?$/, " $1.$2");
  return base + (big ? " · 1M" : "");
}

// expert[1m] runs on the local server (free to use); the rest are cloud models.
function modelPill(s, full = false) {
  if (!s.model) return null;
  return h("span.mpill" + (s.local ? ".local" : ".cloud"), { title: (s.local ? "地端模型（免费）：" : "云端模型：") + s.model },
    s.local ? "🏠 " : "☁ ", full ? s.model : shortModel(s.model));
}

// bar: a short meter (the list), else a pill (the preview)
function ctxPill(c, bar = false) {
  if (!c) return null;
  const lvl = c.pct >= 80 ? "bad" : c.pct >= 50 ? "warn" : "ok";
  const title = `上下文 ${Math.round(c.used / 1000)}K / ${c.window / 1000}K tokens（最后一次回复时）`;
  if (bar) return h("span.ctxbar." + lvl, { title },
    h("span.track", h("span.fill", { style: `width:${Math.min(100, c.pct)}%` })), h("span.n", `${Math.round(c.pct)}%`));
  return h("span.pill." + lvl, { title }, `${Math.round(c.pct)}%`);
}

function livePill(s) {
  if (s.live) return h("span.pill.info", { title: `正在运行：pid ${s.live.pid}${s.live.name ? " · " + s.live.name : ""}` },
    s.live.status === "busy" ? "● 运行中" : "● 打开着");
  if (Date.now() / 1000 - s.mtime < (CH.busy || 120)) return h("span.pill", { title: "刚有改动" }, "刚用过");
  return null;
}

function sessionRow(s) {
  const check = h("input", { type: "checkbox", checked: CH.checked.has(s.id), onclick: e => e.stopPropagation(),
    onchange: e => { e.target.checked ? CH.checked.add(s.id) : CH.checked.delete(s.id); paintHistory(); } });
  return h("div.chrow" + (CH.sel === s.id ? ".on" : "") + (CH.checked.has(s.id) ? ".sel" : ""),
    { onclick: () => { CH.sel = s.id; paintChList(); paintPreview(); } },
    check,
    h("div.chmain",
      h("div.t", s.title || s.first || h("span.muted", "（没有对话）")),
      h("div.small.muted.chmeta",
        h("span", new Date(s.ended * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })),
        s.ws ? h("span.pill.honey", s.ws) : null,
        h("span.mono.cwd", { title: s.cwd }, s.cwd || "?"),
        h("div.spacer"),
        livePill(s)),
      h("div.small.muted.chpills", h("span", fmtSize(s.size)), h("div.spacer"), modelPill(s), ctxPill(s.context, true))));
}

async function paintPreview() {
  const box = $("#chview");
  if (!box) return;
  const s = CH.list && CH.list.find(x => x.id === CH.sel);
  if (!s) { box.replaceChildren(h("div.empty", "点左边的会话预览；勾选后可以删除")); return; }
  const head = h("div.chhead",
    h("div.row", !s.title && s.first ? h("span.pill.honey", "第一句") : null,
      h("b", s.title || s.first || "（没有对话）"), h("div.spacer"),
      livePill(s),
      h("button.btn.claude", { disabled: !s.cwd_exists, title: s.cwd_exists ? `在 ${s.cwd} 执行：${s.resume_cmd}` : "目录不在了",
        onclick: async () => {
          if (s.live && !await ask("这个会话正在运行", h("div", `pid ${s.live.pid} 还开着这个会话。再开一个 resume 会出现两个窗口写同一份记录，`,
            "一般先切到原来的窗口。"), "仍然打开")) return;
          const r = await act("claude_resume", { id: s.id });
          if (r) toast("已打开：" + r.cmd);
        } }, "✳ 继续这个会话"),
      h("button.icon", { title: "复制 resume 命令", onclick: () => navigator.clipboard.writeText(`cd /d "${s.cwd}" && ${s.resume_cmd}`).then(() => toast("已复制")) }, "📋")),
    h("div.small.muted.row",
      h("span", new Date((s.started || s.ended) * 1000).toLocaleString(), " → ", new Date(s.ended * 1000).toLocaleString()),
      s.branch ? h("span", "⎇ " + s.branch) : null, h("span", fmtSize(s.size)), h("span.mono", s.id)),
    s.model ? h("div.small.row", h("span.muted", "最后的模型："), modelPill(s, true),
      s.context ? [h("span.muted", "上下文："), `${Math.round(s.context.used / 1000)}K / ${s.context.window / 1000}K`, ctxPill(s.context)] : null) : null,
    h("div.small.mono.muted", s.cwd || ""),
    s.last_prompt ? h("div.chlast", { title: "点击展开 / 收起", onclick: e => e.currentTarget.classList.toggle("open") },
      h("div.lbl", "⏎ 最后一句"), h("div.txt", s.last_prompt)) : null);
  const msgs = h("div.chmsgs", h("span.spin"), " 读取…");
  box.replaceChildren(head, msgs);
  const want = s.id;
  const r = await act("claude_preview", { id: s.id }, "GET");
  if (CH.sel !== want || !r) return;
  const users = r.messages.filter(m => m.role === "user");
  const tag = m => m === users[0] ? " · 第一句" : m === users[users.length - 1] && !r.more ? " · 最后一句" : "";
  msgs.replaceChildren(...r.messages.map(m =>
    m.role === "tools" ? h("div.chmsg.tools", "🔧 ", m.text)
      : m.role === "command" ? h("div.chmsg.tools", "⌘ ", m.text)
      : h("div.chmsg." + m.role, h("div.who", m.role === "user" ? "你" + tag(m) : "Claude"), h("div.txt", m.text))),
    r.more ? h("div.muted.small", "…后面还有，继续会话可看全部") : null,
    !r.messages.length ? h("div.empty", "没有对话内容") : null);
}

async function deleteChecked() {
  const ids = [...CH.checked];
  const titles = ids.map(id => CH.list.find(s => s.id === id)).filter(Boolean).slice(0, 8)
    .map(s => h("div.small", "· ", s.title || s.first || s.id));
  if (!await ask(`删除 ${ids.length} 个会话`, h("div", titles, ids.length > 8 ? h("div.small.muted", `…共 ${ids.length} 个`) : null,
    h("div.muted.small", { style: "margin-top:8px" }, "移到 ~/.claude/workbay-trash，可在「回收站」恢复；正在运行的、2 分钟内有改动的不会删。")), "移到回收站", true)) return;
  const r = await act("claude_delete", { ids, confirm: true });
  if (!r) return;
  toast(`已移到回收站 ${r.moved.length} 个`);
  if (r.refused.length) toast("没删：" + r.refused.map(([, why]) => why).join("；"), true);
  r.moved.forEach(id => CH.checked.delete(id));
  if (r.moved.includes(CH.sel)) CH.sel = null;
  loadHistory();
}

async function trashDialog() {
  const body = h("div", h("span.spin"));
  const fill = async () => {
    const r = await act("claude_trash", {}, "GET");
    if (!r) return;
    body.replaceChildren(
      h("div.muted.small", { style: "margin-bottom:8px" }, r.path),
      ...(r.batches.length ? r.batches.map(b => h("div.buildres",
        h("div.row", h("b", b.name.replace(/(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)/, "$1-$2-$3 $4:$5:$6")),
          h("span.muted.small", `${b.sessions.length} 个会话`), h("div.spacer"),
          h("button.btn.small", { onclick: async () => {
            const x = await act("claude_restore", { batch: b.name });
            if (x) { toast(`已恢复 ${x.restored.length} 个` + (x.kept.length ? `，${x.kept.length} 项原位置已有同名，留在回收站` : "")); fill(); loadHistory(); }
          } }, "恢复"),
          h("button.danger.small", { onclick: async () => {
            const x = await confirmed("claude_empty_trash", { batch: b.name }, "永久删除");
            if (x && x.removed) fill();
          } }, "永久删除")),
        b.sessions.slice(0, 6).map(s => h("div.small.muted", "· ", s.title || s.first || s.id)),
        b.sessions.length > 6 ? h("div.small.muted", `…`) : null))
        : [h("div.empty", "回收站是空的")]));
  };
  modal("Claude History · 回收站", body, c => [
    h("button.ghost", { onclick: () => act("claude_open_trash") }, "打开目录"),
    h("button.danger", { onclick: async () => { const x = await confirmed("claude_empty_trash", {}, "清空回收站"); if (x && x.removed) fill(); } }, "清空回收站"),
    h("button.primary", { onclick: c }, "关闭")], true);
  fill();
}
