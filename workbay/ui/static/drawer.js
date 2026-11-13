// The repo drawer: one repository's changes, diff, commit and recent log.
"use strict";

const D = { ws: null, repo: null, data: null, file: null, msg: {} };   // msg: repo id -> draft

function closeDrawer() {
  const d = $("#drawer");
  if (D.repo) D.msg[D.repo.id] = ($("#dr-msg") || {}).value ?? D.msg[D.repo.id];
  d.hidden = true;
  d.replaceChildren();
  D.repo = null;
}
document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#modal").hidden && !$("#drawer").hidden) closeDrawer(); });

async function openRepo(ws, repo) {
  if (!repo.cloned) return;
  if (D.repo && D.repo.id !== repo.id) closeDrawer();
  D.ws = ws; D.repo = repo; D.file = null;
  const d = $("#drawer");
  d.hidden = false;
  d.replaceChildren(h("div.dr-body", h("span.spin"), " 读取中…"));
  await reloadRepo();
}

async function reloadRepo() {
  const { ws, repo } = D;
  const r = await act("repo", { ws: ws.id, repo: repo.id }, "GET");
  if (!r || D.repo !== repo) return;
  D.data = r;
  paintDrawer();
}

function stripTemplate(t) {
  return t.split("\n").filter(l => !l.startsWith("#")).join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
}

function paintDrawer() {
  const { ws, repo, data } = D;
  const st = data.status;
  const o = what => () => act("open", { what, ws: ws.id, repo: repo.id }).then(r => r && toast("已打开"));
  const remote = Object.entries(data.remotes)[0];
  const head = h("div.dr-head",
    h("div.row", h("span.dr-title", repo.name), h("span.muted.small", repo.path !== "." ? repo.path : ""),
      h("div.spacer"),
      h("button.btn.claude", { onclick: o("claude"), title: "在这个仓库目录打开 cmd 并运行 Claude Code" }, "✳ Claude"),
      h("button.btn", { onclick: o("gitbash") }, "🐚 Git Bash"),
      h("button.btn", { onclick: o("vscode") }, "VS Code"),
      h("button.btn", { onclick: o("explorer"), title: "资源管理器" }, "📁"),
      h("button.btn", { onclick: e => menu(e.currentTarget, openItems(ws, repo)) }, "⋯"),
      h("button.icon", { onclick: reloadRepo, title: "重新读取" }, "⟳"),
      h("button.icon", { onclick: closeDrawer }, "✕")),
    h("div.row.small", { style: "margin-top:6px" },
      st.branch ? h("span.pill.honey", "⎇ " + st.branch) : h("span.pill.warn", `detached ${st.head}`),
      st.upstream ? h("span.pill", `→ ${st.upstream}  ↑${st.ahead} ↓${st.behind}`) : h("span.pill", "无 upstream"),
      remote ? h("span.muted.mono", { title: remote[0] }, remote[1]) : null,
      h("div.spacer"),
      h("button.btn", { onclick: () => act("fetch", { ws: ws.id, repos: [repo.id] }).then(r => r && toast(`已开始：${r.job.title}`)) }, "⇣ Fetch"),
      h("button.btn", { onclick: () => confirmed("pull", { ws: ws.id, repos: [repo.id] }, "pull --rebase") }, "⤓ Pull --rebase")));
  const body = h("div.dr-body", stuckSection(), changesSection(), diffSection(), commitSection(), logSection(), backupSection());
  $("#drawer").replaceChildren(head, body);
  if (D.file) showDiff(D.file);
}

function xyClass(c) {
  return { M: "M", A: "A", D: "D", R: "R", C: "R", U: "U", "?": "Q" }[c] || "";
}

