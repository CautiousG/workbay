// The build panel of a workspace: its IC's defconfig targets (gcc, build.py), build /
// build + flash (J-Link, RTL87x2L), the last result of each, the diagnostics.
"use strict";

const fmtSize = n => n == null ? "" : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`;
const fmtTook = r => r && r.started && r.ended ? `${Math.round(r.ended - r.started)} s` : "";

async function buildPanel(ws) {
  const sel = new Set();
  let data = null, text = localStorage.getItem("apiary.bfilter") || "";
  const body = h("div");
  const load = async (rescan = false) => {
    body.replaceChildren(h("span.spin"), rescan ? " 重新扫描 defconfig…" : " 读取 target…");
    data = await act("build_targets", { ws: ws.id, rescan: rescan || null }, "GET");
    if (data) paint(); else body.replaceChildren("读取失败");
  };
  const paint = () => {
    const icPick = h("select", { onchange: async e => {
      const r = await act("build_ic", { ws: ws.id, ic: e.target.value });
      if (r) { data = r; sel.clear(); paint(); } } },
      h("option", { value: "", selected: !data.ic }, "（不编译）"),
      data.ics.map(ic => h("option", { value: ic, selected: data.ic === ic }, ic + (ic === data.default_ic ? "（按目录名）" : ""))));
    const filter = h("input", { type: "text", placeholder: "搜索 app / variant / 路径", value: text, style: "width:260px",
      oninput: e => { text = e.target.value; localStorage.setItem("apiary.bfilter", text); fill(); } });
    const tbody = h("tbody");
    const t = data.tools;
    const missing = [!t.cmake && "cmake", !t.gcc && "arm-none-eabi-gcc", data.targets.some(x => x.kind === "humac") && !t.ninja && "ninja"].filter(Boolean);
    const fill = () => {
      const q = text.trim().toLowerCase();
      const rows = data.targets.filter(x => !q || (x.app + " " + x.variant + " " + x.id).toLowerCase().includes(q))
        .sort((a, b) => b.fav - a.fav);
      tbody.replaceChildren(...rows.map(x => targetRow(ws, x, sel, paintFoot, () => fill())));
      if (!rows.length) tbody.append(h("tr", h("td.muted", { colspan: 7 }, data.ic ? "没有匹配的 defconfig" : "选一个 IC")));
    };
    body.replaceChildren(
      h("div.row", { style: "margin-bottom:10px" }, h("span.muted.small", "IC"), icPick, filter, h("div.spacer"),
        h("span.muted.small", `${data.targets.length} 个 defconfig`),
        h("button.ghost.small", { onclick: () => load(true) }, "⌕ 重新扫描")),
      missing.length ? h("div.risk", `找不到 ${missing.join("、")}：编译会失败。gcc 的目录可在 Settings 里填。`) : null,
      h("div", { style: "max-height:56vh;overflow:auto" },
        h("table.grid.builds", h("thead", h("tr", ["", "☆", "target", "路径", "上次", "结果", "产物"].map(x => h("th", x)))), tbody)));
    fill();
    paintFoot();
  };
  let foot;
  const paintFoot = () => {
    if (!foot || !data) return;
    const picked = data.targets.filter(x => sel.has(x.id));
    const one = picked.length === 1 ? picked[0] : null;
    foot.replaceChildren(
      h("span.muted.small.grow", picked.length ? `已选 ${picked.length} 个` : "勾选 target；Keil 工程请用 IDE"),
      h("button.ghost", { disabled: !picked.length, onclick: () => cleanBuild(ws, picked).then(load) }, "清掉 build"),
      h("button.ghost", { disabled: !picked.length, onclick: () => runBuild(ws, picked, "build", true) }, "重新 configure + 编译"),
      one && one.flash ? h("button.btn", { title: "cmake --build --target " + one.app + "_flash（J-Link，同一时间只烧一个）",
        onclick: () => runBuild(ws, picked, "flash") }, "⚡ 编译 + 烧录") : null,
      h("button.primary", { disabled: !picked.length, onclick: () => runBuild(ws, picked, "build") }, picked.length > 1 ? `编译 ${picked.length} 个` : "编译"));
  };
  modal(`编译 · ${ws.name}`, body, close => { foot = h("div.row.grow"); return [foot, h("button.ghost", { onclick: close }, "关闭")]; }, true);
  load();
}

function targetRow(ws, x, sel, onSel, refill) {
  const l = x.last;
  const mb = l && l.main_bin;
  const stale = mb && l.started && mb.mtime < l.started - 1;
  const res = !l ? h("span.muted", "–")
    : l.state === "running" ? h("span.st-running", "⏵ 进行中")
    : h("span", { class: l.state === "ok" ? "st-ok" : "st-failed", style: "cursor:pointer", title: "查看诊断",
        onclick: () => showBuildResult(ws, x) },
      (l.state === "ok" ? "✓" : "✗ " + l.stage) + (l.errors ? ` · ${l.errors} E` : "") + (l.warnings ? ` · ${l.warnings} W` : ""));
  const check = h("input", { type: "checkbox", checked: sel.has(x.id),
    onchange: e => { e.target.checked ? sel.add(x.id) : sel.delete(x.id); onSel(); } });
  const star = h("button.icon", { title: "收藏（排在前面）", onclick: async () => {
    const r = await act("build_fav", { ws: ws.id, target: x.id, on: !x.fav });
    if (r) { x.fav = r.fav; refill(); } } }, x.fav ? "★" : "☆");
  return h("tr", { ondblclick: () => { sel.add(x.id); onSel(); refill(); } },
    h("td", check), h("td", star),
    h("td", h("b", x.app), " ", h("span.muted", x.variant), x.lib ? h("span.pill", { style: "margin-left:6px" }, "lib") : null,
      x.flash ? h("span.pill.info", { style: "margin-left:6px", title: "可 J-Link 烧录" }, "⚡") : null),
    h("td.mono.small.muted", { title: x.path }, x.id.replace(/\/defconfig\.[^/]*$/, "")),
    h("td.small.muted", l ? ago(l.ended || l.started) + (fmtTook(l) ? ` · ${fmtTook(l)}` : "") : ""),
    h("td.small", res),
    h("td.small", mb ? h("a", { href: "#", title: mb.path + (stale ? "\n比上次编译早：可能是旧产物" : ""),
      onclick: e => { e.preventDefault(); act("reveal", { ws: ws.id, path: mb.path }); } },
      mb.name, " ", h("span.muted", fmtSize(mb.size)), stale ? h("span.pill.warn", { style: "margin-left:4px" }, "旧产物") : null) : null));
}

async function runBuild(ws, picked, action, reconfigure = false) {
  const r = await act("build", { ws: ws.id, targets: picked.map(x => x.id), action, reconfigure });
  if (!r || !r.job) return;
  toast(`已开始：${r.job.title}`);
  buildJobResult(ws, r.job.id, picked);
}

async function cleanBuild(ws, picked) {
  const r = await confirmed("build_clean", { ws: ws.id, targets: picked.map(x => x.id) }, "删除 build 目录");
  if (r && r.removed) toast(r.removed.length ? `已删除 ${r.removed.length} 个 build 目录` : "没有 build 目录");
}

// A build Job's outcome, one row per target, with its diagnostics.
async function buildJobResult(ws, jobId, picked) {
  const body = h("div", h("span.spin"), " 编译中…（输出在 Jobs 页）");
  modal(`编译结果 · ${ws.name}`, body, c => [
    h("button.ghost", { onclick: () => { c(); show("jobs"); } }, "看输出"),
    h("button.ghost", { onclick: () => { c(); buildPanel(ws); } }, "回到编译面板"),
    h("button.primary", { onclick: c }, "关闭")], true);
  const state = await waitJob(jobId);
  const results = await Promise.all(picked.map(x => act("build_result", { ws: ws.id, target: x.id }, "GET")));
  const jobs = await act("jobs", {}, "GET");
  const job = jobs && jobs.jobs.find(j => j.id === jobId);
  body.replaceChildren(
    state === "cancelled" ? h("div.pill", "已取消") : null,
    ...picked.map((x, i) => resultBlock(ws, x, results[i] && results[i].result, job && job.steps[i])));
}

async function showBuildResult(ws, x) {
  const r = await act("build_result", { ws: ws.id, target: x.id }, "GET");
  if (!r) return;
  modal(`上次结果 · ${x.app} · ${x.variant}`, resultBlock(ws, x, r.result), c => [h("button.primary", { onclick: c }, "关闭")], true);
}

function resultBlock(ws, x, res, step) {
  if (!res) return h("div.risk", `${x.app} · ${x.variant}：没有结果（${step ? step.note || step.state : "未编译"}）`);
  const ok = res.state === "ok";
  const diags = res.diags || [];
  const errs = diags.filter(d => d.sev === "error"), warns = diags.filter(d => d.sev === "warning");
  const line = d => h("div.diag." + d.sev,
    h("span.sev", d.sev === "error" ? "E" : "W"),
    d.file ? h("a.mono", { href: "#", title: "在 VS Code 打开", onclick: e => { e.preventDefault(); act("goto", { ws: ws.id, file: d.file, line: d.line }); } },
      shortPath(ws, d.file) + (d.line ? `:${d.line}` : "")) : null,
    h("span", d.msg));
  const bins = (res.products || []).filter(p => /\.bin$/i.test(p.name));
  return h("div.buildres",
    h("div.row", h("span" + (ok ? ".st-ok" : ".st-failed"), ok ? "✓" : "✗"), h("b", `${x.app} · ${x.variant}`),
      h("span.muted.small", res.action === "flash" ? "编译 + 烧录" : "编译"), ok ? null : h("span.pill.bad", `${res.stage} 失败`),
      h("span.pill" + (errs.length ? ".bad" : ""), `${errs.length} error`), h("span.pill" + (warns.length ? ".warn" : ""), `${warns.length} warning`),
      h("span.muted.small", fmtTook(res)), h("div.spacer"),
      h("button.ghost.small", { onclick: () => act("reveal", { ws: ws.id, path: x.build_dir }) }, "打开 build 目录")),
    diags.length ? h("div.diags", [...errs, ...warns].slice(0, 80).map(line),
      diags.length > 80 ? h("div.muted.small", `还有 ${diags.length - 80} 条，见 apiary_build.log`) : null) : null,
    bins.length ? h("div.small", { style: "margin-top:6px" }, h("span.muted", "产物："),
      bins.slice(0, 8).map(p => h("a.mono", { href: "#", style: "margin-right:12px", title: p.path,
        onclick: e => { e.preventDefault(); act("reveal", { ws: ws.id, path: p.path }); } },
        p.name, " ", h("span.muted", fmtSize(p.size)), p.mtime < res.started - 1 ? h("span.pill.warn", "旧") : null)),
      h("button.icon", { title: "复制主 bin 路径", onclick: () => res.main_bin && navigator.clipboard.writeText(res.main_bin.path).then(() => toast("已复制")) }, "📋")) : null);
}

function shortPath(ws, p) {
  return p.toLowerCase().startsWith(ws.path.toLowerCase()) ? p.slice(ws.path.length + 1) : p;
}
