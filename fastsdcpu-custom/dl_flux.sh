#!/usr/bin/env bash
# Download the FLUX.1-schnell GGUF stack (all commercially licensable) into FastSD.
#   diffusion + clip + t5xxl : rupeshs/FastSD-Flux-GGUF   (Apache-2.0 / MIT)
#   vae (ae.safetensors)     : tripathiarpan20/FLUX.1-schnell — a NON-GATED mirror,
#     byte-identical (sha256) to the gated black-forest-labs schnell ae (Apache-2.0).
set -u
FS=/home/kanishka/kk_works/fastsdcpu
G="$FS/models/gguf"
BASE=https://huggingface.co/rupeshs/FastSD-Flux-GGUF/resolve/main
mkdir -p "$G/diffusion" "$G/clip" "$G/t5xxl" "$G/vae"

dl () { curl -fL -C - --retry 5 --retry-delay 5 -o "$2" "$1"; }

# Prebuilt .so — if it fails to load with a GLIBCXX error, run build_sdcpp.sh instead.
dl "$BASE/libstable-diffusion.so"        "$FS/libstable-diffusion.so"
dl "$BASE/clip_l_q4_0.gguf"              "$G/clip/clip_l_q4_0.gguf"
dl "$BASE/t5xxl_q4_0.gguf"               "$G/t5xxl/t5xxl_q4_0.gguf"
dl "$BASE/flux1-schnell-q4_0.gguf"       "$G/diffusion/flux1-schnell-q4_0.gguf"
dl "https://huggingface.co/tripathiarpan20/FLUX.1-schnell/resolve/main/ae.safetensors" "$G/vae/ae.safetensors"

echo "FLUX GGUF stack downloaded to $G"
echo "Expected sha256 of the VAE: afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38"
sha256sum "$G/vae/ae.safetensors"