function changesSection() {
  const ch = D.data.changes;
  const staged = ch.filter(c => c.staged), rest = ch.filter(c => c.unstaged || c.untracked || c.conflicted);
  const line = (c, isStaged) => {
    const code = isStaged ? c.x : (c.untracked ? "?" : c.conflicted ? "U" : c.y);
    const key = (isStaged ? "S:" : "W:") + c.path;
    return h("div.ch" + (D.file && D.file.key === key ? ".on" : ""), { onclick: () => showDiff({ key, path: c.path, staged: isStaged, untracked: c.untracked }) },
      h("input", { type: "checkbox", checked: isStaged, disabled: c.conflicted && !isStaged, title: isStaged ? "unstage" : "stage",
        onclick: e => { e.stopPropagation(); stageFiles([c.path], !isStaged); } }),
      h("span.xy." + xyClass(code), code === "?" ? "??" : code),
      h("span", c.orig ? `${c.orig} → ${c.path}` : c.path),
      c.add != null || c.del != null ? h("span.nums", h("span.a", `+${c.add ?? 0}`), " ", h("span.d", `−${c.del ?? 0}`)) : null);
  };
  if (!ch.length) return h("div.dr-sec", h("h4", "改动"), h("div.muted", "工作区干净"));
  return h("div.dr-sec",
    h("h4.row", `Staged (${staged.length})`, h("div.spacer"),
      staged.length ? h("button.btn.small", { onclick: () => stageFiles(staged.map(c => c.path), false) }, "全部 unstage") : null),
    h("div.changes", staged.length ? staged.map(c => line(c, true)) : h("div.ch.muted", "（无）")),
    h("h4.row", { style: "margin-top:10px" }, `未 staged (${rest.length})`, h("div.spacer"),
      rest.length ? h("button.btn.small", { onclick: () => stageFiles(rest.filter(c => !c.conflicted).map(c => c.path), true) }, "全部 stage") : null),
    h("div.changes", rest.length ? rest.map(c => line(c, false)) : h("div.ch.muted", "（无）")));
}

async function stageFiles(files, on) {
  if (!files.length) return;
  const r = await act(on ? "stage" : "unstage", { ws: D.ws.id, repo: D.repo.id, files });
  if (r) { D.msg[D.repo.id] = $("#dr-msg").value; await reloadRepo(); }
}

function diffSection() {
  return h("div.dr-sec#dr-diff", D.file ? null : h("div.muted.small", "点击文件查看 diff"));
}

async function showDiff(f) {
  D.file = f;
  $$(".changes .ch").forEach(el => el.classList.remove("on"));
  const box = $("#dr-diff");
  box.replaceChildren(h("h4", f.path + (f.staged ? "（staged）" : "")), h("span.spin"));
  const r = await act("diff", { ws: D.ws.id, repo: D.repo.id, path: f.path, staged: f.staged, untracked: f.untracked }, "GET");
  if (!r || D.file !== f) return;
  const html = r.diff ? r.diff.split("\n").map(l => {
    const cls = l.startsWith("@@") ? "hunk" : /^(diff |index |--- |\+\+\+ |new file|deleted file|similarity|rename )/.test(l) ? "meta"
      : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return cls ? `<span class="${cls}">${esc(l)}</span>` : esc(l);
  }).join("\n") : '<span class="meta">（没有文本差异：可能是二进制文件或只改了权限）</span>';
  box.replaceChildren(h("h4.row", f.path + (f.staged ? "（staged）" : ""), h("div.spacer"),
    h("button.icon", { onclick: () => { D.file = null; box.replaceChildren(h("div.muted.small", "点击文件查看 diff")); } }, "✕")),
    h("pre.diff", { html }));
}

// --- stuck: a half-done rebase / merge, conflicts, the wrong branch ---
const FIX = {
  abort: ["放弃", "回到操作开始前"], continue: ["继续", "冲突改好并 stage 后继续"], skip: ["跳过这个 commit", "rebase --skip"],
  sync: ["同步到 manifest", "切到 manifest 的分支并 rebase"], align: ["强制对齐", "不关心本地：stash 改动、备份 HEAD，对齐到远端"],
};

function stuckSection() {
  const s = D.data.stuck;
  if (!s) return null;
  return h("div.dr-sec.stuck", h("div.row", h("span.pill.bad", "⛔ 卡住"), h("b", s.text), h("div.spacer")),
    h("div.row", { style: "margin-top:8px" }, s.fixes.map(f => h("button.btn" + (f === "align" ? ".danger" : ""),
      { title: FIX[f][1], onclick: () => fixStuck(D.ws, D.repo, f) }, FIX[f][0]))));
}

