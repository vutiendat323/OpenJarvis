"""Export the REAL-TSE WeSep BSRNN target-speaker extractor to TorchScript.

The Voice separator (``[voice.speaker] separator = "tse"``) loads the result
with plain torch, so OpenJarvis never imports wesep or wespeaker. Shapes are
fixed: ``MIX_SECS`` of mixture and ``ENROLL_SECS`` of enrollment, 16 kHz.

Run once, outside the OpenJarvis venv's dependency set:

    git clone https://github.com/REAL-TSE/wesep-real-tse
    # checkpoints: the REAL-TSE Google Drive zip, folder spk_emb_100
    pip install wespeaker@git+https://github.com/wenet-e2e/wespeaker.git@8f53b64
    python scripts/voice_tse_export.py WESEP_REPO CKPT_DIR \\
        ~/.cache/openjarvis/tse/bsrnn_spk_emb_100.ts

The upstream checkpoint ships without its own licence file (WeSep code is
Apache-2.0; Libri2Mix and VoxCeleb data are CC BY 4.0): keep the export local.
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch

SAMPLE_RATE = 16_000
MIX_SECS = 8
ENROLL_SECS = 3
# The model's STFT hop; the mixture gets one hop of padding so the output
# covers every input sample.
_HOP = 128


def main(wesep_repo: str, ckpt_dir: str, out: str) -> None:
    sys.path.insert(0, wesep_repo)
    import wesep

    # Traced on CUDA: the STFT window is baked in as a constant on this device.
    model = wesep.load_model_local(ckpt_dir).model.eval().cuda()
    for module in model.modules():
        if hasattr(module, "dither"):
            module.dither = 0.0  # kaldi fbank dither is training noise
    mix = torch.zeros(1, MIX_SECS * SAMPLE_RATE + _HOP, device="cuda")
    enroll = torch.zeros(1, ENROLL_SECS * SAMPLE_RATE, device="cuda")
    torch.manual_seed(0)
    mix.normal_(0, 0.05)
    enroll.normal_(0, 0.05)

    class Extract(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.model = model

        def forward(self, mix: torch.Tensor, enroll: torch.Tensor) -> torch.Tensor:
            out = self.model(mix, enroll)
            out = out[0] if isinstance(out, (list, tuple)) else out
            return out.reshape(1, -1)[:, : mix.shape[1]]

    with torch.no_grad():
        eager = Extract().eval()
        traced = torch.jit.trace(eager, (mix, enroll), check_trace=False)
        other = torch.randn_like(mix) * 0.05
        diff = (eager(other, enroll) - traced(other, enroll)).abs().max().item()
    if diff > 1e-4:
        raise SystemExit(f"traced model disagrees with eager: {diff}")
    Path(out).expanduser().parent.mkdir(parents=True, exist_ok=True)
    traced.save(str(Path(out).expanduser()))
    print(f"wrote {out} (max diff vs eager {diff:.2e})")


if __name__ == "__main__":
    main(*sys.argv[1:4])
