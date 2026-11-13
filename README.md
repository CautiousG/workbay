# Apiary · Workspace Manager

A local web UI for many west / multi-repo / single git workspaces on one machine:
the state of every repo on one page, sync a west workspace to its manifest in one
click, build gcc defconfig projects, and browse / resume Claude Code sessions.
Python standard library only (plus `west`), Windows first.

一页看清本机所有 workspace 的状态，点一下就能 fetch / pull / `west update` / 跑命令 / 提交。

支持三种 workspace：

| 类型 | 识别方式 | 例子 |
|---|---|---|
| west | 有 `.west/config`，按 manifest 列出 project | bee4、bee5、bb3、bbpro4 |
| multi | 目录下（两层以内）有多个 git 仓库 | bee2、bee3、Ameba |
| single | 目录本身就是 git 仓库 | Auto_Script |

## 安装与启动

需要 Python ≥ 3.10（安装时勾选 Add to PATH）和 Git for Windows。

**双击 `ui.bat`** 即可，浏览器会打开 http://127.0.0.1:8780/ ，关掉黑色窗口即停止。

- 不需要安装 Apiary 本身，直接从这个目录运行；唯一的依赖是 `west`，没有的话第一次运行会自动 `pip install --user west`。pip 不通时按窗口里的提示手动装。
- 第一次打开默认扫描本机除系统盘外的所有本地硬盘（D:\、E:\ …）的下一层目录；在 Settings 里改扫描目录、添加或隐藏 workspace。设置存在 `%APPDATA%\apiary\settings.json`，每人一份，不在仓库里。
- 更新：`git pull`，重新打开 `ui.bat`。
- 可以在桌面建一个 `ui.bat` 的快捷方式。

命令行查看状态（不开页面，在这个目录下执行）：

```bat
python -m apiary status          :: 用设置里的扫描目录
python -m apiary status D:\ E:\  :: 临时指定
```

## 功能

- **Workspaces**：每个 workspace 一张卡片，汇总 dirty / 偏离 manifest / 未 clone / behind / ahead / 最久未 fetch 时间；展开看每个仓库。
  - west 的 manifest 列：`✓` HEAD = manifest-rev；`↗ 领先` HEAD 在 manifest-rev 之后（pull 过，正常）；`⚑ 偏离` 分叉或落后；`○` 没有 manifest-rev。
  - 勾选仓库后出现批量栏：fetch、pull --rebase、west update 选中的 project、命令。
  - **✳ Claude**：在 workspace 根目录（卡片上）或仓库目录（抽屉里）打开一个 cmd 窗口并运行 `claude`；命令可在 Settings 改（如 `claude --continue`）。
- **west update**：先预检，列出每个 project 会发生什么、哪些有未提交改动或只在本地的 commit；有风险时要再确认。可选 `--keep-descendants`（默认）、`--rebase`、`--narrow`。
- **命令…**：`git <args>` 在每个（选中的）仓库执行，或 `west forall -c "<cmd>"`；最近用过的命令可一键复用。
- **Manifest**：Projects 表（path / revision / url / groups / 本地状态）和 Raw 原文。
- **仓库抽屉**（点仓库行）：改动列表、勾选 stage/unstage、看 diff、写 message 提交（自动带入 `commit.template`，检查 Gerrit commit-msg hook，可一键安装）、amend、最近 10 条提交；Git Bash / VS Code / 资源管理器 / gitk 快捷打开。
- **同步到 manifest**：每个仓库只 fetch 一次，切到 yml 写的分支再 rebase；冲突的自动回滚；卡住的仓库（rebase 一半、detached、分支不对）标红并给出处理；"强制对齐"会先 stash 改动、备份本地 commit。卡片上可直接切换 yml。
- **⚒ 编译**：gcc 的 defconfig 工程（`defconfig.<app>.<IC>.<variant>`），一个 workspace 编自己的 IC；增量编译，结果里 error / warning 可点击跳到 VS Code，列出产物 bin；RTL87x2L 可 J-Link 编译 + 烧录（同一时间只烧一个）。
- **Claude History**：所有目录下的 Claude Code 会话，带标题、workspace、最后的模型（🏠 地端 / ☁ 云端）和上下文占比、是否在运行；预览对话，`claude --model <原模型> --resume <id>` 在原目录继续；勾选删除到回收站，可恢复。
- **Jobs**：所有长任务的实时输出，按仓库分段，失败的标红，可取消。
- **Settings**：扫描根目录、额外添加 / 隐藏的目录、west 路径、终端程序、Claude Code 启动命令、自动刷新间隔。
- 默认深色主题，右上角 ◐ 切换浅色。设置存在 `%APPDATA%\apiary\settings.json`，每人一份。

状态只读本地，**不会自动 fetch**；fetch / pull / update 只在点击时执行。自动刷新（默认 60 秒）只读展开的和有问题的 workspace，全部最多 5 分钟一次；读状态的 git 是低优先级的，不会拖慢电脑。

## 说明

- 服务只监听 127.0.0.1；写操作校验请求来源，其它网页无法调用。
- 读 manifest 用 Apiary 环境里的 west 模块；执行 `west update` / `forall` 优先用 PATH 里的 `west`（和你自己敲命令一致），Settings 可指定路径。
- 子进程不会弹出密码输入；ssh key 需要已经配置好（和平时用 Gerrit 一样）。

## 开发

```bat
python -m pip install west pytest
python -m pytest -q
```

结构见 `docs/design.md`；代码分层见 `apiary/__init__.py`。
