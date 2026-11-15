# SPDX-License-Identifier: AGPL-3.0-only
"""Exact scientific-name lookup, not machine translation or guessed taxon synonyms."""
import json
from pathlib import Path
import re

from comparison_prepare import atomic, sha256

NAME_URL = "https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=json&version=2025&locale=zh_SIM"


def load_names(comparison_job):
    source = comparison_job / "taxonomy-ebird2025-zh-SIM.json"
    records = json.loads(source.read_text(encoding="utf-8-sig"))
    names = {row["sciName"].casefold(): row["comName"] for row in records
             if row.get("category") == "species" and re.search(r"[\u3400-\u9fff]", row.get("comName", ""))
             and "?" not in row["comName"]}
    metadata = {"source": NAME_URL, "taxonomy": "Cornell eBird/Clements v2025", "locale": "zh_SIM",
                "sourceSha256": sha256(source), "scientificNameMatching": "casefold exact; no inferred synonyms, splits or lumping",
                "availableChineseSpecies": len(names)}
    return names, metadata


def localize(candidate, names, source):
    scientific = candidate.get("scientificName", "")
    chinese = names.get(scientific.casefold())
    return {**candidate, "commonName": chinese or "中文名待补", "chineseName": chinese,
            "originalName": candidate.get("originalName") or candidate.get("commonName") or candidate.get("className") or scientific,
            "nameSource": source["source"] if chinese else None, "nameStatus": "exact" if chinese else "missing"}


def update_comparison(comparison_job):
    names, source = load_names(comparison_job)
    class_data = json.loads((comparison_job / "birder-classes.json").read_text(encoding="utf-8"))["classes"]
    bird_classes = [row for row in class_data if row["isBird"]]
    source.update(modelBirdClasses=len(bird_classes),
                  modelBirdClassesMatched=sum(row["scientificName"].casefold() in names for row in bird_classes))
    source["modelBirdClassesMissing"] = len(bird_classes) - source["modelBirdClassesMatched"]
    atomic(comparison_job / "chinese-name-source.json", source)
    path = comparison_job / "birder.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    for prediction in report["predictions"]:
        prediction["top5"] = [localize(row, names, source) for row in prediction["top5"]]
        prediction["top3"] = prediction["top5"][:3]
    report["chineseNames"] = source
    atomic(path, report)
    for key in ["huge", "bioclip2"]:
        path = comparison_job / (key + ".json")
        if path.exists():
            report = json.loads(path.read_text(encoding="utf-8"))
            if report["status"] != "completed":
                report.update(status="paused", pauseReason="用户选择先用Birder跑通全量，CPU资源优先；权重与独立文本缓存保留")
                atomic(path, report)
            else:
                for prediction in report["predictions"]:
                    prediction["top5"] = [localize({**row, "isBird": True}, names, source) for row in prediction["top5"]]
                    prediction["top3"] = prediction["top5"][:3]
                report["chineseNames"] = source
                atomic(path, report)
    return source


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(update_comparison(args.comparison.resolve()), ensure_ascii=True))
