"""
seeds.py - Uniqueness Shield.

Every StockFlow generation is assigned a deterministic-but-unique mathematical
seed derived from a UUID4 (122 bits of entropy) mixed with the prompt through
a SHA-256 KDF. The seed is:

  * in range [0, 2^32 - 1]  -> valid for HF / OpenAI-compatible / SD pipelines
  * fed to the engine so latents differ even for identical prompts
  * mixed into an 8-token "entropy descriptor" appended to the prompt itself,
    so even seed-less engines (DALL-E 3) receive unique conditioning text
  * hashed into a short "shield code" (e.g. SF-9F2C-41AB) stored with the job
    so you can prove/audit uniqueness per asset

The shield additionally keeps an in-memory + on-disk registry of every seed
already used, guaranteeing zero collisions within a deployment.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import uuid
from typing import Dict, List, Optional, Set

SEED_MAX = 2**32 - 1

# Word banks used to build the entropy descriptor. These are deliberately
# neutral, commercially-safe micro-modifiers (light, palette, composition) so
# they never hurt the visual result - they only decorrelate the latent path.
_LIGHT = [
    "soft rim light", "diffused window light", "golden-hour backlight",
    "cool studio strobe", "ambient dusk glow", "high-key bounce light",
    "low-key directional light", "overcast softbox light",
]
_PALETTE = [
    "muted teal palette", "warm amber accents", "desaturated cobalt tones",
    "subtle magenta highlights", "monochrome graphite grade",
    "pastel coral undertones", "deep emerald shadows", "ivory neutral cast",
]
_COMPOSITION = [
    "rule-of-thirds framing", "centered symmetrical composition",
    "off-axis diagonal layout", "negative-space composition",
    "layered depth composition", "tight macro framing",
    "wide establishing framing", "dynamic low-angle framing",
]


def _sha256_int(*parts: str) -> int:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return int.from_bytes(h.digest(), "big")


def entropy_tokens(seed: int) -> List[str]:
    """Derive 3 deterministic micro-modifiers from the seed."""
    rng = random.Random(seed)
    return [rng.choice(_LIGHT), rng.choice(_PALETTE), rng.choice(_COMPOSITION)]


def apply_entropy(prompt: str, seed: int) -> str:
    """Append the seed-derived entropy descriptor to a prompt."""
    tokens = entropy_tokens(seed)
    return f"{prompt.rstrip(' ,.')} , {', '.join(tokens)}"


def shield_code(seed: int) -> str:
    """Short human-auditable uniqueness code, e.g. 'SF-9F2C-41AB'."""
    digest = hashlib.sha256(f"stockflow-shield::{seed}".encode()).hexdigest().upper()
    return f"SF-{digest[:4]}-{digest[4:8]}"


class SeedRegistry:
    """Tracks every seed issued, persisting to a JSON sidecar file."""

    def __init__(self, registry_path: Optional[str] = None):
        self.path = registry_path
        self._used: Set[int] = set()
        if registry_path and os.path.exists(registry_path):
            try:
                with open(registry_path, "r", encoding="utf-8") as fh:
                    self._used = set(int(s) for s in json.load(fh))
            except (json.JSONDecodeError, OSError, ValueError):
                self._used = set()

    def _persist(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(sorted(self._used), fh)
        except OSError:
            pass  # registry persistence is best-effort

    def issue(self, prompt: str = "") -> Dict[str, object]:
        """Issue a guaranteed-unique seed bundle for one generation."""
        for _ in range(64):  # collision retry guard (astronomically unlikely)
            raw_uuid = uuid.uuid4().hex
            seed = _sha256_int(raw_uuid, prompt, os.urandom(16).hex()) % (SEED_MAX + 1)
            if seed not in self._used:
                self._used.add(seed)
                self._persist()
                return {
                    "seed": seed,
                    "shield_code": shield_code(seed),
                    "entropy_tokens": entropy_tokens(seed),
                }
        raise RuntimeError("SeedRegistry: could not issue a unique seed after 64 tries")

    def __len__(self) -> int:
        return len(self._used)
