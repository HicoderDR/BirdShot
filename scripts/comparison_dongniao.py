# SPDX-License-Identifier: AGPL-3.0-only
"""Exactly-once image submission ledger; credentials only from runtime environment."""
import argparse
from datetime import datetime
import io
import json
import os
from pathlib import Path
import time

from PIL import Image
import requests

from comparison_prepare import atomic, sha256

ENDPOINT = "https://ai.open.hhodata.com/api/v2/dongniao"


def clean(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, list):
        return [clean(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: clean(item, secret) for key, item in value.items() if key.lower() not in {"api_key", "authorization", "key"}}
    return value


def run(job):
    key = os.environ.get("DONGNIAO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DONGNIAO_API_KEY is not available in the runtime environment; no API call made")
    source = json.loads((job / "samples.json").read_text(encoding="utf-8"))
    samples = source["samples"]
    path = job / "dongniao.json"
    if path.exists():
        report = json.loads(path.read_text(encoding="utf-8"))
    else:
        report = {"name": "懂鸟 API v2", "status": "running", "endpoint": ENDPOINT, "modelVersion": "服务未公开",
                  "regionParameter": None, "regionNote": "文档仅要求已知地区代码，无可靠江苏映射，不猜编码",
                  "submissionCount": 0, "pollCount": 0, "maxSubmissions": 10, "predictions": [],
                  "scoreMeaning": "服务原始score，未假定与BioCLIP概率或cosine同标度；非人工真值"}
        atomic(path, report)
    session = requests.Session()
    session.headers["api_key"] = key
    # No automatic upload retries: uncertain receipt is terminal until a saved resultid is available.
    for sample in samples:
        record = next((r for r in report["predictions"] if r["sampleId"] == sample["id"]), None)
        if record and record["status"] in {"completed", "failed", "uncertain-submission", "no-animal"}:
            continue
        begin = time.perf_counter()
        if record is None:
            if report["submissionCount"] >= 10:
                raise RuntimeError("Submission limit reached, no additional image request made")
            image_path = job / sample["image"]
            if sha256(image_path) != sample["sha256"]:
                raise RuntimeError("Fixed sample image changed")
            with Image.open(image_path) as image:
                if image.getexif() or min(image.size) < 11 or max(image.size) > 8192 or image_path.stat().st_size > 2_000_000:
                    raise RuntimeError("Upload dimensions/metadata/size do not meet fixed sample requirements")
            record = {"sampleId": sample["id"], "imageSha256": sample["sha256"], "status": "uncertain-submission",
                      "resultid": None, "submissionStartedAt": datetime.now().isoformat(), "polls": 0, "responses": []}
            report["predictions"].append(record)
            report["submissionCount"] += 1
            atomic(path, report)  # Write intent before upload; never blindly resubmit after an interruption.
            timer = time.perf_counter()
            try:
                with image_path.open("rb") as image_file:
                    response = session.post(ENDPOINT, data={"upload": "1", "class": "B"},
                                            files={"image": (sample["id"] + ".jpg", image_file, "image/jpeg")},
                                            timeout=(20, 90))
                record["submissionSeconds"] = time.perf_counter() - timer
                payload = response.json()
                record["responses"].append({"http": response.status_code, "body": clean(payload, key)})
                if response.status_code != 200 or not isinstance(payload, list) or len(payload) != 2:
                    record.update(status="failed", error="API HTTP/protocol failure; no resubmission")
                    atomic(path, report)
                    if response.status_code in {401, 403, 503}:
                        report.update(status="blocked", error="鉴权/额度/限流错误；停止后续识别提交，未猜测或重试")
                        atomic(path, report)
                        return
                    continue
            except (requests.RequestException, ValueError):
                record.update(error="Submission response not confirmed; request not retried")
                atomic(path, report)
                continue
            code, value = payload
            if code == 1000 and isinstance(value, str):
                record.update(resultid=value, status="polling")
            elif code == 1000 and isinstance(value, list):
                record.update(status="completed", rawResult=clean(value, key))
            elif code == 1010:
                record.update(status="no-animal", error="API did not identify an animal")
            else:
                record.update(status="failed", error=f"API business code {code}")
                if code == 1007:
                    report.update(status="blocked", error="API quota/rate limit; stopped remaining submissions")
                    atomic(path, report)
                    return
            atomic(path, report)
        if record.get("resultid") and record["status"] == "polling":
            # Resume the same resultid without another image submission.
            for attempt in range(record["polls"], 20):
                time.sleep(2 if attempt < 5 else 4)
                record["polls"] += 1
                report["pollCount"] += 1
                atomic(path, report)
                try:
                    response = session.post(ENDPOINT, data={"resultid": record["resultid"]}, timeout=(20, 45))
                    payload = response.json()
                except (requests.RequestException, ValueError):
                    record["lastPollError"] = "Poll transport/protocol error; image not resubmitted"
                    atomic(path, report)
                    continue
                record["responses"].append({"http": response.status_code, "body": clean(payload, key)})
                if response.status_code != 200 or not isinstance(payload, list) or len(payload) != 2:
                    record.update(status="failed", error="Poll HTTP/protocol failure")
                    break
                code, value = payload
                if code == 1000 and isinstance(value, list):
                    record.update(status="completed", rawResult=clean(value, key))
                    break
                if code == 1010:
                    record.update(status="no-animal", error="API did not identify an animal")
                    break
                if code != 1001 or value != record["resultid"]:
                    record.update(status="failed", error=f"Poll business code {code}")
                    break
            else:
                record.update(status="poll-timeout", error="Bounded polling expired; saved resultid, image not resubmitted")
        if record["status"] == "completed":
            objects = record.get("rawResult", [])
            record["objects"] = []
            for obj in objects:
                predictions = []
                for item in obj.get("list", [])[:5]:
                    if len(item) < 4:
                        continue
                    names = str(item[1]).split("|")
                    predictions.append({"score": item[0], "chineseName": names[0] if names else "",
                                        "commonName": names[1] if len(names) > 1 else "",
                                        "scientificName": names[2] if len(names) > 2 else "",
                                        "id": item[2], "class": item[3]})
                record["objects"].append({"box": obj.get("box"), "top5": predictions})
            record["top5"] = record["objects"][0]["top5"] if record["objects"] else []
            record["top1ObjectPolicy"] = "API返回的第一个目标；完整目标列表一并保存，未假定服务排序为可靠主体"
        record["endToEndSeconds"] = time.perf_counter() - begin
        atomic(path, report)
        print(f"dongniao {sample['id']}: {record['status']}; submissions={report['submissionCount']} polls={report['pollCount']}", flush=True)
    report.update(status="completed" if all(r["status"] in {"completed", "no-animal", "failed", "uncertain-submission"} for r in report["predictions"])
                  and len(report["predictions"]) == 10 else "partial", finishedAt=datetime.now().isoformat())
    atomic(path, report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    run(args.job.resolve())
