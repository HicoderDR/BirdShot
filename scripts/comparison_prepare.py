# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed, traceable local species-comparison assets. No private image uploads."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import time
import urllib.request

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ["BIRDSHOT_MODEL_CACHE"]) if os.environ.get("BIRDSHOT_MODEL_CACHE") else ROOT / ".tools" / "species-models"
MODELS = {
    "huge": {"repo": "imageomics/bioclip-2.5-vith14", "revision": "6e3d04e3d6522012c88181085c5ae666e14c45cd",
             "architecture": "ViT-H-14", "size": 3944517804,
             "sha256": "ac2e37c2f89ef8e6b889176a9a3f418970ad9db15a218bd29e3321e95c46ae97"},
    "bioclip2": {"repo": "imageomics/bioclip-2", "revision": "2957b322090f9cb17ae72c71981c7218a28d81e0",
                 "architecture": "ViT-L-14", "size": 1710517724,
                 "sha256": "b7b2bf6fbc95799e42630e394cf95803892ab447c1a8ab629dbc82fbeaf7dfef"},
}
TAXONOMY_URL = "https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=csv&version=2025"


def atomic(path, value):
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for attempt in range(10):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.05)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "BirdShot-local-comparison/1.0"}), timeout=120) as response:
        temp = target.with_suffix(target.suffix + ".part")
        with temp.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        temp.replace(target)


def prepare(job, source_job=None):
    job.mkdir(parents=True, exist_ok=True)
    if not (job / "samples.json").exists():
        if source_job is None:
            raise ValueError("--source-job is required when fixing a new local comparison")
        source_job = source_job.resolve(strict=True)
        candidates = json.loads((ROOT / ".tools" / "comparison-candidates.json").read_text(encoding="utf-8"))
        indices = [1, 4, 5, 7, 13, 18, 20, 29, 32, 38]
        reasons = ["背向与头部不可见", "近景侧面与羽毛细节", "枝间侧面小鸟", "近景小型鸟",
                   "浅色侧面小鸟", "浅色水鸟近景", "水面深色水鸟", "低分辨率小目标", "侧面猛禽", "背面水鸟与遮挡"]
        rows = []
        (job / "images").mkdir(exist_ok=True)
        for i, index in enumerate(indices):
            entry = candidates[index]
            source = source_job / entry["crop"]
            if sha256(source) != entry["cropSha256"]:
                raise RuntimeError("Original crop fingerprint differs from the completed job")
            # One content-identical metadata-free RGB JPEG is used by all four systems.
            with Image.open(source) as image:
                rgb = image.convert("RGB")
                if min(rgb.size) < 11 or max(rgb.size) > 8192:
                    raise RuntimeError("Chosen crop violates documented API dimensions")
                buffer = io.BytesIO()
                rgb.save(buffer, "JPEG", quality=95, exif=b"")
                if len(buffer.getvalue()) > 2_000_000:
                    buffer = io.BytesIO()
                    rgb.save(buffer, "JPEG", quality=88, exif=b"")
                if len(buffer.getvalue()) > 2_000_000:
                    raise RuntimeError("Chosen crop exceeds API 2MB limit; no implicit content change")
                destination = job / "images" / f"sample-{i + 1:02d}.jpg"
                destination.write_bytes(buffer.getvalue())
                with Image.open(destination) as check:
                    if check.getexif():
                        raise RuntimeError("Upload comparison image still contains EXIF")
                rows.append({"id": f"sample-{i + 1:02d}", "image": f"images/{destination.name}", "sha256": sha256(destination),
                             "dimensions": list(rgb.size), "bytes": destination.stat().st_size,
                             "selectionReason": reasons[i], "sourceCrop": str(source), "sourceCropSha256": entry["cropSha256"],
                             "sourceRelative": entry["sourceRelative"], "groupId": entry["groupId"], "photoId": entry["photoId"],
                             "instanceId": entry["instanceId"], "cropRect": entry["cropRect"],
                             "preprocessing": "相同像素范围，RGB JPEG95重编码，删除EXIF；四方均使用此同一文件。未上传原片或RAW。"})
        atomic(job / "samples.json", {"fixedAt": datetime.now().isoformat(), "sourceJob": str(source_job),
                                      "selection": "预测前固定；不同10组，人工查看裁剪有效性；不是10物种真值标签",
                                      "regionUserProvided": "中国江苏", "samples": rows})
    count = prepare_taxonomy(job)
    atomic(job / "models.json", MODELS)
    print(json.dumps({"job": str(job), "fixedSamples": 10, "globalBirdCandidates": count}, ensure_ascii=False), flush=True)


