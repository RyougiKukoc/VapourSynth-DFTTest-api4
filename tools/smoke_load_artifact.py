#!/usr/bin/env python3
"""Explicitly load one DFTTest package and render deterministic temporal frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "dfttest"


def plugin_suffix() -> str:
    if sys.platform == "win32":
        return ".dll"
    if sys.platform == "darwin":
        return ".dylib"
    return ".so"


def frame_hash(frame: Any) -> str:
    digest = hashlib.sha256()
    for plane in range(frame.format.num_planes):
        digest.update(bytes(frame[plane]))
    return digest.hexdigest()


class IsolatedEnvironmentPolicy:
    """Create one R79 environment with autoload disabled before plugin loading."""

    def __init__(self, flags: int) -> None:
        self._api: Any = None
        self._environment: Any = None
        self._flags = flags

    def on_policy_registered(self, api: Any) -> None:
        self._api = api
        self._environment = api.create_environment(self._flags)

    def on_policy_cleared(self) -> None:
        self._api = None
        self._environment = None

    def get_current_environment(self) -> Any:
        return self._environment

    def set_environment(self, environment: Any) -> Any:
        previous = self._environment
        if environment is not None:
            self._environment = environment
        return previous

    def is_alive(self, environment: Any) -> bool:
        return environment is self._environment

    def close(self) -> None:
        if self._api is not None and self._environment is not None:
            self._api.destroy_environment(self._environment)
            self._environment = None


def install_isolated_policy(vs_module: Any) -> IsolatedEnvironmentPolicy | None:
    if not hasattr(vs_module, "register_policy") or vs_module.has_policy():
        return None
    policy = IsolatedEnvironmentPolicy(int(vs_module.DISABLE_AUTO_LOADING))
    vs_module.register_policy(policy)
    return policy


def add_vapoursynth_root(root_text: str | None) -> None:
    if not root_text:
        return
    root = Path(root_text).resolve()
    candidates = [root, root / "Lib" / "site-packages"]
    for candidate in reversed(candidates):
        if (candidate / "vapoursynth" / "__init__.py").exists():
            sys.path.insert(0, str(candidate))
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is not None:
        for candidate in (root, root / "vapoursynth"):
            if candidate.is_dir():
                add_dll_directory(str(candidate))


def resolve_artifact(artifact_dir_arg: str | None, artifact_zip_arg: str | None) -> tuple[Path, Path | None]:
    if artifact_zip_arg:
        archive = (ROOT / artifact_zip_arg).resolve()
        if not archive.is_file():
            raise FileNotFoundError(archive)
        temporary = Path(tempfile.mkdtemp(prefix="dfttest-package-"))
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(temporary)
        directories = [path for path in temporary.iterdir() if path.is_dir()]
        if len(directories) != 1:
            raise RuntimeError(f"expected one top-level plugin directory in {archive}, found {len(directories)}")
        return directories[0], temporary

    root = (ROOT / (artifact_dir_arg or "dist/msys2-ucrt64/dfttest")).resolve()
    candidates = [root, root / PLUGIN_NAME, root / "vapoursynth" / "plugins" / PLUGIN_NAME]
    for candidate in candidates:
        if (candidate / f"{PLUGIN_NAME}{plugin_suffix()}").is_file():
            return candidate, None
    raise FileNotFoundError(root / PLUGIN_NAME / f"{PLUGIN_NAME}{plugin_suffix()}")


def make_source(core: Any, vs_module: Any) -> Any:
    frames = []
    for number in range(12):
        inner = core.std.BlankClip(
            width=48,
            height=32,
            format=vs_module.YUV420P8,
            length=1,
            color=[48 + number * 8, 96 + number * 3, 160 - number * 2],
        )
        frames.append(core.std.AddBorders(inner, left=8, right=8, top=8, bottom=8, color=[16, 128, 128]))
    return core.std.Splice(frames)


def plane_stats(core: Any, clip: Any, frame_number: int) -> list[dict[str, float]]:
    values = []
    for plane in range(clip.format.num_planes):
        props = core.std.PlaneStats(clip, plane=plane).get_frame(frame_number).props
        values.append(
            {
                "plane": plane,
                "minimum": float(props["PlaneStatsMin"]),
                "maximum": float(props["PlaneStatsMax"]),
                "average": float(props["PlaneStatsAverage"]),
            }
        )
    return values


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Explicitly smoke-load a DFTTest plugin package.")
    parser.add_argument("--artifact-dir", help="Plugin package directory or directory containing it.")
    parser.add_argument("--artifact-zip", help="Release zip with one top-level dfttest directory.")
    parser.add_argument("--vapoursynth-root", help="Extracted Windows VapourSynth wheel root.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    package_dir, temporary = resolve_artifact(args.artifact_dir, args.artifact_zip)
    plugin = package_dir / f"{PLUGIN_NAME}{plugin_suffix()}"
    manifest = package_dir / "manifest.vs"
    if not plugin.is_file() or not manifest.is_file():
        raise FileNotFoundError(f"package must contain {plugin.name} and manifest.vs: {package_dir}")

    add_vapoursynth_root(args.vapoursynth_root)
    add_dll_directory = getattr(os, "add_dll_directory", None)
    handles = [add_dll_directory(str(package_dir))] if add_dll_directory is not None else []
    try:
        import vapoursynth as vs

        policy = install_isolated_policy(vs)
        core = vs.core
        core.std.LoadPlugin(str(plugin))
        if not hasattr(core, PLUGIN_NAME) or not hasattr(core.dfttest, "DFTTest"):
            raise RuntimeError("core.dfttest.DFTTest was not registered by the explicitly loaded plugin")

        output = core.dfttest.DFTTest(make_source(core, vs))
        rendered = {number: output.get_frame(number) for number in (0, 3, 11)}
        hashes = {number: frame_hash(frame) for number, frame in rendered.items()}
        invalid_error = ""
        try:
            core.dfttest.DFTTest(make_source(core, vs), ftype=5)
        except vs.Error as exc:
            invalid_error = str(exc)
        if "ftype must be 0, 1, 2, 3, or 4" not in invalid_error:
            raise RuntimeError(f"DFTTest did not reject ftype=5 as documented: {invalid_error!r}")

        frame = rendered[3]
        result = {
            "plugin": str(plugin),
            "manifest": str(manifest),
            "width": frame.width,
            "height": frame.height,
            "format": frame.format.name,
            "frames": output.num_frames,
            "frame_hashes": hashes,
            "plane_stats": plane_stats(core, output, 3),
            "invalid_ftype_error": invalid_error,
        }
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            for key, value in result.items():
                print(f"{key}={value}")
        return 0
    finally:
        for handle in handles:
            handle.close()
        if "policy" in locals() and policy is not None:
            policy.close()
        if temporary is not None:
            import shutil

            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
