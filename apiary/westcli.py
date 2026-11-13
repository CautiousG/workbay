"""Which west runs the writes (update, forall): the settings' path, else the `west` on
PATH - what people type themselves - else this Python's own `python -m west`."""
import os
import shutil
import subprocess
import sys

from . import gitcmd


def command(west_path=""):
    """-> (argv prefix, where it came from: "settings" | "PATH" | "apiary")."""
    if west_path:
        return [west_path], "settings"
    found = shutil.which("west")
    if found:
        return [found], "PATH"
    return [sys.executable, "-m", "west"], "apiary"


def describe(west_path=""):
    """{argv, source, version | error} for the Settings page."""
    argv, source = command(west_path)
    out = {"argv": argv, "source": source, "version": "", "error": ""}
    if west_path and not os.path.exists(west_path):
        out["error"] = f"{west_path} 不存在"
        return out
    try:
        proc = subprocess.run([*argv, "--version"], capture_output=True, timeout=gitcmd.READ_TIMEOUT_S,
                              env=gitcmd.env(), stdin=subprocess.DEVNULL, creationflags=gitcmd.CREATE_FLAGS)
        text = (proc.stdout or proc.stderr).decode("utf-8", "replace").strip()
        out["version" if proc.returncode == 0 else "error"] = text
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["error"] = str(exc)
    return out
