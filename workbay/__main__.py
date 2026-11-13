"""python -m workbay ui | status"""
import argparse
import sys
import time


def _status(args):
    from .core import config
    from .ws import discover, gitstat
    settings = config.load()
    if args.root:
        settings.roots, settings.added = args.root, []
    t0 = time.perf_counter()
    wss = discover.scan(settings)
    sts = gitstat.statuses([r for ws in wss for r in ws.repos])
    for ws in wss:
        print(f"{ws.name}  [{ws.kind}]  {ws.path}" + (f"  !! {ws.error}" if ws.error else ""))
        for r in ws.repos:
            st = sts.get(r.id)
            if st is None:
                print(f"    {r.path:<40} (未 clone)")
                continue
            if st.error:
                print(f"    {r.path:<40} !! {st.error.splitlines()[0]}")
                continue
            flags = [st.branch or f"({st.head})"]
            if st.ahead is not None:
                flags.append(f"+{st.ahead}/-{st.behind}")
            if st.dirty:
                flags.append(f"dirty S{st.staged} M{st.modified} C{st.conflicted}")
            if st.untracked:
                flags.append(f"?{st.untracked}")
            if st.on_manifest is False:
                flags.append("off-manifest")
            print(f"    {r.path:<40} " + "  ".join(flags))
    print(f"-- {len(wss)} workspaces, {len(sts)} repos, {time.perf_counter() - t0:.1f} s")
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="workbay", description="Workbay")
    sub = p.add_subparsers(dest="cmd")
    ui = sub.add_parser("ui", help="the web UI (default)")
    ui.add_argument("--port", type=int, default=8780)
    ui.add_argument("--no-browser", action="store_true")
    st = sub.add_parser("status", help="print every workspace's state")
    st.add_argument("root", nargs="*", help="scan these roots instead of the settings'")
    args = p.parse_args(argv)
    if args.cmd == "status":
        return _status(args)
    from .ui.server import serve
    return serve(port=getattr(args, "port", 8780), open_browser=not getattr(args, "no_browser", False))


if __name__ == "__main__":
    sys.exit(main())
