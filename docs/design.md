# Apiary · Workspace Manager — 设计

日期：2026-11-12

## 1. 目标

Realtek BT 组的本地目录下堆着很多 workspace：新 IC 用 west manifest 管理，老 IC 是一个目录下放几个独立 git clone，还有单个 git 仓库。Apiary 是一个本地 Web UI，一页看清所有 workspace 的状态，点击就能 fetch / pull / `west update` / 跑自定义命令 / 提交，并能查看 manifest 内容。

给组里所有人用：放在共享仓库 `bt1sd1/others/Apiary`，拉下来双击 `ui.bat`。不安装 Apiary 本身（避免 pip 构建依赖网络、避免在共享目录里留 egg-info），直接 `python -m apiary` 从目录运行；只在缺 west 时 `pip install --user west`。首次默认扫描所有非系统本地硬盘。

### 第一版范围

- A 只读状态：分支、dirty、ahead/behind、上次 fetch；west 类型另有 HEAD 与 manifest-rev 是否一致
- B 同步：单仓 fetch、`pull --rebase`；workspace 级 `west update`（全部或勾选 project），先预检再执行
- D 自定义命令：`west forall -c` 或对勾选仓库跑 `git <args>`，实时输出
- F 快捷打开：Git Bash、终端、VS Code、资源管理器、gitk / git gui、workspace 下的 `.uvmpw` / `.code-workspace`
- Repo 详情：改动列表、diff、stage / unstage、commit（带 commit.template、Change-Id hook 检查）、最近提交
- Manifest 视图：Projects 表（revision / url / groups / 本地状态 / 来自哪个文件）+ Raw 只读原文

### 不做（第二版或不做）

- 第二版：Gerrit（open change、push 到 `refs/for/<branch>`）、定时后台 fetch、pywebview 独立窗口
- 不做：任意的分支切换 / 新建分支 / stash 管理（只有同步 / 强制对齐里按 manifest 切分支和 stash）、在页面里编辑 yml 内容、浏览 repo 文件树、打包 exe

## 2. 架构

沿用 ppt_tester 的结构：Api 层是普通 Python（入参普通值，出参 JSON-ready dict），HTTP 层很薄，前端原生 JS，标准库为主。

```
Apiary/
├─ pyproject.toml          依赖：west；requires-python >= 3.10
├─ ui.bat                  双击 → python -m apiary ui
├─ README.md
├─ docs/design.md
├─ apiary/
│  ├─ __main__.py          CLI：ui | status
│  ├─ config.py            设置的读写（%APPDATA%\apiary\settings.json）
│  ├─ gitcmd.py            git 子进程的统一入口（环境变量、编码、超时）
│  ├─ discover.py          扫描 → Workspace 列表，识别类型
│  ├─ manifest.py          west API 只读解析 manifest
│  ├─ gitstat.py           单仓状态 + 并发批量
│  ├─ westcli.py           选择执行写操作的 west
│  ├─ jobs.py              长任务：子进程、事件、取消、workspace 锁
│  ├─ ops.py               fetch / pull / update 预检 / 同步计划 / 卡住 / forall / commit / open
│  ├─ sync.py              同步 / 强制对齐一个仓库（Job 的一步，独立脚本）
│  ├─ build.py             编译 / 烧录一个 defconfig 工程（Job 的一步，独立脚本）
│  ├─ claude_history.py    Claude Code 的会话：列表、预览、回收站
│  └─ ui/
│     ├─ api.py            Api、ApiError
│     ├─ server.py         127.0.0.1 HTTP、/api/*、SSE /api/events
│     └─ static/app.js, app.css
└─ tests/
```

依赖方向：`ui` → `ops`/`jobs` → `discover`/`manifest`/`gitstat`/`westcli` → `gitcmd`/`config`。下层不 import 上层。

## 3. 数据模型

