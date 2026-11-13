# ⬢ Workbay

**A local workbench for people who live in many git workspaces.**
One page on `127.0.0.1` shows the state of every repository on the machine, syncs a
west workspace to its manifest in one click, builds gcc projects, and finds the Claude
Code session you had last week.

![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![deps](https://img.shields.io/badge/deps-stdlib%20%2B%20west-success)
![platform](https://img.shields.io/badge/platform-Windows-0078d4)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

[中文说明](#中文说明)

## Why

When you have a dozen workspaces (west manifests, folders of plain clones, single repos)
the questions are always the same: *which ones are dirty, which are behind, which are
stuck in a half-done rebase, which branch does the manifest want?* Workbay answers them
on one page, read-only by default, and does the tedious fixes when you click.

## Features

| Tool | What it does |
|---|---|
| **Workspaces** | A card per workspace: dirty / ahead / behind / off-manifest / not cloned / last fetch. Repo drawer with changes, diff, stage, commit (commit template, Gerrit Change-Id hook), recent log. |
| **Sync to manifest** | One fetch per repo, switch to the branch the yml names and rebase; conflicts roll back; stuck repos are flagged with a fix. "Force align" stashes and backs up first, so nothing is lost. |
| **Build** | gcc `defconfig.<app>.<IC>.<variant>` projects via CMake, incremental; errors and warnings link to VS Code; J-Link flash with a global lock. |
| **Claude History** | Claude Code sessions from every directory: title, workspace, last model and context use, running or not; preview, resume in the original directory with the same model, delete to a restorable trash. |
| **Jobs** | Live output of every long task, per step, cancellable. |

Safe by design: it listens on 127.0.0.1 only and checks the origin of every write. Status
reads never fetch, and they run as low-priority, windowless git processes.

## Quick start

Requirements: Python ≥ 3.10, Git for Windows.

```bat
git clone https://github.com/CautiousG/workbay.git
cd workbay
ui.bat
```

`ui.bat` installs `west` once if it is missing, then opens http://127.0.0.1:8780/.
Nothing else is installed; settings live in `%APPDATA%\workbay\settings.json`.

```bat
python -m workbay status            :: the same overview in the terminal
python -m pytest -q                 :: tests (pip install pytest)
```

## Layout

```
workbay/
  core/      jobs (subprocess steps, events, locks), gitcmd, config
  ws/        discover, manifest, gitstat, westcli, ops, sync
  build.py   gcc defconfig build / flash (also a standalone Job step)
  claude_history.py
  ui/        api.py (plain calls -> JSON), server.py (HTTP + SSE), static/ (plain JS)
tests/       pytest, fixtures built in tmp
docs/design.md
```

Formerly *Apiary*: its settings (`%APPDATA%\apiary`), session trash and backup refs are
picked up as they are.

---

## 中文说明

一页看清本机所有 workspace 的状态，点一下就能 fetch / pull / `west update` / 跑命令 / 提交。

| 类型 | 识别方式 |
|---|---|
| west | 有 `.west/config`，按 manifest 列出 project |
| multi | 目录下（两层以内）有多个 git 仓库 |
| single | 目录本身就是 git 仓库 |

### 启动

**双击 `ui.bat`**，浏览器会打开 http://127.0.0.1:8780/ ，关掉黑色窗口即停止。

- 直接从这个目录运行，不安装 Workbay 本身；缺 `west` 时第一次运行会自动 `pip install --user west`。
- 第一次打开默认扫描除系统盘外所有本地硬盘的下一层目录；Settings 里可改扫描目录、添加或隐藏 workspace。
- 更新：`git pull`，重新打开 `ui.bat`。

### 功能

- **Workspaces**：每个 workspace 一张卡片，汇总 dirty / 偏离 manifest / 未 clone / behind / ahead / 最久未 fetch；展开看每个仓库。
  - manifest 列：`✓` HEAD = manifest-rev；`↗ 领先` 在 manifest-rev 之后（正常）；`⚑ 偏离` 分叉或落后；`○` 没有 manifest-rev。
  - 勾选仓库后出现批量栏：fetch、pull --rebase、west update 选中的 project、命令。
  - **✳ Claude**：在 workspace 或仓库目录开 cmd 窗口运行 `claude`（命令可在 Settings 改）。
- **同步到 manifest**：每个仓库只 fetch 一次，切到 yml 写的分支再 rebase；冲突自动回滚；卡住的仓库标红并给处理办法；"强制对齐"先 stash、备份本地 commit。卡片上可直接切换 yml。
- **west update**：先预检每个 project 会发生什么，有风险要再确认。
- **命令…**：`git <args>` 在每个（选中的）仓库执行，或 `west forall -c`。
- **Manifest**：Projects 表和 Raw 原文。
- **仓库抽屉**：改动、stage / unstage、diff、提交（带 `commit.template`、检查 Gerrit commit-msg hook）、amend、最近提交、快捷打开。
- **⚒ 编译**：gcc 的 defconfig 工程，每个 workspace 编自己的 IC；增量编译，error / warning 点击跳 VS Code，列出 bin；RTL87x2L 可 J-Link 编译 + 烧录（同时只烧一个）。
- **Claude History**：所有目录的 Claude Code 会话：标题、workspace、最后的模型（🏠 地端 / ☁ 云端）和上下文占比、是否在运行；预览，`claude --model <原模型> --resume <id>` 在原目录继续；勾选删到回收站，可恢复。
- **Jobs**：长任务实时输出，可取消。
- 深色 / 浅色主题（右上角 ◐）。

状态只读本地，**不会自动 fetch**。自动刷新（默认 60 秒）只读展开的和有问题的 workspace，全部最多 5 分钟一次；读状态的 git 是低优先级、无窗口的，不会拖慢电脑。

### 说明

- 只监听 127.0.0.1；写操作校验请求来源，其它网页无法调用。
- 执行 `west update` / `forall` 优先用 PATH 里的 `west`，Settings 可指定。
- 子进程不弹密码输入；ssh key 需要已配置好。
- 设计见 [docs/design.md](docs/design.md)。

## License

[MIT](LICENSE)