// one fix on one repo; abort / continue / skip at once, sync / align as a Job
async function fixStuck(ws, repo, action) {
  let r;
  if (action === "align") r = await confirmed("align", { ws: ws.id, repos: [repo.id] }, `强制对齐 ${repo.name}`);
  else if (action === "sync") r = await confirmed("sync", { ws: ws.id, projects: [repo.name], autostash: [repo.name] }, `同步 ${repo.name}`);
  else {
    r = await act("unstick", { ws: ws.id, repo: repo.id, action });
    if (r) toast(`${repo.name}：${FIX[action][0]}完成`);
  }
  if (!r) return;
  if (r.job) await waitJob(r.job.id);
  if (D.repo && D.repo.id === repo.id) await reloadRepo();
  refreshSoon(ws.id);
}

function backupSection() {
  const b = D.data.backups || [];
  if (!b.length && !D.data.stashes) return null;
  return h("div.dr-sec", h("h4", "备份"),
    D.data.stashes ? h("div.muted.small", `stash 里有 ${D.data.stashes} 份改动（git stash list / git stash pop）`) : null,
    b.map(x => h("div.row.small", { style: "margin-top:4px" },
      h("span.sha", x.sha), h("span.grow", { title: x.ref }, x.subject), h("span.muted", ago(x.time)),
      h("button.btn.small", { title: "在这个备份上建一个分支", onclick: async () => {
        const r = await act("backup", { ws: D.ws.id, repo: D.repo.id, ref: x.ref });
        if (r) { toast(`已建分支 ${r.branch}`); reloadRepo(); }
      } }, "恢复为分支"),
      h("button.ghost.small", { onclick: async () => {
        if (!await ask("删除备份", `删除 ${x.ref}？里面的 commit 之后可能找不回来。`, "删除", true)) return;
        if (await act("backup", { ws: D.ws.id, repo: D.repo.id, ref: x.ref, drop: true })) reloadRepo();
      } }, "删除"))));
}

// --- commit message: the template, highlighted ---
// [JIRA] lines -> keyword, # lines -> comment, **** -> to fill in, past 72 columns -> too long
function msgHtml(text) {
  return text.split("\n").map((line, i) => {
    if (line.startsWith("#")) return `<span class="m-c">${esc(line)}</span>`;
    const head = line.slice(0, 72), tail = line.slice(72);
    let html = esc(head).replace(/^(\s*)(\[[^\]]+\])/, '$1<span class="m-k">$2</span>')
      .replace(/(\*{3,}|\bNA\b(?=\s*$))/g, '<span class="m-f">$1</span>');
    if (i === 0 && line.trim() && !line.startsWith("[")) html = `<span class="m-s">${html}</span>`;
    return html + (tail ? `<span class="m-long">${esc(tail)}</span>` : "");
  }).join("\n") + "\n";
}

function msgEditor(value, onInput) {
  const back = h("pre.msg-hl", { "aria-hidden": "true" });
  const ta = h("textarea.msg#dr-msg", { placeholder: "commit message", value, spellcheck: false });
  const paint = () => { back.innerHTML = msgHtml(ta.value); back.scrollTop = ta.scrollTop; };
  ta.addEventListener("input", () => { paint(); onInput(ta.value); });
  ta.addEventListener("scroll", () => { back.scrollTop = ta.scrollTop; back.scrollLeft = ta.scrollLeft; });
  paint();
  const box = h("div.msg-box", back, ta);
  box.set = v => { ta.value = v; paint(); onInput(v); };
  box.ta = ta;
  return box;
}