```
Workspace
  id        路径的规范化小写形式（Windows 不区分大小写）
  name      目录名
  path      绝对路径
  kind      "west" | "multi" | "single"
  repos     [Repo]
  manifest  ManifestInfo | None        仅 west
  error     str | None                 manifest 解析失败等；仍列出 repos

Repo
  name, path（相对 workspace）, abspath
  role      "project" | "manifest" | "top" | "plain"
            project: manifest 声明的；manifest: manifest 仓库自身；
            top: workspace 根目录本身是 git（如 bee5）；plain: multi/single 里的
  declared  {revision, url, groups, active, source} | None     仅 west
  cloned    bool                                               west 中可能未 clone

RepoStatus
  branch | None（detached）, head（短 sha）, upstream | None
  ahead, behind | None（无 upstream）
  staged, modified, untracked, conflicted   计数
  manifest_rev | None, on_manifest   HEAD == manifest-rev
  fetched_at | None                  .git/FETCH_HEAD 的 mtime
  error | None
```

## 4. 发现（discover.py）

输入：设置里的扫描根目录、手动添加目录、隐藏目录。

- 对每个扫描根，只看下一层子目录；手动添加的目录直接作为 workspace 候选。
- 类型判定（按顺序）：
  1. 有 `.west/config` → `west`。顶层若也有 `.git`，加一个 role=top 的 Repo。
  2. 顶层有 `.git`（目录或文件）→ `single`。
  3. 子目录（最多 2 层，遇到 `.git` 不再往下）里有 git 仓库 → `multi`。
  4. 都不是 → 不是 workspace，跳过。
- 跳过的目录名：`$RECYCLE.BIN`、`System Volume Information`、以 `.` 开头的、`node_modules`、`.venv`。
- 扫描结果缓存在内存；Settings 改变或点"刷新"时重新扫描。

## 5. Manifest（manifest.py）

- 只读：在工具自己的环境里 `from west.manifest import Manifest`，`Manifest.from_topdir(topdir=ws)`。
- 每个 project 输出：name、path、revision、url、groups、active（`is_active`）。west API 不暴露 project 来自哪个 import 的文件，所以不显示来源。
- west 的 schema 校验（pykwalify）不是线程安全的：并发加载会互相报错。`manifest.load` 用一把全局锁串行（每个约 0.2 s）。
- manifest 仓库自身作为 role=manifest 的 Repo。
- 顶部信息：manifest 文件的绝对路径、当前 group-filter。
- Raw 标签：返回 manifest 文件原文；import 进来的子文件可以切换查看（限 workspace 内的 `.yml`/`.yaml`）。
- 解析失败：Workspace.error 记录异常信息，repos 退回到 multi 的扫描结果。

## 6. 状态（gitstat.py）

- 公司 Windows 上每起一个 git 进程约 0.1 s，所以每个仓库只起一次 `git status --porcelain=v2 --branch`；manifest-rev（含 packed-refs）和 FETCH_HEAD 的 mtime 直接读 `.git` 下的文件。
- 解析 `# branch.head`、`# branch.upstream`、`# branch.ab`；条目行按首字符 `1`/`2`/`u`/`?` 计数，XY 中 X≠`.` 计 staged，Y≠`.` 计 modified。
- manifest 状态：HEAD = manifest-rev → ✓；不等时再跑一次 `merge-base --is-ancestor manifest-rev HEAD`，是祖先 → ↗ 领先（pull 过，不告警）；否则 → ⚑ 偏离（告警）。
- `.git` 是目录但没有 HEAD（如 GitKraken 留下的 `.git/gk`）不算仓库。
- 批量：`ThreadPoolExecutor(max_workers=16)`；本机 19 个 workspace / 209 个仓库约 7 s。页面按 workspace 逐个请求，先读展开的和有问题的。单仓失败只影响这一行。

## 7. 写操作（westcli.py / jobs.py / ops.py）

### west 的选择

写操作用的 west 按顺序找：设置里的路径 → PATH 里的 `west` → `sys.executable -m west`。Settings 页显示用的是哪个及其 `--version`。

### Job

