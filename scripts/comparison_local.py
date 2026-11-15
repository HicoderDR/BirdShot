# SPDX-License-Identifier: AGPL-3.0-only
"""Actual fixed-candidate local BioCLIP inference; resumable per-model text cache."""
import argparse
from datetime import datetime
import gc
import importlib.metadata
import json
import os
from pathlib import Path
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TORCH_FORCE_WEIGHTS_ONLY_LOAD", "1")
import numpy as np
from PIL import Image
import psutil
import torch
import torch.nn.functional as F

from comparison_prepare import atomic, sha256, CACHE, MODELS


def encode_text_without_trailing_padding(model, tokens):
    length = int(tokens.argmax(dim=-1).max()) + 1
    position, attention = model.positional_embedding, model.attn_mask
    try:
        # Causal attention cannot read positions after EOS; remove only unused trailing padding.
        model.positional_embedding = torch.nn.Parameter(position[:length], requires_grad=False)
        model.attn_mask = attention[:length, :length]
        return model.encode_text(tokens[:, :length], normalize=True)
    finally:
        model.positional_embedding = position
        model.attn_mask = attention


def run(job, key, threads):
    import open_clip
    spec = MODELS[key]
    directory = CACHE / key
    checkpoint = directory / "open_clip_model.safetensors"
    if not checkpoint.exists() or sha256(checkpoint) != spec["sha256"]:
        raise RuntimeError("Missing or unverified official model checkpoint")
    candidates = json.loads((job / "candidates.json").read_text(encoding="utf-8"))
    samples = json.loads((job / "samples.json").read_text(encoding="utf-8"))["samples"]
    result_path = job / f"{key}.json"
    metadata = {
        "status": "loading", "key": key, "name": "BioCLIP 2.5 Huge" if key == "huge" else "BioCLIP 2",
        "repo": spec["repo"], "revision": spec["revision"], "checkpointSha256": spec["sha256"], "license": "MIT",
        "architecture": spec["architecture"], "candidateCount": candidates["count"],
        "candidateFingerprint": sha256(job / "candidates.json"), "template": candidates["template"],
        "regionFilter": None, "device": "CPU", "precision": "float32", "threads": threads,
        "versions": {package: importlib.metadata.version(package) for package in ["torch", "open_clip_torch"]},
        "loadingSeconds": None, "textEncodingSeconds": None, "textCompleted": 0, "predictions": [],
        "scoreMeaning": "cosine 为归一化向量点积；logit=exp(logit_scale)*cosine；softmax仅在本轮11167全球候选内归一化，非校准正确率。",
    }
    atomic(result_path, metadata)
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    started = time.perf_counter()
    cfg = json.loads((directory / "open_clip_config.json").read_text(encoding="utf-8"))
    configurations = CACHE / "configs"
    configurations.mkdir(exist_ok=True)
    atomic(configurations / f"birdshot_{key}.json", cfg["model_cfg"])
    open_clip.add_model_config(configurations)
    model, _, preprocess = open_clip.create_model_and_transforms(
        f"birdshot_{key}", pretrained=str(checkpoint), device="cpu", precision="fp32",
        image_mean=cfg["preprocess_cfg"]["mean"], image_std=cfg["preprocess_cfg"]["std"],
        image_interpolation=cfg["preprocess_cfg"]["interpolation"], image_resize_mode=cfg["preprocess_cfg"]["resize_mode"],
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(spec["architecture"])
    metadata.update(loadingSeconds=time.perf_counter() - started, preprocess=str(preprocess), status="encoding-text",
                    parameters=sum(p.numel() for p in model.parameters()), ramBytes=psutil.Process().memory_info().rss)
    atomic(result_path, metadata)
    prompts = [candidates["template"].format(**label) for label in candidates["labels"]]
    tokens = tokenizer(prompts)
    with torch.inference_mode():
        validation = tokens[:4]
        full = model.encode_text(validation, normalize=True)
        trimmed = encode_text_without_trailing_padding(model, validation)
        difference = float((full - trimmed).abs().max())
        if not torch.allclose(full, trimmed, atol=2e-6, rtol=2e-5):
            raise RuntimeError("Causal-padding optimization failed numerical equivalence check")
        metadata["paddingOptimization"] = {"method": "EOS后因果不可见的尾部padding移除；权重不变，FP32", "maxAbsFeatureDifference": difference}
        atomic(result_path, metadata)
    features_file = directory / f"text-{sha256(job / 'candidates.json')[:16]}.npy"
    progress_file = features_file.with_suffix(".json")
    dimension = cfg["model_cfg"]["embed_dim"]
    if progress_file.exists():
        progress = json.loads(progress_file.read_text())
        if progress["modelSha256"] != spec["sha256"]:
            raise RuntimeError("Text features cannot be shared across different model weights")
        completed = progress["completed"]
        text_seconds = progress["seconds"]
        text_features = np.load(features_file, mmap_mode="r+")
    else:
        completed, text_seconds = 0, 0
        text_features = np.lib.format.open_memmap(features_file, mode="w+", dtype=np.float32, shape=(len(prompts), dimension))
    metadata["textCacheReusedClasses"] = completed
    metadata["timingNote"] = "文本编码秒数累计已保存各段；加载与totalSeconds是当前进程恢复后耗时，不包含下载或先前进程加载。"
    batch = 16 if key == "bioclip2" else 8
    with torch.inference_mode():
        for offset in range(completed, len(prompts), batch):
            timer = time.perf_counter()
            features = encode_text_without_trailing_padding(model, tokens[offset:offset + batch])
            text_features[offset:offset + len(features)] = features.cpu().numpy()
            text_seconds += time.perf_counter() - timer
            completed = min(offset + batch, len(prompts))
            if completed % (batch * 10) == 0 or completed == len(prompts):
                text_features.flush()
                atomic(progress_file, {"modelSha256": spec["sha256"], "completed": completed, "seconds": text_seconds})
                metadata.update(textCompleted=completed, textEncodingSeconds=text_seconds, ramBytes=psutil.Process().memory_info().rss)
                atomic(result_path, metadata)
                print(f"{key}: text {completed}/{len(prompts)} ({text_seconds:.1f}s)", flush=True)
        metadata.update(textCompleted=completed, textEncodingSeconds=text_seconds, status="classifying")
        atomic(result_path, metadata)
        text_tensor = torch.from_numpy(np.array(text_features))
        scale = float(model.logit_scale.exp())
        metadata["logitScale"] = scale
        for sample in samples:
            path = job / sample["image"]
            if sha256(path) != sample["sha256"]:
                raise RuntimeError("Fixed sample image fingerprint changed")
            begin = time.perf_counter()
            with Image.open(path) as image:
                tensor = preprocess(image.convert("RGB")).unsqueeze(0)
            preprocessing_seconds = time.perf_counter() - begin
            inference_start = time.perf_counter()
            feature = model.encode_image(tensor, normalize=True)
            cosine = feature @ text_tensor.T
            logits = cosine * scale
            probabilities = logits.softmax(dim=-1)
            inference_seconds = time.perf_counter() - inference_start
            indices = probabilities[0].topk(5).indices.tolist()
            metadata["predictions"].append({
                "sampleId": sample["id"], "status": "completed", "imageSha256": sample["sha256"],
                "preprocessingSeconds": preprocessing_seconds, "inferenceSeconds": inference_seconds,
                "endToEndSeconds": time.perf_counter() - begin,
                "top5": [{**candidates["labels"][i], "cosine": float(cosine[0, i]), "logit": float(logits[0, i]),
                          "probability": float(probabilities[0, i])} for i in indices],
            })
            atomic(result_path, metadata)
            print(f"{key}: {sample['id']} completed ({inference_seconds:.2f}s)", flush=True)
    metadata.update(status="completed", finishedAt=datetime.now().isoformat(),
                    totalSecondsExcludingDownload=time.perf_counter() - started, ramBytes=psutil.Process().memory_info().rss)
    atomic(result_path, metadata)
    del model
    gc.collect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--model", choices=list(MODELS), required=True)
    parser.add_argument("--threads", type=int, default=6)
    args = parser.parse_args()
    run(args.job.resolve(), args.model, args.threads)