function commitSection() {
  const { data, repo } = D;
  const draft = D.msg[repo.id] ?? (data.template ? stripTemplate(data.template) : "");
  const editor = msgEditor(draft, v => { D.msg[repo.id] = v; });
  const msg = editor.ta;
  const amend = h("input", { type: "checkbox" });
  const nStaged = data.changes.filter(c => c.staged).length;
  const tfile = data.template_file ? data.template_file.split(/[\\/]/).pop() : "";
  const load = full => {
    if (msg.value.trim() && msg.value !== stripTemplate(data.template) && !confirm("用模板替换当前的 message？")) return;
    editor.set(full ? data.template : stripTemplate(data.template));
    msg.focus();
  };
  const tmplBar = data.template ? h("div.row.small", { style: "margin-bottom:6px" },
    h("span.muted", { title: data.template_file }, `模板：${tfile}`),
    h("button.btn.small", { onclick: () => load(false), title: "只载入要填的行（# 说明行去掉）" }, "⤵ 载入模板"),
    h("button.ghost.small", { onclick: () => load(true), title: "带 # 说明行载入；提交时 # 行会自动去掉" }, "载入完整模板（含说明）"),
    h("div.spacer"),
    h("span.muted", "提交时 # 开头的行会去掉")) : h("div.muted.small", { style: "margin-bottom:6px" }, "这个仓库没有 commit 模板（commit.template 或 .git-commit-template）");
  const hook = data.hook ? h("span.pill.ok", "✓ commit-msg hook（Change-Id）")
    : h("span.row", h("span.pill.warn", "⚠ 没有 commit-msg hook，提交不会带 Change-Id"),
      h("button.btn.small", { onclick: installHook }, "安装 hook"));
  const doCommit = async () => {
    const args = { ws: D.ws.id, repo: repo.id, message: msg.value, amend: amend.checked };
    let r = await act("commit", args);
    if (r && r.need_confirm) {
      if (!await ask("amend 确认", h("div", r.need_confirm.map(x => h("div.risk", x))), "仍然 amend", true)) return;
      r = await act("commit", { ...args, confirm: true });
    }
    if (r && r.sha) {
      toast(`已提交 ${r.sha}`);
      delete D.msg[repo.id];
      await reloadRepo();
      refreshSoon(D.ws.id);
    }
  };
  return h("div.dr-sec", h("h4", "提交"), tmplBar, editor,
    h("div.row", { style: "margin-top:6px" }, h("label.row", amend, "amend"), hook, h("div.spacer"),
      h("button.ghost.small", { onclick: () => editor.set(""), title: "清空 message" }, "清空"),
      h("button.primary", { onclick: doCommit, title: nStaged ? "" : "没有 staged 的文件（amend 可以只改 message）" },
        nStaged ? `Commit (${nStaged})` : "Commit")));
}

async function installHook() {
  const r = await act("install_hook", { ws: D.ws.id, repo: D.repo.id });
  if (r) { toast("hook 已安装：" + r.hook); await reloadRepo(); }
}

function refChips(refs) {
  return (refs || []).map(r => h("span.ref." + r.kind, { title: { head: "当前分支", branch: "本地分支", remote: "远端分支",
    tag: "tag", manifest: "west update 记下的 manifest 版本" }[r.kind] }, r.kind === "tag" ? "🏷 " + r.name : r.name));
}

function commitRow(c, cls = "") {
  return h("div.c" + cls, h("span.sha", c.sha), h("span.s", { title: c.subject }, refChips(c.refs), c.subject),
    h("span.muted.small", c.author), h("span.muted.small", ago(c.time)));
}

function logSection() {
  const { log, upstream, tag, status } = D.data;
  // the upstream has commits HEAD lacks: show where it is, above HEAD's log
  const up = upstream ? h("div.upstream",
    h("div.small.muted", `${upstream.ref} 领先 ${upstream.behind} 个 commit`, upstream.behind > upstream.commits.length ? `（显示最新 ${upstream.commits.length} 个）` : "",
      "，pull --rebase 后会到这里"),
    upstream.commits.map(c => commitRow(c, ".ahead")),
    h("div.gap", "⋮")) : null;
  const facts = h("div.row.small", { style: "margin-bottom:6px" },
    status.upstream ? h("span.muted", `远端 ${status.upstream}：` + (status.behind || status.ahead
      ? `本地 ↑${status.ahead} ↓${status.behind}` : "和本地一致")) : h("span.muted", "没有 upstream"),
    tag ? h("span.ref.tag", { title: "HEAD 包含的最近一个 tag" }, "🏷 最近 tag " + tag) : null);
  return h("div.dr-sec.log", h("h4", "最近提交"), facts, up,
    log.length ? log.map(c => commitRow(c)) : h("div.muted", "（还没有提交）"));
}
