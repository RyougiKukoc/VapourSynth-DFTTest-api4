#!/usr/bin/env python3
"""Smoke test a wheel-installed DFTTest payload through VapourSynth autoload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from smoke_load_artifact import frame_hash, make_source, plane_stats, plugin_suffix


PLUGIN_NAME = "dfttest"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Smoke test installed vapoursynth-dfttest wheel autoload.")
    parser.add_argument("--site-dir", help="Optional site-packages directory to prepend.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.site_dir:
        sys.path.insert(0, args.site_dir)

    import vapoursynth as vs

    package_dir = Path(vs.__file__).resolve().parent / "plugins" / PLUGIN_NAME
    plugin = package_dir / f"{PLUGIN_NAME}{plugin_suffix()}"
    manifest = package_dir / "manifest.vs"
    if not plugin.is_file() or not manifest.is_file():
        raise FileNotFoundError(f"installed wheel payload missing {plugin.name} or manifest.vs: {package_dir}")

    core = vs.core
    if not hasattr(core, PLUGIN_NAME) or not hasattr(core.dfttest, "DFTTest"):
        raise RuntimeError("core.dfttest.DFTTest was not autoloaded from the installed wheel")

    output = core.dfttest.DFTTest(make_source(core, vs))
    rendered = {number: output.get_frame(number) for number in (0, 3, 11)}
    frame = rendered[3]
    result = {
        "vapoursynth_module": vs.__file__,
        "plugin": str(plugin),
        "manifest": str(manifest),
        "width": frame.width,
        "height": frame.height,
        "format": frame.format.name,
        "frames": output.num_frames,
        "frame_hashes": {number: frame_hash(value) for number, value in rendered.items()},
        "plane_stats": plane_stats(core, output, 3),
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for key, value in result.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