- 一个 Job：id、标题、workspace id、步骤列表（每步一个仓库或一条命令）、状态（queued/running/ok/failed/cancelled）、输出事件。
- 输出逐行变成事件 `{job, step, stream, text}`，所有 Job 的事件放在一个全局有序列表里（上限 20000 条），SSE `/api/events?from=N` 推送。
- 同一个 workspace 同时只有一个写 Job；第二个请求返回 ApiError 409。不同 workspace 可以并行。
- 取消：终止当前子进程（Windows 上 `taskkill /T /F /PID`），剩下的步骤标记 cancelled。
- 子进程环境：`GIT_TERMINAL_PROMPT=0`；输出按 UTF-8 解码，`errors="replace"`。
- Job 只存在内存里，关掉工具即清空。

### 操作

| 操作 | 命令 | 前置检查 |
|---|---|---|
| fetch | `git fetch --prune`（每仓一步） | 无 |
| pull | `git pull --rebase` | detached 拒绝；dirty 需要 `confirm` |
| west update 预检 | 只读，返回每个 project：会不会 detach、dirty、只在本地的 commit（`HEAD --not manifest-rev --remotes`） | — |
| west update | `west update [--keep-descendants] [--rebase] [--narrow] [projects…]` | 预检有风险项时需要 `confirm` |
| forall | `west forall -c "<cmd>" [projects…]` | 仅 west |
| git 命令 | 对每个勾选仓库 `git <args>`（参数用 shlex 风格切分，不经 shell） | 无 |
| stage / unstage | `git add -- <files>` / `git restore --staged -- <files>` | 同步执行，不进 Job |
| commit | `git commit -F <tmpfile> [--amend]` | 无 staged 改动拒绝；amend 且已 push 到 upstream 时需要 `confirm` |
| open | Git Bash / 终端 / VS Code / 资源管理器 / gitk / git gui / 文件 | 路径必须是已知 workspace 内 |

| 同步到 manifest | 每个 project 一步：`python apiary/sync.py sync --remote R --revision REV [--autostash]` | 预检表；切分支 / autostash 时需要 `confirm` |
| 强制对齐 | `sync.py align`（每个勾选 project 一步） | 总是 `confirm`，列出会 stash / 备份的内容 |
| 解除卡住 | `rebase / merge / cherry-pick / revert / am --abort | --continue | --skip` | 同步执行；workspace 有 Job 时拒绝 |
| 切换 yml | 改 `.west/config` 的 `[manifest] file`（= `west config manifest.file`），然后重读 manifest | 只允许 manifest 仓库里的 `.yml` / `.yaml` |

需要 `confirm` 的请求，第一次不带 `confirm` 时，Api 返回 `{"need_confirm": [理由…]}`，页面弹窗确认后带 `confirm: true` 再发一次。执行前总是重新读状态，不用页面缓存。

### 同步到 manifest（sync.py）

日常流程原来是：改 `.west/config` 的 yml → `west update` → `west checkout`（manifest 里的扩展命令）→ 每个仓库 `git pull --rebase`。每个仓库 fetch 三次；一个仓库 rebase 冲突就留在半截，要逐个去找。现在合成一个按钮：

- **预检**（只读）：每个 active project 一行——branch 的：切到同名本地分支（没有就从 `<remote>/<branch>` 建并 track）再 rebase 到远端；tag / commit 的：detach 过去。dirty 的默认跳过，勾 autostash 才带上；卡住的（见下）跳过。
- **执行**：一个 Job，每个 project 一步，跑 `sync.py`（标准库脚本，进 Job 是为了输出、取消和 workspace 锁照旧）。每个仓库只 fetch 一次那个 revision，并把 `manifest-rev` 更新到它（和 `west update` 一致）。
- **失败回滚**：rebase 冲突 → `rebase --abort`，切回原分支 / 原 HEAD，stash 的改动放回；仓库和同步前一样，只是多了 fetch 到的对象。
- **不丢东西**：detached HEAD 上有不在任何分支上的 commit，切走前先存成 `refs/apiary/backup/<时间>-<sha>`。
- **结果**：退出码 0 完成 / 1 失败 / 2 跳过（`Step.skip_rc`），最后一行 `@@note <一句话>` 成为该步的说明（Job 事件 `type: note`）。结果弹窗列出失败 / 跳过的仓库，每行：重试、强制对齐、Git Bash、Claude Code、详情；多个时可"全部强制对齐"。
- 不用 `west update`：它对分支会 detach，且没有 rebase 回滚；`west update` 按钮仍保留（clone 新 project 还要它）。

