"""build.py (targets, diagnostics, a run with a stand-in cmake), the J-Link lock, and the
Api calls over them."""
import json
import os
import sys
import textwrap
import time

import pytest

from apiary import build
from apiary.jobs import Busy, Jobs, Step

# A cmake stand-in: configure writes CMakeCache.txt (+ build.ninja with the flash target),
# build writes bin\<app>.bin and prints a gcc warning; FAKE_FAIL=1 makes the build fail.
FAKE_CMAKE = textwrap.dedent(r'''
    import os, sys
    a = sys.argv[1:]
    if "--build" in a:
        bd = a[a.index("--build") + 1]
        if "--target" in a:
            print("flashing " + a[a.index("--target") + 1]); sys.exit(0)
        if os.environ.get("FAKE_FAIL"):
            print(r"D:\ws\src\main.c:12:5: error: 'x' undeclared (first use in this function)")
            print("collect2.exe: error: ld returned 1 exit status")
            sys.exit(1)
        print(r"D:\ws\src\main.c:7:9: warning: unused variable 'y' [-Wunused-variable]")
        os.makedirs(os.path.join(bd, "bin"), exist_ok=True)
        app = open(os.path.join(bd, "app.txt")).read()
        for n in (app + ".bin", app + "_MP_sdk_1.bin"):
            open(os.path.join(bd, "bin", n), "wb").write(b"x" * 2048)
        sys.exit(0)
    bd = a[a.index("-B") + 1]
    os.makedirs(bd, exist_ok=True)
    d = dict(x.split("=", 1) for x in a if "=" in x and not x.startswith("-"))
    gen = a[a.index("-G") + 1]
    kcfg = d["kconfig_path"]
    app = os.path.basename(kcfg).split(".")[1]
    with open(os.path.join(bd, "CMakeCache.txt"), "w") as f:
        f.write(f"kconfig_path:UNINITIALIZED={kcfg}\nCMAKE_GENERATOR:INTERNAL={gen}\n")
        if "-DPPT_LIB_INSTALL=ON" in a:
            f.write("PPT_LIB_INSTALL:BOOL=ON\n")
    open(os.path.join(bd, "app.txt"), "w").write(app)
    open(os.path.join(bd, "configured.txt"), "a").write("x")
    open(os.path.join(bd, "build.ninja"), "w").write(f"build {app}_flash: phony\n")
    print("-- defconfig: " + kcfg)
''')


def touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def tree(tmp_path):
    """A workspace holding realtek (x2j) and humac (x2l) defconfig projects."""
    ws = tmp_path / "bee5-workspace"
    touch(ws / "realtek/sample/ppt/simple/proj_ptx/rtl87x2j/defconfig.ppt_simple_ptx.RTL87x2J.bank0")
    touch(ws / "realtek/sample/template/proj/rtl87x2j/defconfig.template.RTL87x2J.bank0")
    touch(ws / "realtek/sample/template/proj/rtl87x2j/defconfig.template.RTL87x2J.bank1")
    touch(ws / "realtek/subsys/ppt/humac/sample/ppt_sync/proj_master/rtl87x2l/defconfig.ppt_sync_master.RTL87X2L.default")
    touch(ws / "realtek/subsys/ppt/humac/sync/lib/rtl87x2l/proj/defconfig.ppt_sync_lib.RTL87x2L.ppt_sync")
    touch(ws / "build-cmake/old/defconfig.ppt_simple_ptx.RTL87x2J.bank0")      # a build dir: skipped
    touch(ws / "elsewhere/defconfig.orphan.RTL87x2J.bank0")                    # no realtek above: skipped
    return ws


@pytest.fixture
def fake_cmake(tmp_path, monkeypatch):
    script = touch(tmp_path / "fake" / "fake_cmake.py", FAKE_CMAKE)
    cmd = touch(tmp_path / "fake" / "cmake.cmd", f'@"{sys.executable}" "{script}" %*\n')
    monkeypatch.setenv("APIARY_CMAKE", str(cmd))      # also seen by build.py run as a Job step
    return cmd


