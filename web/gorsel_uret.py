# -*- coding: utf-8 -*-
"""
SDXL-Turbo ile kapak görseli üretir.
Düşük VRAM (sequential CPU offload) → sıcak şarkı servisiyle (ACE-Step) çakışmaz.
Ayrı süreç olarak çalışır; bitince tüm belleği bırakır (sıralı çalışma ilkesi).

Kullanım:
  python gorsel_uret.py --prompt "..." --out cover.png [--steps 4]
"""
import argparse
import torch
from diffusers import AutoPipelineForText2Image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=4)   # SDXL-Turbo: 1-4 adım yeter
    args = ap.parse_args()

    pipe = AutoPipelineForText2Image.from_pretrained(
        "stabilityai/sdxl-turbo",
        torch_dtype=torch.float16,
        variant="fp16",
    )
    # Düşük VRAM: ağırlıkların çoğu CPU/RAM'de, katmanlar gerektikçe GPU'ya taşınır.
    # Böylece sıcak ACE-Step VRAM'i tutarken bile sığar.
    pipe.enable_sequential_cpu_offload()

    image = pipe(
        prompt=args.prompt,
        num_inference_steps=args.steps,
        guidance_scale=0.0,   # SDXL-Turbo CFG kullanmaz
        height=512, width=512,
    ).images[0]
    image.save(args.out)
    print("GORSEL_OK:", args.out, flush=True)


if __name__ == "__main__":
    main()
