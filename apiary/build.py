"""Build one gcc defconfig project with CMake, and flash it over J-Link (x2l).

A defconfig project is a file  defconfig.<app>.<IC>.<variant...>  ; two ways to build it:

    realtek  (RTL87x2J / RTL87x3J / RTL87x3K)  in the nearest realtek\\ above the file:
             cmake -G "MinGW Makefiles" . -D kconfig_path=./<rel> -D compile_lib_only=OFF
                   -D COMPILER_DIR=<gcc> -D COMPILER_TYPE=gcc -B <build>
    humac    (RTL87x2L)  in the file's own directory:
             cmake -S <dir> -B <build> -G Ninja -D kconfig_path=<abs> [-DPPT_LIB_INSTALL=ON]
             flash: cmake --build <build> --target <app>_flash   (JLink.exe Commander script)

A Job step runs it as `python build.py build|flash --kind K --src DIR --defconfig F
--build-dir D [--gcc DIR] [--reconfigure]`, so the Job's output, cancel and workspace
lock apply. It configures when needed, builds (and flashes), forwards the output, and
writes <build>/apiary_build.json (the result the page shows, also after a restart) and
<build>/apiary_build.log. Exit code 0 done, 1 failed; the last line is "@@note <text>".

The page's side (which IC a workspace is, the targets in it) is scan() / default_ic()
below. Standard library only: it runs as a script, outside the package.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

NOTE = "@@note "
RESULT = "apiary_build.json"
LOG = "apiary_build.log"
MAX_DIAGS = 200

# IC -> how it builds. Matched without case (RTL87X2L = RTL87x2L).
ICS = {"RTL87x2J": "realtek", "RTL87x3J": "realtek", "RTL87x3K": "realtek", "RTL87x2L": "humac"}
# A workspace's directory name -> its IC (a workspace builds its own IC only, though the
# repository holds the others too). The first match wins; Settings can override it.
WS_IC = (("bee5", "RTL87x2J"), ("bb3", "RTL87x3J"), ("bbpro4", "RTL87x3K"), ("beex", "RTL87x2L"))
_SKIP_DIRS = {"node_modules", "objects", "listings", "out", "__pycache__"}


def default_ic(ws_name):
    name = ws_name.lower()
    return next((ic for key, ic in WS_IC if key in name), None)


def canon_ic(ic):
    """"rtl87X2l" -> "RTL87x2L", None when it is not one of ICS."""
    return next((k for k in ICS if k.lower() == str(ic or "").lower()), None)


# --- targets (the page's list) --------------------------------------------------------

def parse_name(name):
    """"defconfig.ppt_test.RTL87x2L.default" -> (app, IC as written, variant) or None."""
    parts = name.split(".")
    if len(parts) < 3 or parts[0] != "defconfig" or not parts[1]:
        return None
    return parts[1], parts[2], ".".join(parts[3:]) or "default"


def _realtek_root(path, ws_path):
    """The nearest directory named realtek above path (inside ws_path), or None."""
    d, top = os.path.dirname(path), os.path.normcase(os.path.abspath(ws_path))
    while d and os.path.normcase(d).startswith(top):
        if os.path.basename(d).lower() == "realtek":
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def scan(ws_path, ic):
    """Every defconfig project of ic in ws_path -> [target dict], PPT ones first."""
    ic = canon_ic(ic)
    if not ic:
        return []
    kind, found = ICS[ic], []
    for base, dirs, names in os.walk(ws_path):
        dirs[:] = [d for d in dirs if not d.startswith((".", "build")) and d.lower() not in _SKIP_DIRS]
        for n in names:
            p = parse_name(n) if n.startswith("defconfig.") else None
            if not p or p[1].lower() != ic.lower():
                continue
            path = os.path.join(base, n)
            src = _realtek_root(path, ws_path) if kind == "realtek" else base
            if not src:
                continue
            rel = os.path.relpath(path, ws_path).replace("\\", "/")
            found.append({"id": rel, "app": p[0], "variant": p[2], "ic": ic, "kind": kind, "path": path,
                          "src": src, "lib": "/lib/" in "/" + rel.lower(),
                          "ppt": p[0].lower().startswith("ppt") or "/ppt/" in "/" + rel.lower()})
    # the build directory: <app>.<variant>, and a hash of the path when two share it
    names = {}
    for t in found:
        names.setdefault(f"{t['app']}.{t['variant']}", []).append(t)
    for key, same in names.items():
        for t in same:
            tail = "" if len(same) == 1 else "-" + hashlib.sha1(t["id"].encode()).hexdigest()[:6]
            t["build_dir"] = os.path.join(ws_path, "build-cmake", "apiary", key + tail)
            t["flash"] = kind == "humac" and not t["lib"]
    return sorted(found, key=lambda t: (not t["ppt"], t["app"].lower(), t["variant"].lower(), t["id"]))


def last_result(build_dir):
    try:
        with open(os.path.join(build_dir, RESULT), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# --- tools ----------------------------------------------------------------------------

def find_gcc(setting=""):
    """The bin directory of arm-none-eabi-gcc: the setting, PATH, the usual install."""
    if setting:
        return setting
    found = shutil.which("arm-none-eabi-gcc")
    if found:
        return os.path.dirname(found)
    base = r"C:\Arm GNU Toolchain arm-none-eabi"
    try:
        versions = sorted((d for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "bin", "arm-none-eabi-gcc.exe"))),
                          key=lambda d: [int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", d)])
    except OSError:
        versions = []
    return os.path.join(base, versions[-1], "bin") if versions else ""


def find_cmake():
    """$APIARY_CMAKE (another cmake, the tests' stand-in), PATH, the usual install."""
    exe = os.environ.get("APIARY_CMAKE") or shutil.which("cmake")
    if exe:
        return exe
    exe = r"C:\Program Files\CMake\bin\cmake.exe"
    return exe if os.path.isfile(exe) else ""


def find_jlink():
    found = shutil.which("JLink")
    if found:
        return found
    for base in (r"C:\Program Files\SEGGER", r"C:\Program Files (x86)\SEGGER"):
        try:
            dirs = sorted(d for d in os.listdir(base) if d.lower().startswith("jlink"))
        except OSError:
            continue
        for d in reversed(dirs):
            if os.path.isfile(os.path.join(base, d, "JLink.exe")):
                return os.path.join(base, d, "JLink.exe")
    return ""


def tools(gcc_setting=""):
    return {"cmake": find_cmake() or None, "ninja": shutil.which("ninja"),
            "make": shutil.which("mingw32-make") or shutil.which("make"),
            "gcc": find_gcc(gcc_setting) or None, "jlink": find_jlink() or None}


# --- diagnostics ----------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_GCC = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[^:*?\"<>|]+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
                  r"(?P<sev>fatal error|error|warning):\s*(?P<msg>.*)$")