def test_parse_name_and_ic():
    assert build.parse_name("defconfig.ppt_test.RTL87x2L.default") == ("ppt_test", "RTL87x2L", "default")
    assert build.parse_name("defconfig.template.RTL87x3K.ap.bank0") == ("template", "RTL87x3K", "ap.bank0")
    assert build.parse_name("defconfig.x") is None
    assert build.canon_ic("rtl87X2l") == "RTL87x2L" and build.canon_ic("RTL9999") is None
    assert build.default_ic("bee5-workspace") == "RTL87x2J"
    assert build.default_ic("bbpro4-radio-workspace") == "RTL87x3K"
    assert build.default_ic("bb3-workspace") == "RTL87x3J"
    assert build.default_ic("beex-workspace") == "RTL87x2L"
    assert build.default_ic("Ameba-workspace") is None


def test_scan_one_ic_only(tree):
    x2j = build.scan(str(tree), "RTL87x2J")
    assert [(t["app"], t["variant"]) for t in x2j] == [
        ("ppt_simple_ptx", "bank0"), ("template", "bank0"), ("template", "bank1")]      # PPT first
    t = x2j[0]
    assert t["kind"] == "realtek" and t["src"] == str(tree / "realtek") and not t["flash"]
    assert t["build_dir"] == os.path.join(str(tree), "build-cmake", "apiary", "ppt_simple_ptx.bank0")
    x2l = {t["app"]: t for t in build.scan(str(tree), "rtl87x2l")}
    assert set(x2l) == {"ppt_sync_master", "ppt_sync_lib"}
    assert x2l["ppt_sync_master"]["flash"] and x2l["ppt_sync_master"]["kind"] == "humac"
    assert x2l["ppt_sync_lib"]["lib"] and not x2l["ppt_sync_lib"]["flash"]
    assert x2l["ppt_sync_master"]["src"].endswith("rtl87x2l")
    assert build.scan(str(tree), None) == []


def test_scan_same_name_gets_its_own_build_dir(tree):
    touch(tree / "realtek/other/rtl87x2j/defconfig.template.RTL87x2J.bank0")
    dirs = [t["build_dir"] for t in build.scan(str(tree), "RTL87x2J") if t["app"] == "template" and t["variant"] == "bank0"]
    assert len(dirs) == 2 and len(set(dirs)) == 2


@pytest.mark.parametrize("line, want", [
    (r"D:\ws\a.c:12:5: error: 'x' undeclared", ("error", 12, "'x' undeclared")),
    ("D:/ws/a.c:7:9: warning: unused variable 'y' [-Wunused-variable]", ("warning", 7, "unused variable 'y' [-Wunused-variable]")),
    ("../src/a.h:3: fatal error: b.h: No such file or directory", ("error", 3, "b.h: No such file or directory")),
    (r"D:\ws\boot\system_rtl.c:317:(.text.system_init+0xe): undefined reference to `soc_log_module_init'",
     ("error", 317, "undefined reference to `soc_log_module_init'")),
    ("c:/arm gnu/bin/../arm-none-eabi/bin/ld.exe: region `FLASH' overflowed by 64 bytes", ("error", None, "region `FLASH' overflowed by 64 bytes")),
    ("CMake Error at sample/CMakeLists.txt:9 (include):", ("error", 9, "CMake Error at sample/CMakeLists.txt:9 (include):")),
])
def test_diagnostic(line, want):
    d = build.diagnostic(line)
    assert (d["sev"], d["line"], d["msg"]) == want


@pytest.mark.parametrize("line", [
    "collect2.exe: error: ld returned 1 exit status",
    "c:/x/bin/ld.exe: CMakeFiles/t.dir/a.c.obj: in function `system_init':",
    "[12/68] Building C object app.c.obj",
    "-- defconfig: D:/x/defconfig.a.RTL87x2L.default",
])
def test_not_a_diagnostic(line):
    assert build.diagnostic(line) is None


