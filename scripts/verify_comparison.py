# SPDX-License-Identifier: AGPL-3.0-only
"""Verify exact fixed-sample coverage, model independence and sanitized API budget."""
import argparse
import json
import os
import sys
from pathlib import Path

from comparison_prepare import sha256, MODELS, atomic
from comparison_report import summary


def verify(job):
    samples = json.loads((job / "samples.json").read_text(encoding="utf-8"))["samples"]
    expected = {sample["id"]: sample["sha256"] for sample in samples}
    if len(expected) != 10 or len({sample["groupId"] for sample in samples}) != 10:
        raise RuntimeError("Fixed ten distinct-group samples not preserved")
    for sample in samples:
        if sha256(job / sample["image"]) != sample["sha256"] or sha256(Path(sample["sourceCrop"])) != sample["sourceCropSha256"]:
            raise RuntimeError("Comparison sample or original crop changed")
    data = summary(job)
    for key, report in data["reports"].items():
        if report["status"] != "completed" or report["completedCount"] != 10:
            raise RuntimeError(f"{key} not fully completed: {report['status']} {report['completedCount']}/10")
        for prediction in report["predictions"]:
            if expected.get(prediction["sampleId"]) != prediction["imageSha256"] or not 1 <= len(prediction.get("top5", [])) <= 5:
                raise RuntimeError("Different image or missing result used in comparison")
    for key in ["huge", "bioclip2"]:
        report = data["reports"][key]
        if report["checkpointSha256"] != MODELS[key]["sha256"] or report["textCompleted"] != 11167:
            raise RuntimeError("Model weights or independently encoded global text set incomplete")
    if data["reports"]["huge"]["candidateFingerprint"] != data["reports"]["bioclip2"]["candidateFingerprint"]:
        raise RuntimeError("BioCLIP candidate lists differ")
    api = data["reports"]["dongniao"]
    if api["submissionCount"] != 10:
        raise RuntimeError("API submission budget not exactly the authorized ten")
    secret = os.environ.get("DONGNIAO_API_KEY", "")
    if secret:
        for path in job.glob("*.json"):
            if secret in path.read_text(encoding="utf-8"):
                raise RuntimeError("Credential leak detected in persisted result")
    atomic(job / "summary-final.json", data)
    proof = {"verified": True, "samples": 10, "completedPerSystem": {key: report["completedCount"] for key, report in data["reports"].items()},
             "apiSubmissions": api["submissionCount"], "apiPolls": api["pollCount"], "globalCandidates": 11167,
             "pairwiseAgreement": data["pairwiseAgreement"], "threeWay": data["threeWay"],
             "fourWay": data["fourWay"], "timing": data["timing"], "groundTruth": None}
    atomic(job / "verification.json", proof)
    return proof


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.job.resolve()), ensure_ascii=False))