_LD_LINE = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[^:*?\"<>|]+?):(?P<line>\d+):\((?P<sect>[^)]*)\):\s*(?P<msg>.*)$")
_LD = re.compile(r"^(?:.*[\\/])?(?:arm-none-eabi-)?(?:ld|collect2)(?:\.exe)?:\s*(?P<msg>.*)$")
_CMAKE = re.compile(r"^CMake (?P<sev>Error|Warning)(?: \(dev\))?(?: at (?P<file>.+?):(?P<line>\d+))?")


def diagnostic(text):
    """One output line -> {sev: error|warning, msg, file, line} or None."""
    text = _ANSI.sub("", text).strip()
    m = _GCC.match(text)
    if m:
        return {"sev": "warning" if m["sev"] == "warning" else "error", "msg": m["msg"],
                "file": m["file"], "line": int(m["line"])}
    m = _LD_LINE.match(text)
    if m and "in function" not in m["msg"]:
        return {"sev": "error", "msg": m["msg"], "file": m["file"], "line": int(m["line"])}
    m = _LD.match(text)
    if m:
        msg = m["msg"]
        if re.search(r"in function [`'].*':$|ld returned \d+ exit status", msg):
            return None
        warn = msg.lower().startswith("warning:")
        return {"sev": "warning" if warn else "error", "msg": re.sub(r"^(warning|error):\s*", "", msg, flags=re.I),
                "file": None, "line": None}
    m = _CMAKE.match(text)
    if m:
        return {"sev": m["sev"].lower(), "msg": text, "file": m["file"],
                "line": int(m["line"]) if m["line"] else None}
    return None