### 卡住的仓库

`gitstat.status` 从 git dir 的文件判断进行中的操作（`rebase-merge`、`rebase-apply`、`MERGE_HEAD`、`CHERRY_PICK_HEAD`、`REVERT_HEAD`），不多起 git 进程。`ops.stuck(status, declared)` 给出原因和可用的处理：

| 原因 | 处理 |
|---|---|
| rebase / merge / cherry-pick / revert / am 进行中 | 放弃、继续（冲突改好并 stage 后）、rebase 可跳过、强制对齐 |
| 有冲突文件 | 强制对齐 |
| west project：detached 但 manifest 要分支 / 在别的分支 / 没有 upstream / 和 upstream 分叉 | 同步、强制对齐 |

卡片徽标数卡住的个数，展开后仓库表上方一条红色栏列出它们（点名字出处理菜单），行内也有 ⛔ 标记；抽屉顶部同样给按钮。

**强制对齐**（"这个仓库我不关心"）：放弃进行中的操作 → 改动 `git stash push`（不丢）→ fetch → 旧 HEAD 和旧的同名分支若不是目标的祖先，存到 `refs/apiary/backup/` → `checkout -B <branch> <remote>/<branch>` 并设 upstream（tag / commit：detach）。抽屉的"备份"段列出这些 ref，可"恢复为分支"或删除。

### 切换 yml

卡片上的 `manifest: <yml> ▾` 打开选择框：manifest 仓库里的全部 yml，可搜索，当前 yml 同目录的排前面、`archive/` 的排最后。选中后写 `.west/config`，重新 probe 这个 workspace，然后问"现在同步吗？"。不在页面里编辑 yml 内容。

### commit 细节

- message 模板：`git config commit.template` 指向的文件；没设置时用仓库根目录的 `.git-commit-template`（Realtek 的仓库大都带这个，但没配 commit.template）、`.gitmessage`。打开抽屉时预填去掉 `#` 行的模板；"载入模板"随时重新载入，"载入完整模板"连 `#` 说明一起（提交时 `--cleanup=strip` 会去掉）。
- 编辑框高亮（textarea 下面垫一层同字体的 `<pre>`）：`[JIRA]` 这类 `[关键词]` 行首、`#` 注释、`****` / 行尾 `NA` 这类待填处、第一行 subject、超过 72 列的部分。
- Change-Id：检查 `git rev-parse --git-path hooks/commit-msg` 是否存在；不存在时页面提示，提供"安装 hook"按钮（`scp -p -P 29418 <user>@<host>:hooks/commit-msg`，host 从 remote url 解析；失败就把命令显示给用户手动执行）。

## 8. 页面

一个 HTML 外壳（写在 server.py 里），`app.css`，JS 按部分分文件：`app.js`（工具函数、API、共享状态、tab、SSE）、`ws.js`（卡片、仓库表）、`dialogs.js`（批量栏、west update、命令、Manifest、自动刷新）、`drawer.js`、`jobs.js`、`settings.js`。三个视图：

