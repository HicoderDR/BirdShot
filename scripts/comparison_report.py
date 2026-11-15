# SPDX-License-Identifier: AGPL-3.0-only
"""Loopback-only comparison viewer: fixed ten cropped images and sanitized results."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import statistics
import sys
from urllib.parse import urlparse

from comparison_prepare import ROOT


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summary(job):
    sample_data = read(job / "samples.json")
    taxonomy = read(job / "candidates.json")
    reports = {}
    for key in ["huge", "bioclip2", "birder", "dongniao"]:
        path = job / f"{key}.json"
        reports[key] = read(path) if path.exists() else {"status": "pending", "predictions": []}
    for key, report in reports.items():
        report["completedCount"] = sum(p.get("status") == "completed" for p in report["predictions"])
        report["failedCount"] = sum(p.get("status") in {"failed", "uncertain-submission", "no-animal", "poll-timeout"} for p in report["predictions"])
    class_file = job / "birder-classes.json"
    if class_file.exists():
        bird_names = {row["scientificName"].casefold() for row in read(class_file)["classes"] if row["isBird"]}
        for prediction in reports["birder"]["predictions"]:
            coverage = {}
            for other in ["huge", "bioclip2", "dongniao"]:
                item = next((p for p in reports[other]["predictions"] if p["sampleId"] == prediction["sampleId"]), None)
                if item and item.get("top5"):
                    candidate = item["top5"][0].get("scientificName", "")
                    coverage[other] = {"scientificName": candidate, "inBirderHead": candidate.casefold() in bird_names}
            prediction["otherTop1Coverage"] = coverage
            absent = [x["scientificName"] for x in coverage.values() if not x["inBirderHead"]]
            if absent:
                prediction["coverageWarning"] += " 其他模型候选不在Birder固定头内：" + ", ".join(sorted(set(absent))) + "（仅标签覆盖核对，非真值）。"
    agreement = []
    def name(key, sample):
        prediction = next((p for p in reports[key]["predictions"] if p["sampleId"] == sample), None)
        if not prediction or prediction.get("status") != "completed" or not prediction.get("top5"):
            return None
        top = prediction["top5"][0]
        # Only exact scientific names are compared. No guessed synonyms or Chinese names.
        return " ".join(top.get("scientificName", "").casefold().split()) or None
    keys = list(reports)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            pairs = [(sample["id"], name(a, sample["id"]), name(b, sample["id"])) for sample in sample_data["samples"]]
            pairs = [p for p in pairs if p[1] and p[2]]
            agreement.append({"a": a, "b": b, "compared": len(pairs), "same": sum(x == y for _, x, y in pairs),
                              "differentSamples": [sample for sample, x, y in pairs if x != y]})
    three = [(sample["id"], [name(key, sample["id"]) for key in ["huge", "bioclip2", "dongniao"]])
             for sample in sample_data["samples"]]
    comparable = [row for row in three if all(row[1])]
    four = [(sample["id"], [name(key, sample["id"]) for key in keys]) for sample in sample_data["samples"]]
    comparable_four = [row for row in four if all(row[1])]
    timing = {}
    for key, report in reports.items():
        inference = [p["inferenceSeconds"] for p in report["predictions"] if p.get("inferenceSeconds") is not None]
        end_to_end = [p["endToEndSeconds"] for p in report["predictions"] if p.get("endToEndSeconds") is not None]
        timing[key] = {"loadingSeconds": report.get("loadingSeconds"), "textEncodingSeconds": report.get("textEncodingSeconds"),
                       "meanImageInferenceSeconds": statistics.mean(inference) if inference else None,
                       "meanEndToEndSeconds": statistics.mean(end_to_end) if end_to_end else None,
                       "note": "单机不同执行时段的观测，不是受控速度基准；API端到端含网络和轮询，服务内部推理耗时未公开。"}
    return {
        "samples": sample_data, "candidate": {key: value for key, value in taxonomy.items() if key != "labels"},
        "reports": reports, "pairwiseAgreement": agreement,
        "threeWay": {"compared": len(comparable), "same": sum(len(set(names)) == 1 for _, names in comparable)},
        "fourWay": {"compared": len(comparable_four), "same": sum(len(set(names)) == 1 for _, names in comparable_four)},
        "timing": timing,
        "truth": None, "agreementPolicy": "仅学名字面精确一致；未对异名、拆分、合并自行对齐，不是准确率。懂鸟不是人工真值。",
        "notice": "两个BioCLIP使用相同全球11167鸟种和提示词，各自编码文本；用户地区江苏未用于缩小候选。Birder固定分类头单独说明覆盖差异。",
    }


def serve(job, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if self.client_address[0] != "127.0.0.1" or not re.fullmatch(r"(127\.0\.0\.1|localhost)(:\d+)?", host) or (origin and origin != f"http://{host}"):
                self.send_error(403)
                return
            route = urlparse(self.path).path
            if route == "/":
                path = ROOT / "scripts" / "comparison-report.html"
                data, kind = path.read_bytes(), "text/html; charset=utf-8"
            elif route == "/summary.json":
                data, kind = json.dumps(summary(job), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
            elif re.fullmatch(r"/(samples|candidates|models|huge|bioclip2|birder|dongniao)\.json", route):
                path = job / route[1:]
                if not path.exists():
                    self.send_error(404)
                    return
                data, kind = path.read_bytes(), "application/json; charset=utf-8"
            elif re.fullmatch(r"/images/sample-(0[1-9]|10)\.jpg", route):
                data, kind = (job / route[1:]).read_bytes(), "image/jpeg"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=3600" if kind == "image/jpeg" else "no-store")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                print("Comparison client disconnected", file=sys.stderr)
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 32
    print(f"COMPARISON_READY http://127.0.0.1:{port}/", flush=True)
    Server(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5175)
    args = parser.parse_args()
    serve(args.job.resolve(strict=True), args.port)