def _resolve(file, dirs):
    """A diagnostic's file as an absolute path when it can be found (ninja's are relative)."""
    if not file:
        return None
    if os.path.isabs(file):
        return os.path.normpath(file)
    for d in dirs:
        p = os.path.normpath(os.path.join(d, file))
        if os.path.isfile(p):
            return p
    return file


# --- products -------------------------------------------------------------------------

def products(build_dir):
    out = []
    for base, dirs, names in os.walk(build_dir):
        dirs[:] = [d for d in dirs if d != "CMakeFiles"]
        for n in names:
            if n.lower().endswith((".bin", ".hex", ".elf")):
                p = os.path.join(base, n)
                st = os.stat(p)
                out.append({"path": p, "name": n, "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: x["path"].lower())
    return out[:60]


def main_bin(items, app):
    """The image people flash: <app>.bin / app.bin in a bin\\ directory, not the MP or hashed copy."""
    bins = [x for x in items if x["name"].lower().endswith(".bin")]
    plain = [x for x in bins if "_mp_" not in x["name"].lower() and not re.search(r"-[0-9a-f]{16,}\.bin$", x["name"].lower())]
    for want in (app.lower() + ".bin", "app.bin"):
        hit = next((x for x in plain if x["name"].lower() == want), None)
        if hit:
            return hit
    return (plain or bins or [None])[0]


def _size(n):
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


# --- the run --------------------------------------------------------------------------

def _cache(build_dir):
    """CMakeCache.txt -> {name: value}."""
    out = {}
    try:
        with open(os.path.join(build_dir, "CMakeCache.txt"), encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^([A-Za-z_][\w.-]*):[A-Z]+=(.*)$", line.rstrip("\n"))
                if m:
                    out[m[1]] = m[2]
    except OSError:
        pass
    return out


def configure_argv(kind, src, defconfig, build_dir, gcc="", cmake="cmake"):
    """-> (argv, cwd, the cache values it sets that tell whether a cache is ours)."""
    bd = build_dir.replace("\\", "/")
    if kind == "realtek":
        kcfg = "./" + os.path.relpath(defconfig, src).replace("\\", "/")
        argv = [cmake, "-G", "MinGW Makefiles", ".", "-D", f"kconfig_path={kcfg}", "-D", "compile_lib_only=OFF",
                "-D", f"COMPILER_DIR={gcc.replace(chr(92), '/')}", "-D", "COMPILER_TYPE=gcc", "-B", bd]
        return argv, src, {"kconfig_path": kcfg, "CMAKE_GENERATOR": "MinGW Makefiles"}
    kcfg = defconfig.replace("\\", "/")
    want = {"kconfig_path": kcfg, "CMAKE_GENERATOR": "Ninja"}
    argv = [cmake, "-S", src.replace("\\", "/"), "-B", bd, "-G", "Ninja", "-D", f"kconfig_path={kcfg}"]
    if gcc:
        argv += ["-D", f"COMPILER_DIR={gcc.replace(chr(92), '/')}"]
    if "/lib/" in "/" + kcfg.lower():
        argv += ["-DPPT_LIB_INSTALL=ON"]
        want["PPT_LIB_INSTALL"] = "ON"
    return argv, src, want


def needs_configure(build_dir, want):
    cache = _cache(build_dir)
    return not cache or any(cache.get(k) != v for k, v in want.items())


def _drop_cache(build_dir):
    try:
        os.remove(os.path.join(build_dir, "CMakeCache.txt"))
    except OSError:
        pass
    shutil.rmtree(os.path.join(build_dir, "CMakeFiles"), ignore_errors=True)


class Runner:
    """Runs the commands, echoes and logs their output, collects the diagnostics."""

    def __init__(self, build_dir, out=print):
        self.build_dir, self.out = build_dir, out
        self.diags, self._seen = [], set()
        self.log = open(os.path.join(build_dir, LOG), "w", encoding="utf-8", errors="replace")

    def say(self, text):
        self.out(text)
        self.log.write(text + "\n")

    def __call__(self, argv, cwd):
        self.say("$ " + " ".join(f'"{a}"' if " " in a else a for a in argv) + f"   ({cwd})")
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self.say(f"无法启动 {argv[0]}：{exc}")
            self._add({"sev": "error", "msg": f"无法启动 {argv[0]}：{exc}", "file": None, "line": None}, [])
            return -1
        for raw in proc.stdout:
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            self.say(text)
            d = diagnostic(text)
            if d:
                self._add(d, [self.build_dir, cwd])
        return proc.wait()

    def _add(self, d, dirs):
        d["file"] = _resolve(d["file"], dirs)
        key = (d["sev"], d["file"], d["line"], d["msg"])
        if key not in self._seen:
            self._seen.add(key)
            self.diags.append(d)

    def close(self):
        self.log.close()


def _write(build_dir, data):
    tmp = os.path.join(build_dir, RESULT + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(build_dir, RESULT))


def run(action, kind, src, defconfig, build_dir, gcc="", reconfigure=False, jobs=0, out=print):
    """-> (exit code, note). Writes RESULT in build_dir, the state at each stage."""
    os.makedirs(build_dir, exist_ok=True)
    app = (parse_name(os.path.basename(defconfig)) or ("app",))[0]
    cmake = find_cmake() or "cmake"
    started = time.time()
    result = {"action": action, "defconfig": defconfig, "app": app, "state": "running", "started": started,
              "ended": None, "rc": None, "stage": "configure", "errors": 0, "warnings": 0, "diags": [],
              "products": [], "main_bin": None}
    _write(build_dir, result)
    r = Runner(build_dir, out)
    rc, stage = 0, "configure"
    try:
        argv, cwd, want = configure_argv(kind, src, defconfig, build_dir, gcc, cmake)
        if reconfigure or needs_configure(build_dir, want):
            if os.path.exists(os.path.join(build_dir, "CMakeCache.txt")):
                r.say("配置变了或要求重新 configure：清掉 CMakeCache.txt / CMakeFiles")
                _drop_cache(build_dir)
            rc = r(argv, cwd)
        if rc == 0:
            stage = "build"
            build = [cmake, "--build", build_dir.replace("\\", "/")]
            rc = r(build + ["-j", str(jobs or os.cpu_count() or 4)], cwd)
        if rc == 0 and action == "flash":
            stage = "flash"
            target = f"{app}_flash"
            ninja = os.path.join(build_dir, "build.ninja")
            text = open(ninja, encoding="utf-8", errors="replace").read() if os.path.isfile(ninja) else ""
            if f"build {target}:" not in text:
                r.say(f"没有 {target} 这个 target（cmake 没找到 JLink.exe？）")
                r.diags.append({"sev": "error", "msg": f"没有 {target} target：cmake configure 时没找到 JLink.exe",
                                "file": None, "line": None})
                rc = 1
            else:
                rc = r(build + ["--target", target], cwd)
    finally:
        r.close()
    items = products(build_dir)
    mb = main_bin(items, app)
    errors = sum(d["sev"] == "error" for d in r.diags)
    warnings = sum(d["sev"] == "warning" for d in r.diags)
    result.update(state="ok" if rc == 0 else "failed", ended=time.time(), rc=rc, stage=stage, errors=errors,
                  warnings=warnings, diags=r.diags[:MAX_DIAGS], products=items, main_bin=mb)
    _write(build_dir, result)
    took = f"{result['ended'] - started:.0f} s"
    if rc == 0:
        done = "已烧录" if action == "flash" else ""
        bits = [f"{errors} error · {warnings} warning", f"{mb['name']} {_size(mb['size'])}" if mb else "没有 .bin", took, done]
        return 0, " · ".join(b for b in bits if b)
    first = next((d for d in r.diags if d["sev"] == "error"), None)
    where = f"{os.path.basename(first['file'])}:{first['line']} " if first and first["file"] and first["line"] else ""
    what = f"{where}{first['msg']}" if first else f"rc {rc}"
    return 1, f"{stage} 失败（{errors} error）：{what[:160]} · {took}"


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("action", choices=("build", "flash"))
    p.add_argument("--kind", choices=("realtek", "humac"), required=True)
    p.add_argument("--src", required=True)
    p.add_argument("--defconfig", required=True)
    p.add_argument("--build-dir", required=True)
    p.add_argument("--gcc", default="")
    p.add_argument("--reconfigure", action="store_true")
    p.add_argument("--jobs", type=int, default=0)
    a = p.parse_args(argv)
    code, note = run(a.action, a.kind, a.src, a.defconfig, a.build_dir, a.gcc, a.reconfigure, a.jobs)
    print(NOTE + note, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