- **Workspaces**：过滤框、类型筛选、"只看有问题"；workspace 卡片默认折叠，显示汇总徽标，有问题的排在前面；展开后是仓库表（west 多一列 manifest ✓/⚠/○）；行内按钮 fetch / pull / ⋯；勾选后出现批量栏。卡片按钮：west update、Fetch、命令…、Manifest（仅 west）、打开▾。
- **Repo 抽屉**：点行打开。头部快捷打开按钮；卡住时顶部红框给处理按钮；改动列表（勾选 stage）；点文件看 diff；commit 框（载入模板、高亮、amend、hook 状态）；最近 10 条提交，每条带上面的 ref（当前分支、本地 / 远端分支、tag、manifest-rev），另有和 upstream 的 ↑↓、HEAD 包含的最近 tag；upstream 有 HEAD 没有的 commit 时，在上方单列最新 3 个；有 Apiary 备份或 stash 时列出。
- **Manifest 弹层**：Projects 表 / Raw 原文。
- **Jobs**：左边列表，右边按步骤分段的输出，失败段标红，可取消。
- **Settings**：扫描根、手动添加 / 隐藏、west 路径、刷新间隔（默认 60 s，页面重新获得焦点时也刷新）、终端程序（Windows Terminal / cmd / Git Bash）、最近的自定义命令。
- 深色 / 浅色主题。

自动刷新只读本地状态，不 fetch；有写 Job 在跑的 workspace 跳过。

### 负载

全量读一次状态 = 188 个 `git status`（实测 2026-11-13，18 核）。原来 16 并发 × 3 个请求、每个 git 带一个 conhost：整机 CPU 62–64%，子进程 32，12–15 s，每分钟一次、切回窗口又一次——开着页面就一卡一卡。现在：

- 读状态的 git：`DETACHED_PROCESS`（没有 conhost）+ `BELOW_NORMAL_PRIORITY_CLASS`，`GIT_OPTIONAL_LOCKS=0`（status 不拿 index.lock 写回 index，和 VS Code 一样）。
- 所有请求合起来同时最多 `SLOTS` 个 git（核数 / 3，限 4–8）。
- 定时刷新（默认 60 s，页面在前台且有焦点、在 Workspaces 页时）只读展开的、有问题的、有勾选的 workspace；全部最多 5 分钟一次；切回窗口 30 s 内不重读。手动"⟳ 刷新"照旧读全部。
- 复测：整机 CPU 13–15%，子进程 6，无 conhost，10–11 s；结果和改之前一致。

## 9. HTTP 接口

- `GET /` 页面；`GET /static/*`
- `GET /api/<name>?…`：workspaces、status、repo、diff、manifest、manifest_raw、jobs、settings、env
- `GET` 另有 stuck
- `POST /api/<name>` `{json}`：rescan、fetch、pull、update_check、update、sync_check、sync、align、unstick、backup、set_manifest、forall、git、stage、unstage、commit、install_hook、open、cancel、settings
- `GET /api/events?from=N`：SSE
- 回复都是 JSON；拒绝的请求是 `{"error": "..."}` 加状态码。
- POST 检查 `Host` 是 `127.0.0.1:<port>`/`localhost:<port>`，`Origin`（如有）同源，否则 403。

## 10. 错误处理

- 单仓失败：行内 ⚠ + 原始错误，不影响其他。
- manifest 失败：卡片显示错误，退回 multi 扫描。
- 找不到 git：顶部横幅；找不到任何 west：west 相关按钮禁用并提示。
- 子进程超时：状态读取 15 s，写操作不设超时（可取消）。
- Api 抛 `ApiError(中文提示, status)`，页面 toast。

## 11. 编译（gcc · defconfig 工程）

只做 defconfig 写法的 gcc 工程（`defconfig.<app>.<IC>.<variant...>`），Keil 照旧开 IDE。烧录只接 J-Link（x2l，FPGA 阶段）；x2j / x3j / x3k 用各自的 MPTool / 工具手动烧，Apiary 只给 bin。

### 每个 workspace 一个 IC

同一个仓库支持多个 IC（bee5 里也有 x2l 的 defconfig），但一个 workspace 只编它自己的 IC。按目录名预填，Settings 里可改（`build_ic: {ws_path: IC}`）：

| 目录名含 | IC | 做法 |
|---|---|---|
| bee5 | RTL87x2J | realtek |
| bb3 | RTL87x3J | realtek |
| bbpro4 | RTL87x3K | realtek |
| beex | RTL87x2L | humac |

IC 和做法的对应是一张表（`build.py` 里），加 IC 加一行。

### target

