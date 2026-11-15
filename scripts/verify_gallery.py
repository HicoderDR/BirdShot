# SPDX-License-Identifier: AGPL-3.0-only
import argparse
from collections import Counter
import json
from pathlib import Path

from comparison_prepare import atomic, sha256


def verify(job, revision):
    gallery = json.loads((revision / "gallery.json").read_text(encoding="utf-8"))
    mapping = json.loads((job / "mapping.json").read_text(encoding="utf-8"))["crops"]
    if gallery["status"] != "completed" or gallery["progress"] != {"processed": len(mapping), "total": len(mapping)}:
        raise RuntimeError("All candidate crops have not yet been reverified")
    items = gallery["items"]
    if len(items) != len(mapping) or len({item["crop"] for item in items}) != len(mapping):
        raise RuntimeError("Candidate missing or duplicated")
    preferred = [item for item in items if item["decision"] == "keep"]
    if not all(item["birdVerification"]["status"] == "bird" and item["quality"]["cohortSize"] >= 2
               and not item["quality"]["edgeRisk"] for item in preferred):
        raise RuntimeError("Nonbird/unknown/singleton/edge-risk entered preferred pool")
    if any(item.get("preferredRank") for item in items if item["birdVerification"]["status"] != "bird"):
        raise RuntimeError("Unverified crop has a preferred rank")
    if sorted(item["rank"] for item in preferred) != list(range(1, len(preferred) + 1)):
        raise RuntimeError("Preferred ranks incomplete or duplicated")
    by_crop = {item["crop"]: item for item in items}
    capture_path = job / "revisions" / "capture-enriched-v2.json"
    if not capture_path.exists():
        capture_path = job / "revisions" / "capture-exif-v1.json"
    capture_metadata = json.loads(capture_path.read_text(encoding="utf-8")) if capture_path.exists() else None
    name_counts = Counter()
    verification_rows = {row["crop"]: row for row in json.loads((revision / "verification.json").read_text(encoding="utf-8"))["rows"]}
    for record in mapping:
        item = by_crop[record["crop"]]
        if capture_metadata is not None:
            expected_capture = capture_metadata["photos"][item["frameId"]]["capture"]
            if item.get("capture") != expected_capture:
                raise RuntimeError("Crop capture metadata does not match its original photograph")
            if capture_metadata["photos"][item["frameId"]].get("shootGroup") and item.get("shootGroup") != capture_metadata["photos"][item["frameId"]]["shootGroup"]:
                raise RuntimeError("Crop shoot group does not match its original photograph")
        if sha256(job / item["crop"]) != record["cropSha256"]:
            raise RuntimeError("Existing crop changed during reverification")
        source_row = verification_rows[record["crop"]]
        top3 = item["species"].get("top3", [])
        if source_row["status"] == "completed":
            if [{key: value for key, value in candidate.items() if key != "englishName"} for candidate in top3] != source_row["top5"][:3] or len(top3) != 3:
                raise RuntimeError("Top3 changed from the actual full-head Birder predictions")
            for candidate in top3:
                if not 0 <= candidate["probability"] <= 1 or not candidate["scientificName"]:
                    raise RuntimeError("Invalid candidate probability/name")
                if candidate.get("nameStatus") == "exact" and candidate.get("nameSource"):
                    name_counts["matchedOccurrences"] += 1
                elif candidate["commonName"] == "中文名待补":
                    name_counts["missingOccurrences"] += 1
                else:
                    raise RuntimeError("Chinese candidate name has no traceable source or missing-name label")
                name_counts["englishMatchedOccurrences" if candidate.get("englishName") else "englishMissingOccurrences"] += 1
    bad_cases = ["crops/g00112/p000267_b05.jpg", "crops/g00112/p000268_b05.jpg",
                 "crops/g00119/p000288_b01.jpg", "crops/g00119/p000290_b01.jpg",
                 "crops/g00112/p000267_b01.jpg"]
    for crop in bad_cases:
        if by_crop[crop]["decision"] == "keep" or by_crop[crop]["birdVerification"]["status"] == "bird":
            raise RuntimeError("Inspected leaf/branch false-selection case not excluded")
    true_bird_cases = ["crops/g00119/p000288_b02.jpg", "crops/g00119/p000290_b02.jpg"]
    if not any(by_crop[crop]["decision"] == "keep" for crop in true_bird_cases):
        raise RuntimeError("True sibling bird instances still cannot be selected")
    proof = {"verified": True, "candidateCount": len(items), "preferred": len(preferred),
             "states": dict(Counter(item["birdVerification"]["status"] for item in items)),
             "removedOld": gallery["summary"]["removedOld"], "addedNew": gallery["summary"]["addedNew"],
             "oldJobUnmodifiedCrops": True, "inspectedFalseSelectionsExcluded": True,
             "trueSiblingBirdSelectable": True, "expertGroundTruth": False}
    proof["top3Names"] = dict(name_counts)
    if capture_metadata is not None:
        for group_path in (revision / "groups").glob("g*.json"):
            group = json.loads(group_path.read_text(encoding="utf-8"))
            for frame in group["frames"]:
                if frame.get("capture") != capture_metadata["photos"][frame["id"]]["capture"]:
                    raise RuntimeError("Group capture metadata does not match its original photograph")
        proof["captureMetadata"] = capture_metadata["statistics"]
    atomic(revision / "validation.json", proof)
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.job.resolve(), args.revision.resolve()), ensure_ascii=False))
