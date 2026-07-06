"""Image-processing worker — run inside the FastSD venv (has torch/optimum/PIL/
rembg/realesrgan). Invoked by the Home Hub as a subprocess so the hub itself
stays light. One op per call; writes the output PNG to --out.

Ops: img2img (prompt-guided variation), rembg (remove background),
     upscale (Real-ESRGAN if available, else high-quality Lanczos).
"""
import argparse
import sys

from PIL import Image

MODEL = "rupeshs/sd-turbo-openvino"


def img2img(src, out, prompt, strength=0.6, steps=3):
    from optimum.intel import OVStableDiffusionImg2ImgPipeline
    pipe = OVStableDiffusionImg2ImgPipeline.from_pretrained(MODEL)
    init = Image.open(src).convert("RGB").resize((512, 512))
    img = pipe(
        prompt=prompt or "high quality, detailed",
        image=init, num_inference_steps=max(2, steps),
        guidance_scale=1.5, strength=max(0.2, min(0.9, strength)),
    ).images[0]
    img.save(out)


def rembg_op(src, out):
    from rembg import remove
    img = Image.open(src).convert("RGBA")
    remove(img).save(out)  # transparent PNG


def upscale(src, out, scale=2):
    img = Image.open(src).convert("RGB")
    scale = 4 if int(scale) >= 4 else 2
    try:
        import numpy as np
        from realesrgan import RealESRGANer
        from basicsr.archs.rrdbnet_arch import RRDBNet
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=4)
        url = ("https://github.com/xinntao/Real-ESRGAN/releases/download/"
               "v0.1.0/RealESRGAN_x4plus.pth")
        up = RealESRGANer(scale=4, model_path=url, model=model,
                          tile=256, tile_pad=10, pre_pad=0, half=False)
        res, _ = up.enhance(np.array(img), outscale=scale)
        Image.fromarray(res).save(out)
        return
    except Exception as e:  # basicsr/torchvision breakage or no model -> resample
        sys.stderr.write(f"realesrgan unavailable ({e.__class__.__name__}); using Lanczos\n")
        w, h = img.size
        img.resize((w * scale, h * scale), Image.LANCZOS).save(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--strength", type=float, default=0.6)
    a = ap.parse_args()
    if a.op == "img2img":
        img2img(a.src, a.out, a.prompt, a.strength)
    elif a.op == "rembg":
        rembg_op(a.src, a.out)
    elif a.op == "upscale":
        upscale(a.src, a.out, a.scale)
    else:
        sys.exit(f"unknown op {a.op}")
    print("DONE", a.out)