- 扫描 workspace 下文件名 `defconfig.<app>.<IC>.<variant...>`（IC 不区分大小写，`RTL87X2L` = `RTL87x2L`），一个文件一个 target，显示 `app · variant`。跳过 `.git`、`build-cmake`、`build*`、`node_modules`。结果按 workspace 缓存，面板上可"重新扫描"。
- 搜索、收藏（Settings `build_fav`），PPT 的（app 以 `ppt` 开头或路径含 `\ppt\`）排前面。

### 两种做法

| | realtek（x2j / x3j / x3k） | humac（x2l） |
|---|---|---|
| 目录 | defconfig 往上最近的 `realtek\` | defconfig 所在目录 |
| configure | `cmake -G "MinGW Makefiles" . -D kconfig_path=./<相对 realtek> -D compile_lib_only=OFF -D COMPILER_DIR=<gcc> -D COMPILER_TYPE=gcc -B <build>` | `cmake -S <目录> -B <build> -G Ninja -D kconfig_path=<绝对>`；目录在 `\lib\` 下加 `-DPPT_LIB_INSTALL=ON` |
| build | `cmake --build <build> -j <核数>` | `cmake --build <build>` |
| 烧录 | 无（给 bin） | `cmake --build <build> --target <app>_flash`，build.ninja 里有这个 target 才给按钮 |

- build 目录：`<ws>\build-cmake\apiary\<app>.<variant>`，和手写 bat 的 `build-cmake\<app>` 分开。
- 重新 configure：没有 `CMakeCache.txt`，或 cache 里的 `kconfig_path` / 生成器和这次的不同。面板另有"重新 configure"、"清掉 build 目录"（确认）。
- gcc：Settings `gcc_dir`，空时找 PATH 里的 `arm-none-eabi-gcc`，再找 `C:\Arm GNU Toolchain arm-none-eabi\*\bin`（最新的）。
- 实测（2026-11-13）：beex `ppt_sync_master` 按上表 configure 5 s、build 27 s，`bin\ppt_sync_master.bin` 和 `_MP_*.bin`，有 `ppt_sync_master_flash`；sync lib 目录有 3 个 defconfig，必须带 kconfig_path。bbpro4 x3k `template ap.bank0` configure 通过，link 缺 `soc_log_module_init`（代码问题，不是做法问题）。

### 执行（build.py）

和 sync.py 一样是 Job 的一步、标准库独立脚本：`python apiary/build.py <build|flash> --kind realtek|humac --src <dir> --defconfig <路径> --build-dir <dir> [--gcc <dir>] [--reconfigure]`。cmake 按 `$APIARY_CMAKE`（测试用的替身）→ PATH → `C:\Program Files\CMake\bin` 找。

- 需要时先 configure，再 build（flash：build 带 `<app>_flash` target）；输出原样转发，同时写 `<build>\apiary_build.log`。
- 结束写 `<build>\apiary_build.json`：开始 / 结束时间、rc、error / warning 数、诊断（最多 200 条）、产物（`**\bin\*.bin`、`*.hex`、`*.elf` 的路径、大小、mtime）。面板读它显示上次结果，重启工具也在。
- 最后一行 `@@note 0 error · 2 warning · ppt_sync_master.bin 58 KB`。
- 诊断：gcc `file:line:col: error|warning|fatal error: msg`；ld `file:line:(section): msg`（如 undefined reference，算 error）；`collect2.exe: error:`、`ld.exe: ...` 无行号的也收。源文件是绝对路径。
- 一个 target 一步，勾选多个顺序编；不同 workspace 本来就能并行。
- **J-Link 全局锁**：flash 的 Job 另占 `jlink` 锁，同时只有一个烧录；编译不受影响。

### 页面

卡片上"编译"按钮（有对应 IC 的 defconfig 时）打开编译面板：IC 下拉、搜索、target 列表（勾选、☆、上次结果 ✓/✗、耗时、E/W 数、主 bin 大小、"旧产物"——bin 的 mtime 早于上次编译开始），底部按钮：编译、编译 + 烧录（humac 有 flash target 时）、重新 configure、清掉 build、打开 bin 目录。Job 结束弹结果表：每个 target 一行，展开看诊断，点诊断 `code -g file:line` 跳 VS Code（文件必须在 workspace 内）。

## 12. Claude Code 历史

在不同目录开的 Claude Code 会话集中列出，可预览、在原目录 resume、勾选删除。

`apiary/claude_history.py`。

- 数据（`$CLAUDE_CONFIG_DIR`，否则 `~\.claude`）：一个会话是 `projects\<目录>\<sid>.jsonl`（一行一条记录），同 id 的还有 `projects\<目录>\<sid>\`（subagents、tool-results）、`file-history\<sid>\`、`session-env\<sid>\`。`projects\<目录>\memory\` 和 `history.jsonl`（输入历史）不属于会话，**永远不碰**。格式是 Claude Code 内部的，解析从宽：读不出的字段留空，不报错。
- 列表：每个 jsonl 从头读到第一句用户输入（前面的 hook 输出可能很大；最多 4 MB），再读最后 64 KB——取 `cwd`、`gitBranch`、开始 / 结束时间、第一句（跳过工具结果、`<…>` 开头的命令回显 / 提醒、isMeta、sidechain）、`ai-title`（`/resume` 里看到的标题）、`last-prompt`。按 (路径, mtime, size) 缓存：本机 79 个会话 0.2 s，再读 0.01 s。显示：按天分组，标题或第一句、时间、workspace（cwd 在哪个 workspace 里）、目录、大小、最后的模型和上下文占比、是否在运行。
- 模型 / 上下文：最后一条非 sidechain 的 assistant 记录（最后 64 KB 里没有就往前找到 2 MB）。模型取 `requestedModel`（带 `[1m]` 的是 1M 窗口，否则 200K），老记录没有它就取 `message.model`；上下文 = `usage` 的 input + cache_read + cache_creation，超过 200K 的按 1M 算。
- 在运行：Claude Code 每个进程写 `sessions\<pid>.json`（sessionId、procStart = 进程创建时间的 FILETIME、status busy / idle）。pid 活着且创建时间对得上（差 < 1 s）才算——崩溃留下的文件、pid 被别的程序复用都排除。在运行的显示"● 运行中 / 打开着"，删除拒绝，继续时先提醒。过滤：关键字、workspace / 不在 workspace 里、"很短的"（< 50 KB 或没说话）、"N 天前的"。
- 预览：整份流式读，只留非 sidechain 的用户输入、斜杠命令、assistant 文字；连续的工具调用折成一行"🔧 Bash ×2 · Read"；每条截 2000 字，最多 300 条。
- 继续：在 `cwd` 开新 cmd 窗口跑 Settings 的 claude 命令，去掉其中的 `--continue` / `-c` / `--resume X`，加 `--resume <sid>`；cwd 不在了就提示。另可复制 `cd /d … && claude --resume <sid>`。
- 删除：勾选，确认后把上面四处移到 `apiary-trash\<时间>\`（同样的相对路径），在运行的、2 分钟内改过的拒绝。回收站弹窗：每批可"恢复"（原位置已有同名的项留在回收站）或"永久删除"，另有"清空回收站"（确认）、"打开目录"。
- 页面：顶部 tab"Claude History"，左列表（勾选、全选当前过滤结果、批量删除）+ 右预览。

## 13. 测试

pytest，fixture 在 tmp 下现造：

- bare remote + clone，造出 dirty / ahead / behind / detached / 无 upstream
- 最小 west workspace：manifest 仓库 + 两个 project（用本地 bare 路径当 url），跑 `west update` 生成 manifest-rev
- 用例：discover（三种类型 + west 叠加 git + 隐藏 / 手动）、gitstat 解析、manifest（active、manifest-rev 偏离）、ops（pull 拒绝、update 预检、commit、forall）、jobs（顺序、取消、workspace 锁）、api、server（路由、403、SSE once）

前端不做自动化测试；最后在本机 `D:\` 实测扫描并人工看页面。
