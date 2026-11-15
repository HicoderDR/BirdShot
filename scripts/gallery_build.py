# SPDX-License-Identifier: AGPL-3.0-only
"""Versioned crop verification index; never modifies the previous batch result."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

from comparison_prepare import ROOT, atomic, sha256
from gallery_policy import bird_state, POLICY_VERSION, THRESHOLDS

DECISION_VERSION = "verified-bird-gallery-birder-v2"


def build(job, revision, node, comparison_job):
    verification_file = revision / "verification.json"
    if not verification_file.exists():
        return False
    verified = json.loads(verification_file.read_text(encoding="utf-8"))
    rows = {r["crop"]: r for r in verified["rows"]}
    mapping = json.loads((job / "mapping.json").read_text(encoding="utf-8"))["crops"]
    groups = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((job / "groups").glob("g*.json"))]
    capture_path = job / "revisions" / "capture-exif-v1.json"
    enriched_path = job / "revisions" / "capture-enriched-v2.json"
    if enriched_path.exists():
        capture_metadata = json.loads(enriched_path.read_text(encoding="utf-8"))
        raw_path = job / "revisions" / "paired-raw-location-v1.json"
        if capture_metadata["baseMetadataSha256"] != sha256(capture_path) or capture_metadata["rawInspectionSha256"] != sha256(raw_path):
            raise RuntimeError("Enriched capture metadata source fingerprint changed; do not silently reuse stale locations")
        if capture_metadata.get("additionalLocationAuditSha256") and capture_metadata["additionalLocationAuditSha256"] != sha256(
                job / "revisions" / "jpeg-sidecar-location-v1.json"):
            raise RuntimeError("Additional location source fingerprint changed")
    else:
        capture_metadata = json.loads(capture_path.read_text(encoding="utf-8")) if capture_path.exists() else {"photos": {}, "statistics": {}}
    pending_capture = {"status": "pending", "time": None, "timeZone": None, "timeSource": None,
                       "location": None, "locationNote": "原图必要EXIF元数据尚未提取"}
    for group in groups:
        for frame in group["frames"]:
            metadata = capture_metadata["photos"].get(frame["id"], {})
            frame["capture"] = metadata.get("capture", pending_capture)
            if metadata.get("shootGroup"):
                frame["shootGroup"] = metadata["shootGroup"]
    original_by_crop = {bird["crop"]: (group, frame, bird) for group in groups for frame in group["frames"] for bird in frame["instances"]}
    english_taxonomy = json.loads((comparison_job / "candidates.json").read_text(encoding="utf-8"))
    english_names = {row["scientificName"].casefold(): row["commonName"] for row in english_taxonomy["labels"]
                     if row["commonName"].casefold() != row["scientificName"].casefold()}
    english_source = {"source": english_taxonomy["source"], "taxonomy": english_taxonomy["taxonomy"],
                      "sourceSha256": english_taxonomy["rawCsvSha256"], "matchPolicy": "exact scientific name; no inferred synonyms"}
    items = []
    for record in mapping:
        group, frame, bird = original_by_crop[record["crop"]]
        evidence = rows.get(record["crop"])
        state, reason = bird_state(evidence)
        predictions = [{**candidate, "englishName": english_names.get(candidate.get("scientificName", "").casefold())}
                       for candidate in evidence.get("top5", [])] if evidence and evidence["status"] == "completed" else []
        species = {"top5": predictions, "top3": predictions[:3],
                   "model": "Birder HieraDet Small · iNat2021",
                   "coverage": "全球iNat2021固定10000生物类/1486鸟类，非穷尽江苏鸟种；原全类概率不重归一化，中文名精确查表",
                   "nameSource": verified.get("chineseNames"), "englishNameSource": english_source}
        items.append({
            "id": f"{frame['id']}_b{bird['id']}", "groupId": record["groupId"], "frameId": frame["id"],
            "instanceId": bird["id"], "crop": record["crop"], "thumbnail": bird["thumbnail"],
            "sourceLabel": record["sourceRelative"], "selectedBefore": record["selected"],
            "capture": frame["capture"],
            "shootGroup": frame.get("shootGroup"),
            "birdVerification": {"status": state, "model": "Birder HieraDet Small · iNat2021", "reason": reason,
                                 "evidence": {key: evidence[key] for key in ["status", "error", "scoreType", "birdMass", "nonBirdMass",
                                               "maxBird", "maxNonBird", "top1IsBird", "margin", "detectionConfidence", "classCount"]
                                              if evidence and key in evidence} if evidence else None,
                                 "policyVersion": DECISION_VERSION},
            "species": species,
        })
    data = {
        "version": DECISION_VERSION, "status": verified["status"],
        "progress": {"processed": verified["processed"], "total": len(mapping)}, "oldSelected": sum(r["selected"] for r in mapping),
        "model": verified["model"], "groups": groups, "items": items,
        "captureSummary": capture_metadata["statistics"],
        "rawMetadataSummary": capture_metadata.get("rawInspectionSummary"),
        "additionalLocationSummary": capture_metadata.get("additionalLocationSummary"),
        "shootGroups": capture_metadata.get("shootGroups", []),
        "userRegionReference": capture_metadata.get("userRegionReference"),
        "policy": {"version": DECISION_VERSION, "gatePolicy": POLICY_VERSION, "thresholds": THRESHOLDS,
                   "note": "Birder全类层级Aves/非Aves竞争，少量人工case检查的启发式非正确率；未知保留复核，源图与旧343不改。",
                   "gateFingerprint": verified["fingerprint"]},
    }
    atomic(revision / "rank-input.json", data)
    result = subprocess.run([str(node), "--experimental-strip-types", str(ROOT / "scripts" / "gallery_rank.mjs"),
                             str(job), str(revision)], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr)
    print(result.stdout.strip(), flush=True)
    if verified["status"] == "error":
        raise RuntimeError(verified.get("error", "Birder verification failed; result is incomplete"))
    return verified["status"] == "completed"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, default=ROOT / ".tools" / "species-comparison-10")
    parser.add_argument("--node", type=Path, default=ROOT / ".tools" / "node-v24.11.1-win-x64" / "node.exe")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    while True:
        done = build(args.job.resolve(), args.revision.resolve(), args.node, args.comparison.resolve())
        if done or not args.watch:
            break
        time.sleep(60)
