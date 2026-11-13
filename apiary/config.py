"""The settings, one JSON file per user (not in the repository, so a git pull of the
tool never touches them): %APPDATA%/apiary/settings.json, or $APIARY_HOME/settings.json."""
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

TERMINALS = ("wt", "cmd", "gitbash")
MAX_RECENT = 15


def default_roots():
    """A first run's scan roots: every fixed drive but the system one (workspaces live on
    D:, E:, ...), else the user's home."""
    if os.name != "nt":
        return [str(Path.home())]
    import ctypes
    system = (os.environ.get("SystemDrive") or "C:").upper()
    fixed = 3                                   # DRIVE_FIXED: not network, not removable
    drives = [f"{c}:\\" for c in "DEFGHIJKLMNOPQRSTUVWXYZ"
              if f"{c}:" != system and ctypes.windll.kernel32.GetDriveTypeW(f"{c}:\\") == fixed]
    return drives or [str(Path.home())]


@dataclass
class Settings:
    roots: list = field(default_factory=default_roots)      # scanned one level down
    added: list = field(default_factory=list)               # a workspace each, as is
    hidden: list = field(default_factory=list)              # never listed
    west_path: str = ""                                     # "": PATH, then our own
    refresh_s: int = 60                                     # 0: only by hand / on focus
    terminal: str = "gitbash"
    claude_cmd: str = "claude"                              # typed in a new cmd window
    recent_cmds: list = field(default_factory=list)         # newest first
    gcc_dir: str = ""                                       # "": PATH, then C:\Arm GNU Toolchain...
    build_ic: dict = field(default_factory=dict)            # ws path -> IC ("" = none), over the name's guess
    build_fav: list = field(default_factory=list)           # "<ws path>|<defconfig id>"

    def remember_cmd(self, cmd):
        self.recent_cmds = [cmd] + [c for c in self.recent_cmds if c != cmd]
        del self.recent_cmds[MAX_RECENT:]


def home():
    base = os.environ.get("APIARY_HOME") or os.path.join(os.environ.get("APPDATA") or Path.home(), "apiary")
    return Path(base)


def settings_path():
    return home() / "settings.json"


def _clean(data):
    """A dict from the file or the page -> only the known fields, each of its type."""
    out = {}
    for f in fields(Settings):
        if f.name not in data:
            continue
        value = data[f.name]
        if f.name == "build_ic":
            if not isinstance(value, dict):
                continue
            value = {str(k): str(v or "") for k, v in value.items()}
        elif f.name in ("roots", "added", "hidden", "recent_cmds", "build_fav"):
            if isinstance(value, str):
                value = value.splitlines()
            value = [str(v).strip() for v in value or [] if str(v).strip()]
        elif f.name == "refresh_s":
            try:
                value = max(0, int(value))
            except (TypeError, ValueError):
                continue
        elif f.name == "terminal":
            if value not in TERMINALS:
                continue
        else:
            value = str(value or "").strip()
        out[f.name] = value
    return out


def load():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    return Settings(**_clean(data if isinstance(data, dict) else {}))


def save(settings):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def update(settings, data):
    """The page's changes onto settings (unknown keys / bad values dropped)."""
    for key, value in _clean(data or {}).items():
        setattr(settings, key, value)
    return settings
