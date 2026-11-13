"""Workbay - a workbench of local tools around workspaces, one page on 127.0.0.1.

Packages, each importing only the ones below it:

    ui/             api.py (Api: plain calls -> JSON-ready dicts), server.py (HTTP + SSE)
    build.py        gcc defconfig builds, J-Link flash (also a standalone Job step)
    claude_history  Claude Code's sessions: list, preview, resume, trash
    ws/             workspaces: discover, manifest, gitstat, westcli, ops, sync
    core/           jobs (subprocess steps, events, locks), gitcmd, config
"""
__version__ = "0.2.0"
