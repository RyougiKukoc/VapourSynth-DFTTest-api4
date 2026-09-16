from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from packaging import tags


ROOT = Path(__file__).resolve().parent
PLUGIN_NAME = "dfttest"
DEFAULT_REPOSITORY = "RyougiKukoc/VapourSynth-DFTTest-api4"
WINDOWS_PREBUILT_ASSET = "dfttest-msys2-ucrt64.zip"
LINUX_PREBUILT_ASSET = "dfttest-linux-x86_64.zip"


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() not in {"", "0", "false", "no", "off"})


def _project_version() -> str:
    override = os.environ.get("DFTTEST_PREBUILT_VERSION")
    if override:
        return override
    with (ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("project.version is missing from pyproject.toml")
    return version


def _platform_payload() -> tuple[str, str] | None:
    machine = platform.machine().lower()
    if machine not in {"amd64", "x86_64"}:
        return None
    if sys.platform == "win32":
        return WINDOWS_PREBUILT_ASSET, ".dll"
    if sys.platform == "linux":
        return LINUX_PREBUILT_ASSET, ".so"
    return None


def _plugin_suffix() -> str:
    if sys.platform == "win32":
        return ".dll"
    if sys.platform == "darwin":
        return ".dylib"
    return ".so"


def _default_prebuilt_url(version: str) -> str:
    repository = os.environ.get("DFTTEST_PREBUILT_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPOSITORY
    tag = os.environ.get("DFTTEST_PREBUILT_TAG") or f"v{version}"
    payload = _platform_payload()
    if payload is None:
        raise RuntimeError(f"no DFTTest release payload is published for {sys.platform}/{platform.machine()}")
    asset = os.environ.get("DFTTEST_PREBUILT_ASSET_NAME") or payload[0]
    return f"https://github.com/{repository}/releases/download/{tag}/{asset}"


def _prebuilt_source(version: str) -> tuple[str, bool]:
    explicit = os.environ.get("DFTTEST_PREBUILT_URL")
    if explicit:
        return explicit, True
    return _default_prebuilt_url(version), False


def _fetch_prebuilt_archive(source: str, destination: Path) -> None:
    candidate = Path(source)
    if candidate.exists():
        shutil.copy2(candidate, destination)
        return

    request = urllib.request.Request(source, headers={"User-Agent": "vapoursynth-dfttest-build-hook"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _write_manifest(target_dir: Path) -> None:
    (target_dir / "manifest.vs").write_text(
        "[VapourSynth Manifest V1]\n"
        f"{PLUGIN_NAME}\n",
        encoding="ascii",
        newline="\n",
    )


def _stage_package_from_zip(archive_path: Path, target_dir: Path, suffix: str) -> None:
    with zipfile.ZipFile(archive_path) as zf:
        package_members = [
            name
            for name in zf.namelist()
            if name.replace("\\", "/").startswith(f"{PLUGIN_NAME}/") and not name.endswith("/")
        ]
        if not package_members:
            raise FileNotFoundError(f"prebuilt archive does not contain a top-level {PLUGIN_NAME}/ package directory")

        for member in package_members:
            normalized = member.replace("\\", "/")
            relative = normalized.split("/", 1)[1]
            out_path = target_dir / relative
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    plugin = target_dir / f"{PLUGIN_NAME}{suffix}"
    if not plugin.exists():
        raise FileNotFoundError(f"prebuilt archive did not provide {plugin.name}")
    if not (target_dir / "manifest.vs").exists():
        _write_manifest(target_dir)


def _stage_prebuilt_plugin(version: str, target_dir: Path) -> bool:
    if _truthy(os.environ.get("DFTTEST_FORCE_BUILD")):
        print("DFTTest wheel build: skipping prebuilt asset because DFTTEST_FORCE_BUILD is set")
        return False

    payload = _platform_payload()
    if payload is None:
        print("DFTTest wheel build: no matching platform release payload; falling back to a local native build")
        return False

    source, explicit = _prebuilt_source(version)
    try:
        with tempfile.TemporaryDirectory(prefix="dfttest-prebuilt-") as temp_dir_text:
            archive_path = Path(temp_dir_text) / (Path(source).name or payload[0])
            _fetch_prebuilt_archive(source, archive_path)
            _stage_package_from_zip(archive_path, target_dir, payload[1])
    except Exception as exc:
        if explicit:
            raise RuntimeError(f"failed to use explicit DFTTest prebuilt asset {source!r}") from exc
        print(f"DFTTest wheel build: prebuilt asset unavailable at {source}; falling back to local build ({exc})")
        return False

    print(f"DFTTest wheel build: using prebuilt release asset {source}")
    return True


def _run(cmd: list[str], *, env: dict[str, str]) -> None:
    print("+ " + subprocess.list2cmdline(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True, env=env)


def _prepend_path(env: dict[str, str], variable: str, entries: list[Path]) -> None:
    parts = [str(entry) for entry in entries if entry.is_dir()]
    if not parts:
        return
    existing = env.get(variable)
    env[variable] = os.pathsep.join(parts + ([existing] if existing else []))


def _configure_windows_build_env(env: dict[str, str]) -> dict[str, str]:
    msystem_prefix = env.get("MSYSTEM_PREFIX")
    if msystem_prefix:
        prefix = Path(msystem_prefix)
        entries = [prefix / "bin", prefix.parent / "usr" / "bin"]
    else:
        entries = [Path(r"C:\msys64\ucrt64\bin"), Path(r"C:\msys64\usr\bin")]
    _prepend_path(env, "PATH", entries)
    env.setdefault("CC", "gcc")
    env.setdefault("CXX", "g++")
    return env


def _vapoursynth_pkgconfig_dirs(env: dict[str, str]) -> list[Path]:
    override = env.get("DFTTEST_VAPOURSYNTH_ROOT")
    if override:
        root = Path(override)
        candidates = [root / "pkgconfig", root / "vapoursynth" / "pkgconfig"]
    else:
        try:
            import vapoursynth
        except ImportError as exc:
            raise RuntimeError(
                "DFTTest local build requires VapourSynth>=79 in the isolated build environment or "
                "DFTTEST_VAPOURSYNTH_ROOT pointing to an extracted VapourSynth package"
            ) from exc
        candidates = [Path(vapoursynth.__file__).resolve().parent / "pkgconfig"]
    return [candidate for candidate in candidates if (candidate / "vapoursynth.pc").exists()]


def _configure_posix_build_env(env: dict[str, str]) -> dict[str, str]:
    pkgconfig_dirs = _vapoursynth_pkgconfig_dirs(env)
    if not pkgconfig_dirs:
        raise RuntimeError("could not locate vapoursynth/pkgconfig/vapoursynth.pc for the DFTTest local build")
    # The SDK location takes precedence without discarding a caller's unrelated
    # pkg-config search paths.
    _prepend_path(env, "PKG_CONFIG_PATH", pkgconfig_dirs)
    return env


def _meson_command() -> list[str]:
    meson = shutil.which("meson")
    if meson:
        return [meson]
    for module_name in ("mesonbuild", "mesonbuild.mesonmain"):
        candidate = [sys.executable, "-m", module_name]
        if subprocess.run(candidate + ["--version"], cwd=ROOT, capture_output=True).returncode == 0:
            return candidate
    raise FileNotFoundError("Meson is unavailable; install the project build requirements")


def _find_built_plugin(build_dir: Path) -> Path:
    suffix = _plugin_suffix()
    for stem in (PLUGIN_NAME, f"lib{PLUGIN_NAME}"):
        candidate = build_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"missing built plugin under {build_dir}")


def _stage_local_build(target_dir: Path) -> None:
    if sys.platform == "win32":
        env = _configure_windows_build_env(os.environ.copy())
        build_dir = ROOT / "build-wheel-msys2"
        _run([sys.executable, "tools/ci_prepare_msys2.py"], env=env)
        _run(
            [
                sys.executable,
                "tools/ci_build_msys2.py",
                "--clean",
                "--build-dir",
                str(build_dir),
                "--dist-dir",
                str(target_dir.parent),
            ],
            env=env,
        )
        return

    env = _configure_posix_build_env(os.environ.copy())
    build_dir = ROOT / "build-wheel"
    meson = _meson_command()
    _run(meson + ["setup", str(build_dir), "--wipe"], env=env)
    _run(meson + ["compile", "-C", str(build_dir)], env=env)
    shutil.copy2(_find_built_plugin(build_dir), target_dir / f"{PLUGIN_NAME}{_plugin_suffix()}")
    _write_manifest(target_dir)
    if (ROOT / "LICENSE").exists():
        shutil.copy2(ROOT / "LICENSE", target_dir / "LICENSE")


def _wheel_platform_tag(used_prebuilt: bool) -> str:
    override = os.environ.get("DFTTEST_PLATFORM_TAG")
    if override:
        return override
    if used_prebuilt and sys.platform == "linux":
        return "manylinux_2_27_x86_64"
    return str(next(tags.platform_tags()))


class CustomHook(BuildHookInterface[Any]):
    build_dir = ROOT / "build-wheel"
    dist_dir = ROOT / "vapoursynth" / "plugins" / PLUGIN_NAME

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        build_data["pure_python"] = False
        project_version = _project_version()

        shutil.rmtree(self.build_dir, ignore_errors=True)
        shutil.rmtree(self.dist_dir.parent.parent, ignore_errors=True)
        self.dist_dir.mkdir(parents=True, exist_ok=True)

        used_prebuilt = _stage_prebuilt_plugin(project_version, self.dist_dir)
        if not used_prebuilt:
            _stage_local_build(self.dist_dir)
        build_data["tag"] = f"py3-none-{_wheel_platform_tag(used_prebuilt)}"

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        del version, build_data, artifact_path
        shutil.rmtree(self.build_dir, ignore_errors=True)
        shutil.rmtree(self.dist_dir.parent.parent, ignore_errors=True)
