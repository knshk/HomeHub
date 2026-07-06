# FastSD CPU — custom files & setup

These are the **custom bits layered onto [rupeshs/fastsdcpu](https://github.com/rupeshs/fastsdcpu)**
(which itself is not vendored here — clone it fresh). They live outside the HomeHub
repo on the box (under `/home/kanishka/kk_works/fastsdcpu/`) and are copied here so a
disk failure doesn't lose them. Restore by copying each file back to its place.

## Files

| File here | Restore to | What it is |
|---|---|---|
| `imgop_worker.py` | `fastsdcpu/imgop_worker.py` | Hub image ops run as a subprocess in the FastSD env: img2img (OpenVINO sd-turbo), remove-bg (rembg), upscale (Real-ESRGAN). Called by `home-hub/app/image_ops.py`. |
| `settings.yaml` | `fastsdcpu/configs/settings.yaml` | Default Studio config: **GGUF/FLUX mode**, 4 steps, guidance 1.0, all 4 GGUF file paths pre-selected. |
| `openvino-lcm-models.txt` | `fastsdcpu/configs/openvino-lcm-models.txt` | The OpenVINO image-model list the hub Models page reads. |
| `build_sdcpp.sh` | run anywhere | Builds `libstable-diffusion.so` (GGUF/FLUX backend) from the pinned commit. |
| `dl_flux.sh` | run anywhere | Downloads the FLUX.1-schnell GGUF stack + VAE. |

## Manual patch (not a file here — one-line edit inside the venv)

Real-ESRGAN (upscale) needs `basicsr`, which imports a module removed from newer
torchvision. In:

    fastsdcpu/env/lib/python3.11/site-packages/basicsr/data/degradations.py

change:

    from torchvision.transforms.functional_tensor import rgb_to_grayscale
    # ->
    from torchvision.transforms.functional import rgb_to_grayscale

## Rebuild order (fresh box)

1. Clone fastsdcpu, create its `env/` and install requirements.
2. `bash dl_flux.sh` — pull the FLUX GGUF stack (~9.8 GB).
3. If the Studio errors on `GLIBCXX_3.4.32` loading the `.so`: `bash build_sdcpp.sh`.
4. Copy `imgop_worker.py`, `configs/settings.yaml`, `configs/openvino-lcm-models.txt` into place.
5. Apply the basicsr patch above (only needed for the Upscale op).

## Licensing (all commercial-safe for a paid app)

FLUX.1-**schnell** = Apache-2.0 (NOT the dev non-commercial license), its ae.safetensors
= Apache-2.0, T5-XXL = Apache-2.0, CLIP-L = MIT, stable-diffusion.cpp = MIT.
`sd-turbo` (the OpenVINO img2img model) is **non-commercial** — drafts only.
