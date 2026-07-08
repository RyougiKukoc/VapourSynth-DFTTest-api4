from __future__ import annotations

import argparse
import os
import site
import sys
import sysconfig
from pathlib import Path


PLUGIN_NAME = "dfttest"


def add_existing_dll_dirs(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            os.add_dll_directory(str(path))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test an installed vapoursynth-dfttest wheel.")
    parser.add_argument("--exercise-filter", action="store_true", help="Create a DFTTest node and request one frame.")
    args = parser.parse_args(argv)

    try:
        import vapoursynth as vs
    except ImportError as exc:
        print(f"failed to import VapourSynth Python module: {exc}", file=sys.stderr)
        return 1

    vs_pkg = Path(vs.__file__).resolve().parent
    plugin_dir = vs_pkg / "plugins" / PLUGIN_NAME
    required = [
        plugin_dir / f"{PLUGIN_NAME}.dll",
        plugin_dir / "manifest.vs",
    ]
    for path in required:
        if not path.exists():
            print(f"missing installed file: {path}", file=sys.stderr)
            return 1

    add_existing_dll_dirs(
        [
            plugin_dir,
            vs_pkg,
            Path(sys.executable).resolve().parent,
            Path(sysconfig.get_paths().get("platlib", "")),
            Path(sysconfig.get_paths().get("purelib", "")),
            *(Path(p) for p in site.getsitepackages()),
        ]
    )

    try:
        env = vs.create_environment()
        core = env.get_core()
    except AttributeError:
        core = vs.core

    if not hasattr(core, "dfttest") or not hasattr(core.dfttest, "DFTTest"):
        print("core.dfttest.DFTTest missing after installed-wheel autoload", file=sys.stderr)
        return 1
    print(core.dfttest.DFTTest)

    if args.exercise_filter:
        try:
            clip = core.std.BlankClip(format=vs.YUV420P8, width=64, height=32, length=5, color=[96, 128, 128])
            filtered = core.dfttest.DFTTest(clip)
            frame = filtered.get_frame(2)
            stats = core.std.PlaneStats(filtered).get_frame(2).props
        except Exception as exc:
            print(f"filter exercise failed: {exc}", file=sys.stderr)
            return 1

        if filtered.width != 64 or filtered.height != 32 or frame.width != 64 or frame.height != 32:
            print(
                f"unexpected filter output size: node={filtered.width}x{filtered.height}, frame={frame.width}x{frame.height}",
                file=sys.stderr,
            )
            return 1
        print(f"filter exercise: {frame.width}x{frame.height}")
        print(f"PlaneStatsMin={stats['PlaneStatsMin']}")
        print(f"PlaneStatsMax={stats['PlaneStatsMax']}")
        print(f"PlaneStatsAverage={stats['PlaneStatsAverage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
