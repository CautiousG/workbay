// Settings view: scan roots, added / hidden directories, west, refresh, terminal.
"use strict";

VIEWS.settings = {
  title: "Settings",
  async render(main) {
    const box = h("div.settings");
    main.append(box);
    const [s, env] = await Promise.all([act("settings", {}, "GET"), act("env", {}, "GET")]);
    if (!s) return;
    S.settings = s.settings;
    if (env) S.env = env;
    const v = s.settings;
    const ta = (name, rows) => h("textarea", { name, rows, value: v[name].join("\n") });
    const roots = ta("roots", 3), added = ta("added", 3), hidden = ta("hidden", 3);
    const west = h("input", { type: "text", style: "width:100%", value: v.west_path, placeholder: "留空：PATH 里的 west，找不到就用 Apiary 自带的" });
    const refresh = h("input", { type: "number", min: 0, value: v.refresh_s, style: "width:90px" });
    const claude = h("input", { type: "text", style: "width:100%", value: v.claude_cmd, placeholder: "claude" });
    const gcc = h("input", { type: "text", style: "width:100%", value: v.gcc_dir, placeholder: "留空：PATH 里的 arm-none-eabi-gcc，再找 C:\\Arm GNU Toolchain arm-none-eabi\\*\\bin" });
    const term = h("select", [["gitbash", "Git Bash"], ["wt", "Windows Terminal"], ["cmd", "cmd"]].map(([k, t]) =>
      h("option", { value: k, selected: v.terminal === k }, t)));
    const save = async () => {
      const r = await act("settings", {
        roots: roots.value, added: added.value, hidden: hidden.value, west_path: west.value,
        refresh_s: refresh.value, terminal: term.value, claude_cmd: claude.value, gcc_dir: gcc.value,
      });
      if (!r) return;
      S.settings = r.settings;
      toast("已保存");
      S.status = {};
      await loadWorkspaces();
      show("settings");
    };
    const w = env ? env.west : null, t = env ? env.tools : {};
    box.append(
      h("div.card", h("h3", "扫描"),
        h("label", "扫描根目录（每行一个；只看下一层子目录）"), roots,
        h("label", "额外添加的 workspace（每行一个目录，原样加入）"), added,
        h("label", "隐藏的目录（每行一个）"), hidden),
      h("div.card", h("h3", "执行"),
        h("label", "west 路径（west update / forall 使用）"), west,
        w ? h("div.small", { style: "margin-top:4px" }, w.error ? h("span.st-failed", "⚠ " + w.error)
          : h("span.muted", `当前：${w.argv.join(" ")}（来源：${w.source}）  ${w.version}`)) : null,
        h("label", "终端程序（“打开终端”）"), term,
        h("label", "Claude Code 启动命令（在新 cmd 窗口、仓库目录下执行；例如 claude --continue）"), claude,
        h("label", "arm-none-eabi-gcc 的 bin 目录（编译用）"), gcc,
        h("label", "自动刷新本地状态间隔（秒，0 = 只手动刷新；页面重新获得焦点时也会刷新；不会自动 fetch）"), refresh),
      h("div.row", h("button.primary", { onclick: save }, "保存"), h("span.muted.small", "设置文件：" + s.path)),
      h("div.card", { style: "margin-top:14px" }, h("h3", "环境"),
        h("div.kv",
          h("div.k", "git"), h("div.mono", env && env.git ? `${env.git}  (${env.git_version})` : "未找到"),
          h("div.k", "Git Bash"), h("div.mono", t.gitbash || "未找到"),
          h("div.k", "gitk"), h("div.mono", t.gitk || "未找到"),
          h("div.k", "VS Code"), h("div.mono", t.vscode || "未找到"),
          h("div.k", "Claude Code"), h("div.mono", t.claude || "未找到（不在 PATH）"),
          h("div.k", "Windows Terminal"), h("div.mono", t.wt || "未找到"))),
      h("div.card", h("h3", "关于"), h("div.small.muted",
        "Apiary · Workspace Manager — 本地页面，只监听 127.0.0.1。状态只读本地，fetch / pull / west update 只在你点击时执行。")));
  },
};
