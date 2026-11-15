# SPDX-License-Identifier: AGPL-3.0-only
"""Re-check every existing crop using explicit bird/non-bird CLIP competition."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
import numpy as np
from PIL import Image
import torch
import open_clip

from comparison_prepare import MODELS, CACHE, atomic, sha256

GATE_VERSION = "bioclip2-bird-nonbird-competition-v1"
BIRD_DESCRIPTIONS = [
    "a bird", "a small songbird", "a crow", "a duck", "a goose or swan", "an egret or heron",
    "a shorebird", "a bird of prey", "a flying bird", "a bird seen from behind",
    "a partially hidden bird", "a bird perched on a branch", "a waterbird",
    "a bird in shadow", "a black and white bird", "Animalia Chordata Aves, a bird",
]
NONBIRD_DESCRIPTIONS = [
    "a tree branch with no bird", "a bare twig with no bird", "a tree trunk with no bird",
    "a leaf", "green leaves", "a damaged leaf with holes", "reeds and grass", "plant stems",
    "a flower", "seeds on a plant stem", "a pine cone", "bark on a tree", "a dead tree branch",
    "a rock", "a stone", "mud", "water with no animal", "ripples on water", "sand",
    "a blurry background with no animal", "a plastic bag", "a piece of fabric", "a net",
    "a building", "a pole", "a cloud", "an insect", "a butterfly", "a spider", "a mammal",
    "a squirrel", "a lizard", "a fish", "a person", "a bird nest with no bird",
]


def load_model(threads):
    spec = MODELS["bioclip2"]
    directory = CACHE / "bioclip2"
    checkpoint = directory / "open_clip_model.safetensors"
    if sha256(checkpoint) != spec["sha256"]:
        raise RuntimeError("Cannot verify official BioCLIP2 checkpoint")
    cfg = json.loads((directory / "open_clip_config.json").read_text(encoding="utf-8"))
    config_directory = CACHE / "configs"
    config_directory.mkdir(exist_ok=True)
    atomic(config_directory / "birdshot_bioclip2.json", cfg["model_cfg"])
    open_clip.add_model_config(config_directory)
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    model, _, transform = open_clip.create_model_and_transforms(
        "birdshot_bioclip2", pretrained=str(checkpoint), device="cpu", precision="fp32",
        image_mean=cfg["preprocess_cfg"]["mean"], image_std=cfg["preprocess_cfg"]["std"],
        image_interpolation=cfg["preprocess_cfg"]["interpolation"], image_resize_mode=cfg["preprocess_cfg"]["resize_mode"])
    return model.eval(), transform


def verify(job, output, threads, only_evidence=False):
    mapping = json.loads((job / "mapping.json").read_text(encoding="utf-8"))["crops"]
    if only_evidence:
        mapping = [r for r in mapping if r["groupId"] in {"g00112", "g00119", "g00251", "g00025", "g00349"}]
    output.mkdir(parents=True, exist_ok=True)
    templates = ["a photo of {}.", "a cropped photo of {}.", "a close-up photo of {}."]
    labels = [{"description": name, "kind": "bird"} for name in BIRD_DESCRIPTIONS] + [
        {"description": name, "kind": "nonBird"} for name in NONBIRD_DESCRIPTIONS]
    policy = {"version": GATE_VERSION, "model": MODELS["bioclip2"], "labels": labels, "templates": templates,
              "stateRule": "raw bird-minus-nonbird cosine margin; thresholds are local heuristics evaluated against inspected cases, not calibrated accuracy"}
    fingerprint = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    atomic(output / "policy.json", {**policy, "fingerprint": fingerprint})
    result_path = output / ("evidence.json" if only_evidence else "verification.json")
    result = {"version": GATE_VERSION, "fingerprint": fingerprint, "status": "loading",
              "total": len(mapping), "processed": 0, "rows": [], "updatedAt": datetime.now().isoformat()}
    if result_path.exists():
        old = json.loads(result_path.read_text(encoding="utf-8"))
        if old.get("fingerprint") == fingerprint:
            result = old
        else:
            raise RuntimeError("Gate policy changed; use a new version output directory")
    elif not only_evidence and (output / "evidence.json").exists():
        evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
        if evidence.get("fingerprint") == fingerprint and evidence["status"] == "completed":
            result["rows"] = evidence["rows"]
            result["processed"] = len(result["rows"])
    result["total"] = len(mapping)
    atomic(result_path, result)
    model, transform = load_model(threads)
    tokenizer = open_clip.get_tokenizer("ViT-L-14")
    with torch.inference_mode():
        texts = []
        for label in labels:
            tokens = tokenizer([t.format(label["description"]) for t in templates])
            encoded = model.encode_text(tokens, normalize=True).mean(dim=0)
            texts.append(encoded / encoded.norm())
        text_features = torch.stack(texts)
        done = {r["crop"] for r in result["rows"]}
        pending = [r for r in mapping if r["crop"] not in done]
        result["status"] = "running"
        atomic(result_path, result)
        feature_directory = output / "features"
        feature_directory.mkdir(exist_ok=True)
        for offset in range(0, len(pending), 8):
            batch = pending[offset:offset + 8]
            tensors, valid, failed = [], [], []
            started = time.perf_counter()
            for record in batch:
                try:
                    path = job / record["crop"]
                    if sha256(path) != record["cropSha256"]:
                        raise RuntimeError("Existing crop fingerprint changed")
                    with Image.open(path) as image:
                        tensors.append(transform(image.convert("RGB")))
                    valid.append(record)
                except (OSError, RuntimeError) as error:
                    failed.append({"crop": record["crop"], "status": "error", "error": str(error), "state": "unknown"})
            if valid:
                features = model.encode_image(torch.stack(tensors), normalize=True)
                similarities = features @ text_features.T
                for i, record in enumerate(valid):
                    bird_score, bird_index = similarities[i, :len(BIRD_DESCRIPTIONS)].max(dim=0)
                    other_score, other_index = similarities[i, len(BIRD_DESCRIPTIONS):].max(dim=0)
                    margin = float(bird_score - other_score)
                    # Preserve scores; final thresholds are versioned separately after inspected-case checks.
                    feature_path = feature_directory / (Path(record["crop"]).stem + "_" + record["groupId"] + ".npy")
                    np.save(feature_path, features[i].cpu().numpy())
                    result["rows"].append({
                        "crop": record["crop"], "cropSha256": record["cropSha256"], "status": "completed",
                        "groupId": record["groupId"], "photoId": record["photoId"], "instanceId": record["instanceId"],
                        "birdCosine": float(bird_score), "nonBirdCosine": float(other_score), "margin": margin,
                        "birdPrompt": BIRD_DESCRIPTIONS[int(bird_index)],
                        "nonBirdPrompt": NONBIRD_DESCRIPTIONS[int(other_index)], "detectionConfidence": record["bodyConfidence"],
                        "feature": feature_path.relative_to(output).as_posix(),
                    })
            result["rows"].extend(failed)
            result.update(processed=len(result["rows"]), updatedAt=datetime.now().isoformat(),
                          lastBatchSeconds=time.perf_counter() - started)
            atomic(result_path, result)
            print(f"bird verification {result['processed']}/{result['total']} ({result['lastBatchSeconds']:.1f}s/batch)", flush=True)
    result.update(status="completed", updatedAt=datetime.now().isoformat())
    atomic(result_path, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--evidence", action="store_true")
    args = parser.parse_args()
    verify(args.job.resolve(), args.output.resolve(), args.threads, args.evidence)
