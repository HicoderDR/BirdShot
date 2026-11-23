# SPDX-License-Identifier: AGPL-3.0-only
"""All-class Birder competition for every crop, with traceable Chinese Top3."""
import argparse
from datetime import datetime
import importlib.metadata
import json
from pathlib import Path
import time

import birder
from PIL import Image
import torch

from chinese_bird_names import load_names, localize
from comparison_birder import MODEL_ID, REPO, REVISION, CHECKSUM
from comparison_prepare import atomic, CACHE, sha256

VERSION = "birder-global-inat21-fullclass-v2"


def policy(comparison):
    names, name_source = load_names(comparison)
    spec = {"name": "Birder HieraDet Small · iNat2021", "modelId": MODEL_ID, "repo": REPO, "revision": REVISION,
            "checkpointSha256": CHECKSUM, "license": "Apache-2.0", "device": "CPU", "precision": "float32",
            "classes": 10000, "birdClasses": 1486, "geography": "global iNat2021 sampling, not Jiangsu-specific or exhaustive",
            "frameworkVersion": importlib.metadata.version("birder")}
    policy = {"version": VERSION, "model": spec, "chineseNames": name_source,
              "stateRule": "full 10000-class softmax; sum Aves vs non-Aves and competing top probabilities; no bird-only renormalization"}
    fingerprint = __import__("hashlib").sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    return policy, fingerprint, names