def test_configure_argv(tree):
    t = build.scan(str(tree), "RTL87x2J")[0]
    argv, cwd, want = build.configure_argv("realtek", t["src"], t["path"], t["build_dir"], r"C:\gcc\bin", "cmake")
    assert cwd == t["src"] and argv[1:3] == ["-G", "MinGW Makefiles"]
    assert "kconfig_path=./sample/ppt/simple/proj_ptx/rtl87x2j/defconfig.ppt_simple_ptx.RTL87x2J.bank0" in argv
    assert "COMPILER_DIR=C:/gcc/bin" in argv
    lib = next(t for t in build.scan(str(tree), "RTL87x2L") if t["lib"])
    argv, cwd, want = build.configure_argv("humac", lib["src"], lib["path"], lib["build_dir"], "", "cmake")
    assert argv[argv.index("-G") + 1] == "Ninja" and "-DPPT_LIB_INSTALL=ON" in argv
    assert want["PPT_LIB_INSTALL"] == "ON" and cwd == lib["src"]


def test_run_builds_configures_once_and_records(tree, fake_cmake):
    t = build.scan(str(tree), "RTL87x2J")[0]
    lines = []
    code, note = build.run("build", t["kind"], t["src"], t["path"], t["build_dir"], gcc="C:/gcc", out=lines.append)
    assert code == 0, (note, lines)
    assert "1 warning" in note and "ppt_simple_ptx.bin 2 KB" in note
    res = build.last_result(t["build_dir"])
    assert res["state"] == "ok" and res["warnings"] == 1 and res["errors"] == 0
    assert res["main_bin"]["name"] == "ppt_simple_ptx.bin"
    assert res["diags"][0]["file"] == r"D:\ws\src\main.c" and res["diags"][0]["line"] == 7
    assert os.path.isfile(os.path.join(t["build_dir"], build.LOG))
    build.run("build", t["kind"], t["src"], t["path"], t["build_dir"], gcc="C:/gcc", out=lines.append)
    assert open(os.path.join(t["build_dir"], "configured.txt")).read() == "x"     # cache matches: no configure
    build.run("build", t["kind"], t["src"], t["path"], t["build_dir"], gcc="C:/gcc", reconfigure=True, out=lines.append)
    assert open(os.path.join(t["build_dir"], "configured.txt")).read() == "xx"


def test_run_failure_note_names_the_first_error(tree, fake_cmake, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "1")
    t = build.scan(str(tree), "RTL87x2J")[1]
    code, note = build.run("build", t["kind"], t["src"], t["path"], t["build_dir"], gcc="C:/gcc", out=lambda _: None)
    assert code == 1
    assert note.startswith("build 失败（1 error）：main.c:12 'x' undeclared")
    res = build.last_result(t["build_dir"])
    assert res["state"] == "failed" and res["stage"] == "build" and res["errors"] == 1


def test_run_flash(tree, fake_cmake):
    t = next(t for t in build.scan(str(tree), "RTL87x2L") if t["flash"])
    lines = []
    code, note = build.run("flash", t["kind"], t["src"], t["path"], t["build_dir"], out=lines.append)
    assert code == 0, note
    assert "已烧录" in note and "flashing ppt_sync_master_flash" in lines
    open(os.path.join(t["build_dir"], "build.ninja"), "w").write("")    # no JLink.exe at configure
    code, note = build.run("flash", t["kind"], t["src"], t["path"], t["build_dir"], out=lambda _: None)
    assert code == 1 and "ppt_sync_master_flash" in note


# --- the J-Link lock ----------------------------------------------------------------

def wait_job(job, timeout=30):
    end = time.time() + timeout
    while job.state in ("queued", "running") and time.time() < end:
        time.sleep(0.05)


