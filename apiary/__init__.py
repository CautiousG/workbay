"""Apiary - Workspace Manager: one page for many west / multi-git / single-git workspaces.

Layers, each importing only the ones below it:

    ui/         api.py (Api: plain calls -> JSON-ready dicts), server.py (127.0.0.1 HTTP + SSE)
    ops, jobs   the write side: fetch / pull / west update / forall / git / commit / open
    discover, manifest, gitstat, westcli
    gitcmd, config
"""
__version__ = "0.1.0"