def prepare_taxonomy(job):
    job.mkdir(parents=True, exist_ok=True)
    taxonomy = job / "taxonomy-ebird2025.csv"
    if not taxonomy.exists():
        local = ROOT / ".tools" / "ebird2025.csv"
        if local.exists():
            shutil.copyfile(local, taxonomy)
        else:
            fetch(TAXONOMY_URL, taxonomy)
    rows = list(csv.DictReader(io.StringIO(taxonomy.read_text(encoding="utf-8-sig"))))
    species = [r for r in rows if r["CATEGORY"] == "species"]
    if len(species) < 10000:
        raise RuntimeError("Global candidate taxonomy unexpectedly small")
    labels = [{"scientificName": r["SCIENTIFIC_NAME"], "commonName": r["COMMON_NAME"], "code": r["SPECIES_CODE"],
               "family": r["FAMILY_SCI_NAME"], "extinct": r["EXTINCT"]} for r in species]
    atomic(job / "candidates.json", {
        "source": TAXONOMY_URL, "taxonomy": "Cornell eBird/Clements v2025",
        "category": "species (global; including extinct species present in the official list)",
        "rawCsvSha256": sha256(taxonomy), "count": len(labels),
        "template": "a photo of {scientificName}, {commonName}, a bird.",
        "regionFilter": None, "regionNote": "保留全球候选，不作未溯源地域过滤，不猜懂鸟area编码。",
        "labels": labels,
    })
    return len(labels)


def download_model(key):
    spec = MODELS[key]
    directory = CACHE / key
    directory.mkdir(parents=True, exist_ok=True)
    base = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}"
    for name in ["README.md", "open_clip_config.json"]:
        fetch(f"{base}/{name}", directory / name)
    target = directory / "open_clip_model.safetensors"
    if target.exists() and target.stat().st_size == spec["size"] and sha256(target) == spec["sha256"]:
        print(f"{key}: verified cached checkpoint", flush=True)
        return
    part_folder = directory / "parts"
    part_folder.mkdir(exist_ok=True)
    chunk = 32 * 1024 * 1024
    count = math.ceil(spec["size"] / chunk)
    def part(i):
        begin, end = i * chunk, min(spec["size"], (i + 1) * chunk) - 1
        path = part_folder / f"{i:04d}"
        if path.exists() and path.stat().st_size == end - begin + 1:
            return
        for attempt in range(4):
            try:
                request = urllib.request.Request(f"{base}/open_clip_model.safetensors?download=true&chunk={i}",
                                                 headers={"Range": f"bytes={begin}-{end}", "User-Agent": "BirdShot-local-comparison/1.0"})
                with urllib.request.urlopen(request, timeout=180) as response:
                    if response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {begin}-{end}/"):
                        raise RuntimeError("Model host did not honor exact bounded Range request")
                    temp = path.with_suffix(".part")
                    with temp.open("wb") as output:
                        shutil.copyfileobj(response, output, 1024 * 1024)
                    if temp.stat().st_size != end - begin + 1:
                        raise RuntimeError("Incomplete checkpoint chunk")
                    temp.replace(path)
                return
            except (OSError, RuntimeError) as error:
                if attempt == 3:
                    raise RuntimeError(f"{key} download chunk {i} failed: {error}") from error
                time.sleep(2 * (attempt + 1))
    done = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(part, i) for i in range(count)]):
            future.result()
            done += 1
            if done % 10 == 0 or done == count:
                print(f"{key}: {done}/{count} chunks", flush=True)
    with target.with_suffix(".part").open("wb") as output:
        for i in range(count):
            with (part_folder / f"{i:04d}").open("rb") as stream:
                shutil.copyfileobj(stream, output, 8 * 1024 * 1024)
    target.with_suffix(".part").replace(target)
    if sha256(target) != spec["sha256"]:
        raise RuntimeError("Official checkpoint SHA256 mismatch; refusing inference")
    for path in part_folder.iterdir():
        path.unlink()
    part_folder.rmdir()
    print(f"{key}: official checkpoint SHA256 verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path)
    parser.add_argument("--model", choices=list(MODELS))
    parser.add_argument("--source-job", type=Path)
    parser.add_argument("--taxonomy-only", action="store_true", help="Prepare public English/Chinese names without private comparison samples")
    args = parser.parse_args()
    if args.taxonomy_only:
        if not args.job:
            parser.error("--taxonomy-only requires --job")
        count = prepare_taxonomy(args.job)
        chinese = args.job / "taxonomy-ebird2025-zh-SIM.json"
        fetch(TAXONOMY_URL.replace("fmt=csv", "fmt=json") + "&locale=zh_SIM", chinese)
        names = json.loads(chinese.read_text(encoding="utf-8-sig"))
        if not isinstance(names, list) or not any("\u4e00" <= char <= "\u9fff" for row in names for char in row.get("comName", "")):
            raise RuntimeError("Official zh_SIM taxonomy has no valid Chinese names")
        print(f"Prepared public taxonomy only: {count} English species; no samples or inference")
    elif args.job:
        prepare(args.job, args.source_job)
    if args.model:
        download_model(args.model)
