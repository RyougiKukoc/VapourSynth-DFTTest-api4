#!/usr/bin/env bash
# Build a self-contained DFTTest Linux payload in the manylinux2014 container.
set -exo pipefail

PYTHON_BIN="${PYTHON_BIN:-/opt/python/cp313-cp313/bin/python}"

# The manylinux2014 image's default compiler is too old for the C++17 build.
# Source this before enabling nounset because the vendor script reads MANPATH.
source /opt/rh/devtoolset-10/enable
set -u

export PATH="$(dirname "$PYTHON_BIN"):$PATH"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install build hatchling packaging meson ninja

rm -rf _deps/vapoursynth-wheel dist/wheels-linux dist/linux-x86_64 dist/release-assets-linux _build/fftw
mkdir -p _deps/vapoursynth-wheel dist/wheels-linux dist/linux-x86_64 _build

"$PYTHON_BIN" -m pip download --only-binary=:all: --no-deps \
  --platform manylinux_2_27_x86_64 --implementation cp --python-version 313 --abi abi3 \
  VapourSynth==79 -d _deps/vapoursynth-wheel
unzip -q _deps/vapoursynth-wheel/*.whl -d _deps/vapoursynth-wheel/extracted

# Static float + threads FFTW removes FFTW from the shipped module's runtime
# closure. -fPIC is mandatory because these archives are linked into a .so.
curl --fail --location --retry 3 --output _build/fftw.tar.gz https://www.fftw.org/fftw-3.3.10.tar.gz
tar -xzf _build/fftw.tar.gz -C _build
pushd _build/fftw-3.3.10
CFLAGS='-O3 -fPIC' ./configure --prefix=/opt/dfttest-fftw --enable-float --enable-threads --enable-static --disable-shared
make -j"$(nproc)"
make install
popd

# FFTW installs fftw3f.pc but omits metadata for its separately built threads
# archive. Meson must see the full static link closure under this private
# prefix; ordinary distro packages continue to use their own discovery path.
cat > /opt/dfttest-fftw/lib/pkgconfig/fftw3f_threads.pc <<'EOF'
prefix=/opt/dfttest-fftw
libdir=${prefix}/lib
includedir=${prefix}/include

Name: fftw3f_threads
Description: FFTW single-precision threads library
Version: 3.3.10
Libs: -L${libdir} -lfftw3f_threads -lfftw3f -lm -lpthread
Cflags: -I${includedir}
EOF

export DFTTEST_VAPOURSYNTH_ROOT="/workspace/_deps/vapoursynth-wheel/extracted/vapoursynth"
export PKG_CONFIG_PATH="${DFTTEST_VAPOURSYNTH_ROOT}/pkgconfig:/opt/dfttest-fftw/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export DFTTEST_FORCE_BUILD=1
export DFTTEST_PLATFORM_TAG=manylinux_2_27_x86_64
"$PYTHON_BIN" -m build --wheel --no-isolation --skip-dependency-check --outdir dist/wheels-linux

wheel=$(find dist/wheels-linux -name '*.whl' -print -quit)
test -n "$wheel"
unzip -q "$wheel" 'vapoursynth/plugins/dfttest/*' -d dist/linux-x86_64/extracted
mv dist/linux-x86_64/extracted/vapoursynth/plugins/dfttest dist/linux-x86_64/dfttest
"$PYTHON_BIN" tools/ci_make_release_assets.py --clean \
  --package-dir dist/linux-x86_64/dfttest \
  --wheel-dir dist/wheels-linux \
  --out-dir dist/release-assets-linux \
  --zip-name dfttest-linux-x86_64.zip

readelf --version-info dist/linux-x86_64/dfttest/dfttest.so
ldd dist/linux-x86_64/dfttest/dfttest.so
"$PYTHON_BIN" -c '
import re
import subprocess
output = subprocess.check_output(["readelf", "--version-info", "dist/linux-x86_64/dfttest/dfttest.so"], text=True)
versions = [tuple(map(int, item.split("."))) for item in re.findall(r"GLIBC_(\d+\.\d+)", output)]
assert all(version <= (2, 17) for version in versions), versions
dependencies = subprocess.check_output(["ldd", "dist/linux-x86_64/dfttest/dfttest.so"], text=True)
assert "fftw" not in dependencies.lower(), dependencies
'
