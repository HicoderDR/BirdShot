# SPDX-License-Identifier: AGPL-3.0-only
"""Official global iNaturalist Birder classifier, not a European regional model."""
import argparse
from datetime import datetime
import importlib.metadata
import json
from pathlib import Path
import time

from PIL import Image
import torch

from comparison_prepare import atomic, CACHE, fetch, sha256

MODEL_ID = "hieradet_d_small_dino-v2-inat21"
REPO = "birder-project/" + MODEL_ID
REVISION = "cbd6a5e2e2647374d8f1c66a3a515560b2d8fa4b"
CHECKSUM = "271fa9ed6a9aa1f4d1fc8bbb4c4cac9d15b264f2ac544efb5cd971412691880d"


def prepare_checkpoint():
    directory = CACHE / "birder"
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = directory / (MODEL_ID + ".pt")
    base = f"https://huggingface.co/{REPO}/resolve/{REVISION}"
    fetch(f"{base}/{MODEL_ID}.pt", checkpoint)
    fetch(f"{base}/{MODEL_ID}.json", directory / (MODEL_ID + ".json"))
    fetch(f"{base}/README.md", directory / "README.md")
    if sha256(checkpoint) != CHECKSUM:
        raise RuntimeError("Birder checkpoint SHA256 mismatch")
    return checkpoint


def run(job, threads):
    import birder
    report = {
        "name": "Birder HieraDet Small · iNat2021", "modelId": MODEL_ID, "repo": REPO, "revision": REVISION,
        "license": "Apache-2.0", "checkpointSha256": CHECKSUM,
        "coverage": "全球iNaturalist2021固定10000生物类别，非穷尽全球鸟类/江苏专用分类头；未覆盖物种只能被迫选近似类，不能当有效正确结果。",
        "classCount": 10000, "birdClassCount": None, "regionFilter": None,
        "status": "loading", "predictions": [], "device": "CPU", "precision": "float32", "threads": threads,
        "versions": {p: importlib.metadata.version(p) for p in ["birder", "torch"]},
        "scoreMeaning": "固定10000生物类分类头softmax和原始logit，非全球11167鸟种闭集；概率不可与BioCLIP或API横比。",
    }
    atomic(job / "birder.json", report)
    timer = time.perf_counter()
    checkpoint = prepare_checkpoint()
    report["downloadSeconds"] = time.perf_counter() - timer
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    timer = time.perf_counter()
    net, info, transform = birder.load_pretrained_model_and_transform(
        MODEL_ID, dst=checkpoint, inference=True, device=torch.device("cpu"), progress_bar=False)
    net.eval()
    report.update(loadingSeconds=time.perf_counter() - timer, preprocess=str(transform), classCount=len(info.class_to_idx))
    mapping = {}
    for label, index in info.class_to_idx.items():
        parts = label.split("_")
        # iNat2021 class labels retain class/order/family/genus/species; original label remains preserved.
        scientific = " ".join(parts[-2:]) if len(parts) >= 3 else ""
        mapping[index] = {"scientificName": scientific, "className": label, "classIndex": index, "isBird": "Aves" in parts}
    report["birdClassCount"] = sum(row["isBird"] for row in mapping.values())
    report["classMappingSha256"] = __import__("hashlib").sha256(json.dumps(info.class_to_idx, sort_keys=True).encode()).hexdigest()
    atomic(job / "birder-classes.json", {"modelId": MODEL_ID, "revision": REVISION, "classes": list(mapping.values())})
    ebird = json.loads((job / "candidates.json").read_text(encoding="utf-8"))["labels"]
    english = {row["scientificName"].casefold(): row["commonName"] for row in ebird}
    report.update(status="classifying")
    atomic(job / "birder.json", report)
    samples = json.loads((job / "samples.json").read_text(encoding="utf-8"))["samples"]
    with torch.inference_mode():
        for sample in samples:
            path = job / sample["image"]
            if sha256(path) != sample["sha256"]:
                raise RuntimeError("Fixed sample image fingerprint changed")
            begin = time.perf_counter()
            with Image.open(path) as image:
                tensor = transform(image.convert("RGB")).unsqueeze(0)
            preprocessing = time.perf_counter() - begin
            start = time.perf_counter()
            logits = net(tensor)
            probabilities = logits.softmax(dim=-1)
            elapsed = time.perf_counter() - start
            top = probabilities[0].topk(5).indices.tolist()
            predictions = [{**mapping[i], "commonName": english.get(mapping[i]["scientificName"].casefold(), ""),
                            "probability": float(probabilities[0, i]), "logit": float(logits[0, i])} for i in top]
            report["predictions"].append({
                "sampleId": sample["id"], "status": "completed", "imageSha256": sample["sha256"],
                "preprocessingSeconds": preprocessing, "inferenceSeconds": elapsed,
                "endToEndSeconds": time.perf_counter() - begin, "top5": predictions,
                "coverageWarning": f"固定{len(mapping)}生物类，其中{report['birdClassCount']}鸟类；此列未做全球鸟类全集识别。候选学名是否覆盖可在JSON核对，非真值。",
            })
            atomic(job / "birder.json", report)
            print(f"birder {sample['id']}: completed ({elapsed:.2f}s)", flush=True)
    report.update(status="completed", finishedAt=datetime.now().isoformat())
    atomic(job / "birder.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--prepare-only", action="store_true", help="Verify/download the public checkpoint without reading comparison images")
    args = parser.parse_args()
    if args.prepare_only:
        prepare_checkpoint()
        print("Birder official checkpoint verified; no samples read and no inference")
    elif args.job:
        run(args.job.resolve(), args.threads)
    else:
        parser.error("--job is required unless --prepare-only is used")