class ClassifierSession:
    """One lazy CPU model, shared across group checkpoints."""
    def __init__(self, job, output, comparison, threads):
        self.job, self.output, self.threads = job, output, threads
        self.net = None
        output.mkdir(parents=True, exist_ok=True)
        configured, fingerprint, self.names = policy(comparison)
        spec, name_source = configured["model"], configured["chineseNames"]
        self.fingerprint = fingerprint
        path = output / "verification.json"
        result = {"version": VERSION, "fingerprint": fingerprint, "status": "loading", "processed": 0, "total": 0,
                  "rows": [], "model": spec, "chineseNames": name_source, "threads": threads,
                  "updatedAt": datetime.now().isoformat()}
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("fingerprint") != fingerprint:
                raise RuntimeError("Birder/model/name-source policy changed; cannot silently reuse previous verification")
            result = old
        atomic(output / "policy.json", {**configured, "fingerprint": fingerprint})
        self.path, self.result = path, result
        self.save()

    def save(self):
        self.result.update(processed=len(self.result["rows"]), updatedAt=datetime.now().isoformat())
        atomic(self.path, self.result)

    def load(self):
        if self.net is not None:
            return
        checkpoint = CACHE / "birder" / (MODEL_ID + ".pt")
        if sha256(checkpoint) != CHECKSUM:
            raise RuntimeError("Birder official checkpoint fingerprint mismatch")
        torch.set_num_threads(self.threads)
        # Interop threads can only be set before the first Torch parallel operation.
        if torch.get_num_interop_threads() != 1:
            torch.set_num_interop_threads(1)
        started = time.perf_counter()
        net, info, transform = birder.load_pretrained_model_and_transform(
            MODEL_ID, dst=checkpoint, inference=True, progress_bar=False, device=torch.device("cpu"))
        net.eval()
        mapping = {}
        for label, index in info.class_to_idx.items():
            parts = label.split("_")
            scientific = " ".join(parts[-2:]) if len(parts) >= 3 else ""
            mapping[index] = {"scientificName": scientific, "className": label, "classIndex": index, "isBird": "Aves" in parts}
        bird_ids = [index for index, row in mapping.items() if row["isBird"]]
        other_ids = [index for index, row in mapping.items() if not row["isBird"]]
        if len(mapping) != 10000 or len(bird_ids) != 1486:
            raise RuntimeError("Unexpected Birder taxonomy/head; no implicit regional substitute")
        self.net, self.transform, self.mapping = net, transform, mapping
        self.bird_ids, self.other_ids = bird_ids, other_ids
        self.result["chineseNames"].update(modelBirdClasses=len(bird_ids),
                                          modelBirdClassesMatched=sum(mapping[i]["scientificName"].casefold() in self.names for i in bird_ids))
        self.result["chineseNames"]["modelBirdClassesMissing"] = len(bird_ids) - self.result["chineseNames"]["modelBirdClassesMatched"]
        self.result.update(status="running", loadingSeconds=time.perf_counter() - started, preprocess=str(transform))
        self.save()

    def classify(self, rows, total=None, final=False, on_progress=None):
        result, job = self.result, self.job
        result.update(status="running", total=total if total is not None else len(rows))
        done = {row["crop"]: row for row in result["rows"]}
        for record in rows:
            if record["crop"] in done and done[record["crop"]].get("cropSha256") != record["cropSha256"]:
                raise RuntimeError("Cached Birder crop fingerprint changed")
            if sha256(job / record["crop"]) != record["cropSha256"]:
                raise RuntimeError("Previous crop changed")
        pending = [row for row in rows if row["crop"] not in done]
        if pending:
            if on_progress:
                on_progress("loading", result)
            self.load()
        self.save()
        with torch.inference_mode():
            for offset in range(0, len(pending), 8):
                batch = pending[offset:offset + 8]
                started = time.perf_counter()
                tensors, valid, failed = [], [], []
                for record in batch:
                    try:
                        with Image.open(job / record["crop"]) as image:
                            tensors.append(self.transform(image.convert("RGB")))
                        valid.append(record)
                    except (OSError, RuntimeError) as error:
                        failed.append({"crop": record["crop"], "cropSha256": record["cropSha256"],
                                       "status": "error", "error": str(error), "state": "unknown"})
                if valid:
                    logits = self.net(torch.stack(tensors))
                    probabilities = logits.softmax(-1)
                    for record, scores, raw in zip(valid, probabilities, logits):
                        bird_mass = float(scores[self.bird_ids].sum())
                        max_bird = float(scores[self.bird_ids].max())
                        max_other = float(scores[self.other_ids].max())
                        top = scores.topk(5).indices.tolist()
                        predictions = [localize({**self.mapping[i], "probability": float(scores[i]), "logit": float(raw[i])},
                                                self.names, result["chineseNames"]) for i in top]
                        result["rows"].append({
                            "crop": record["crop"], "cropSha256": record["cropSha256"], "status": "completed",
                            "groupId": record["groupId"], "photoId": record["photoId"], "instanceId": record["instanceId"],
                            "scoreType": "birder-fullclass", "birdMass": bird_mass, "nonBirdMass": float(scores[self.other_ids].sum()),
                            "maxBird": max_bird, "maxNonBird": max_other, "top1IsBird": predictions[0]["isBird"],
                            "margin": max_bird - max_other, "detectionConfidence": record["bodyConfidence"],
                            "top5": predictions, "top3": predictions[:3],
                            "classCount": 10000, "birdClassCount": len(self.bird_ids),
                        })
                result["rows"].extend(failed)
                result["lastBatchSeconds"] = time.perf_counter() - started
                self.save()
                if on_progress:
                    on_progress("classified", result)
                print(f"Birder classification {result['processed']}/{result['total']} ({result['lastBatchSeconds']:.1f}s/batch)", flush=True)
        result.update(status="completed" if final else "running")
        if final:
            result["finishedAt"] = datetime.now().isoformat()
        self.save()
        return [done for done in result["rows"] if done["crop"] in {row["crop"] for row in rows}]


def run(job, output, comparison, threads):
    rows = json.loads((job / "mapping.json").read_text(encoding="utf-8"))["crops"]
    session = ClassifierSession(job, output, comparison, threads)
    session.classify(rows, final=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=6)
    args = parser.parse_args()
    try:
        run(args.job.resolve(), args.output.resolve(), args.comparison.resolve(), args.threads)
    except (OSError, ValueError, RuntimeError) as error:
        progress = args.output.resolve() / "verification.json"
        if progress.exists():
            saved = json.loads(progress.read_text(encoding="utf-8"))
            saved.update(status="error", error=str(error), updatedAt=datetime.now().isoformat())
            atomic(progress, saved)
        raise
