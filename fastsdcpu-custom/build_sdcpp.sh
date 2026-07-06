#!/usr/bin/env bash
# Build libstable-diffusion.so for FastSD's GGUF/FLUX mode, matching the ABI that
# FastSD's ctypes wrapper (src/backend/gguf/gguf_diffusion.py) expects — hence the
# PINNED commit. The author's prebuilt .so needs GLIBCXX_3.4.32 (GCC 13); this box
# has GCC 11 (libstdc++ maxes at 3.4.30), so we build from source instead. cmake is
# installed via `uv pip` (no sudo; the env has no pip).
set -e
PIN=14206fd48832ab600d9db75f15acb5062ae2c296
FS=/home/kanishka/kk_works/fastsdcpu
FSPY="$FS/env/bin"
UV="${UV:-$(command -v uv || echo /home/kanishka/.local/bin/uv)}"
BUILD="${BUILD:-/home/kanishka/kk_works/build}"

"$UV" pip install --python "$FSPY/python" -q cmake
export PATH="$FSPY:$PATH"                     # cmake + ninja now on PATH
mkdir -p "$BUILD"; cd "$BUILD"
[ -d stable-diffusion.cpp/.git ] || git clone https://github.com/leejet/stable-diffusion.cpp
cd stable-diffusion.cpp
git fetch --all --quiet || true
git checkout "$PIN"
git submodule update --init --recursive
cmake . -DSD_BUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=Release -DSD_BUILD_EXAMPLES=OFF
cmake --build . --config Release -j"$(nproc)"
SO="$(find . -name 'libstable-diffusion.so' | head -1)"
cp -f "$FS/libstable-diffusion.so" "$FS/libstable-diffusion.so.bak" 2>/dev/null || true
cp -f "$SO" "$FS/libstable-diffusion.so"
echo "Installed $SO -> $FS/libstable-diffusion.so"
strings "$FS/libstable-diffusion.so" | grep -E '^GLIBCXX_3\.4\.[0-9]+$' | sort -V | tail -1