def test_jlink_lock_is_machine_wide(tmp_path):
    jobs = Jobs()
    slow = [sys.executable, "-c", "import time; time.sleep(1.5)"]
    a = jobs.start("flash a", "wsA", [Step("a", slow, str(tmp_path))], locks=("jlink",))
    with pytest.raises(Busy):
        jobs.start("flash b", "wsB", [Step("b", slow, str(tmp_path))], locks=("jlink",))
    b = jobs.start("build b", "wsB", [Step("b", [sys.executable, "-c", "pass"], str(tmp_path))])   # no lock: runs
    wait_job(a)
    wait_job(b)
    c = jobs.start("flash c", "wsB", [Step("c", [sys.executable, "-c", "pass"], str(tmp_path))], locks=("jlink",))
    wait_job(c)
    assert (a.state, b.state, c.state) == ("ok", "ok", "ok")


# --- the Api ------------------------------------------------------------------------

def test_api_build_calls(api_env, fake_cmake):
    api, root, single = api_env
    touch(single / "realtek/proj/rtl87x2j/defconfig.ppt_demo.RTL87x2J.bank0")
    touch(single / "humac/x/rtl87x2l/defconfig.ppt_tx.RTL87x2L.default")
    ws = next(w for w in api.workspaces()["workspaces"] if w["kind"] == "single")
    r = api.build_targets(ws["id"])
    assert r["ic"] is None and r["targets"] == []                       # "single": no IC from the name
    r = api.set_build_ic(ws["id"], "rtl87x2j")
    assert r["ic"] == "RTL87x2J" and [t["app"] for t in r["targets"]] == ["ppt_demo"]
    tid = r["targets"][0]["id"]
    assert api.build_fav(ws["id"], tid)["fav"] is True
    assert api.build_targets(ws["id"])["targets"][0]["fav"] is True
    from apiary.ui.api import ApiError
    with pytest.raises(ApiError):
        api.build(ws["id"], ["nope"])
    with pytest.raises(ApiError):
        api.build(ws["id"], [tid], action="flash")                      # x2j: no J-Link flash
    api.settings.gcc_dir = "C:/gcc"
    out = api.build(ws["id"], [tid])
    job = api.jobs.get(out["job"]["id"])
    wait_job(job, 60)
    assert job.state == "ok", [(s.label, s.note) for s in job.steps]
    assert "ppt_demo.bin" in job.steps[0].note
    last = api.build_targets(ws["id"])["targets"][0]["last"]
    assert last["state"] == "ok" and last["main_bin"]["name"] == "ppt_demo.bin"
    assert api.build_result(ws["id"], tid)["result"]["warnings"] == 1
    ask = api.build_clean(ws["id"], [tid])
    assert ask["need_confirm"]
    assert len(api.build_clean(ws["id"], [tid], confirm=True)["removed"]) == 1
    assert not os.path.exists(r["targets"][0]["build_dir"])
    with pytest.raises(ApiError):
        api.goto(ws["id"], str(root.parent / "outside.c"), 3)
    with pytest.raises(ApiError):
        api.reveal(ws["id"], "C:\\Windows")
    x2l = api.set_build_ic(ws["id"], "RTL87x2L")["targets"]
    assert [t["app"] for t in x2l] == ["ppt_tx"] and x2l[0]["flash"]


def test_settings_build_fields(apiary_home):
    from apiary import config
    s = config.Settings()
    config.update(s, {"gcc_dir": " C:/gcc ", "build_ic": {"D:\\a": "RTL87x2L"}, "build_fav": ["a|b", " "]})
    config.save(s)
    s2 = config.load()
    assert s2.gcc_dir == "C:/gcc" and s2.build_ic == {"D:\\a": "RTL87x2L"} and s2.build_fav == ["a|b"]
    config.update(s2, {"build_ic": "not a dict"})
    assert s2.build_ic == {"D:\\a": "RTL87x2L"}
    json.loads((apiary_home / "settings.json").read_text(encoding="utf-8"))
