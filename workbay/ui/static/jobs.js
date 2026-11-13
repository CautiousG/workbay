// Jobs view: the list, and the selected job's output by step, live from the events.
"use strict";

const J = { sel: null, out: {}, folded: {} };   // out: job id -> [[lines] per step]

const running = () => S.jobs.filter(j => j.state === "queued" || j.state === "running").length;

async function loadJobs() {
  const r = await act("jobs", {}, "GET");
  if (r) S.jobs = r.jobs;
  pill();
  rerender("jobs");
}

function pill() {
  const p = $("#jobs-pill"), n = running();
  p.className = "pill " + (n ? "info" : "idle");
  p.replaceChildren(...(n ? [h("span.spin"), ` ${n} 个任务执行中`] : []));
  p.onclick = () => show("jobs");
  p.style.cursor = "pointer";
}

onJobEvent(ev => {
  let job = S.jobs.find(j => j.id === ev.job);
  if (!job) {                                // a job we do not know yet: get the list
    if (ev.type === "job" && ev.state === "queued") loadJobs();
    return;
  }
  const out = J.out[ev.job] || (J.out[ev.job] = job.steps.map(() => []));
  if (ev.type === "job") {
    job.state = ev.state;
    if (["ok", "failed", "cancelled"].includes(ev.state)) {
      job.ended = Date.now() / 1000;
      if (ev.state === "failed") toast(`失败：${job.title}`, true);
      else if (ev.state === "ok") toast(`完成：${job.title}`);
    }
    pill();
    if (S.view === "jobs") paintJobList();
    else renderTabs();
  } else if (ev.type === "step") {
    Object.assign(job.steps[ev.step], { state: ev.state, rc: ev.rc });
    if (ev.state === "failed") J.folded[`${job.id}:${ev.step}`] = false;
    paintStep(job, ev.step);
  } else if (ev.type === "note") {
    job.steps[ev.step].note = ev.text;
    paintStep(job, ev.step);
  } else if (ev.type === "out") {
    const lines = out[ev.step];
    if (ev.progress && lines.length && lines[lines.length - 1].progress) lines[lines.length - 1] = ev;
    else if (!ev.progress && lines.length && lines[lines.length - 1].progress) lines[lines.length - 1] = ev;
    else lines.push(ev);
    if (lines.length > 3000) lines.splice(0, lines.length - 3000);
    paintStep(job, ev.step);
  }
});

VIEWS.jobs = {
  title: "Jobs",
  badge: () => running() || null,
  render(main) {
    main.append(h("div.jobs", h("div.joblist#joblist"), h("div.jobout#jobout")));
    paintJobList();
  },
};

function paintJobList() {
  const list = $("#joblist");
  if (!list) return;
  if (!S.jobs.length) {
    list.replaceChildren(h("div.empty", "还没有任务"));
    $("#jobout").replaceChildren(h("div.empty", "fetch / pull / west update / 命令的输出会出现在这里"));
    return;
  }
  if (!J.sel || !S.jobs.find(j => j.id === J.sel)) J.sel = S.jobs[0].id;
  list.replaceChildren(...S.jobs.map(j => h("div.j" + (j.id === J.sel ? ".on" : ""), { onclick: () => { J.sel = j.id; paintJobList(); } },
    h("div.t", h("span.st-" + j.state, stateIcon(j.state)), " ", j.title),
    h("div.small.muted.row", h("span", `#${j.id}`), h("span", `${j.steps.length} 步`),
      j.steps.filter(s => s.state === "failed").length ? h("span.st-failed", `${j.steps.filter(s => s.state === "failed").length} 失败`) : null,
      h("div.spacer"), h("span", j.started ? ago(j.started) : "")))));
  paintJob();
}

function stateIcon(s) {
  return { queued: "◌", running: "⏵", ok: "✓", failed: "✗", cancelled: "⊘", skipped: "–" }[s] || s;
}

async function paintJob() {
  const box = $("#jobout");
  const job = S.jobs.find(j => j.id === J.sel);
  if (!box || !job) return;
  if (!J.out[job.id]) await backfill(job);
  const live = job.state === "queued" || job.state === "running";
  box.replaceChildren(
    h("div.jh", h("b", job.title), h("span.st-" + job.state, `${stateIcon(job.state)} ${job.state}`), h("div.spacer"),
      h("button.ghost.small", { onclick: () => { job.steps.forEach((_, k) => J.folded[`${job.id}:${k}`] = true); paintJob(); } }, "全部折叠"),
      h("button.ghost.small", { onclick: () => { job.steps.forEach((_, k) => J.folded[`${job.id}:${k}`] = false); paintJob(); } }, "全部展开"),
      live ? h("button.danger", { onclick: () => act("cancel", { job: job.id }) }, "取消") : null),
    ...job.steps.map((s, k) => stepEl(job, k)));
}

// The events of a job from before this page opened: read them all once.
async function backfill(job) {
  J.out[job.id] = job.steps.map(() => []);
  try {
    const res = await fetch("/api/events?from=0&once");
    const text = await res.text();
    for (const block of text.split("\n\n")) {
      if (!block.startsWith("data: ")) continue;
      const ev = JSON.parse(block.slice(6));
      if (ev.job === job.id && ev.type === "out") {
        const lines = J.out[job.id][ev.step];
        if (lines.length && lines[lines.length - 1].progress) lines[lines.length - 1] = ev; else lines.push(ev);
      }
    }
  } catch (e) { /* the list still shows */ }
}

function stepEl(job, k) {
  const s = job.steps[k];
  const key = `${job.id}:${k}`;
  const fold = J.folded[key] ?? (job.steps.length > 3 && s.state === "ok");
  const lines = (J.out[job.id] || [])[k] || [];
  const pre = h("pre", lines.map(l => l.text.startsWith("$ ") ? h("span.cmd", l.text + "\n") : l.text + "\n"));
  return h("div.step." + s.state + (fold ? ".fold" : ""), { "data-step": key },
    h("div.sh", { onclick: () => { J.folded[key] = !fold; paintStep(job, k); } },
      h("span.st-" + s.state, stateIcon(s.state)), s.label,
      s.rc != null && s.rc !== 0 ? h("span.pill.bad", `rc ${s.rc}`) : null,
      s.note ? h("span.note.small", s.note) : null,
      h("span.muted.small", lines.length ? `${lines.length} 行` : "")), pre);
}

function paintStep(job, k) {
  if (S.view !== "jobs" || J.sel !== job.id) return;
  const el = $(`.step[data-step="${job.id}:${k}"]`);
  if (!el) return paintJob();
  const box = $("#jobout");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  el.replaceWith(stepEl(job, k));
  if (atBottom) box.scrollTop = box.scrollHeight;
}

BOOT.push(loadJobs);
