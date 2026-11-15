# SPDX-License-Identifier: AGPL-3.0-only
"""Download small demo assets and export the two local inference models."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

import cv2
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "public" / "culling"
TOOLS = ROOT / ".tools"
EYE_REVISION = "3025db2b961e2e0c611bcb7d13f179a5d5d085f2"
VIDEO_NAME = "004_Pied_kingfisher_at_Queen_Elizabeth_National_Park_Video_by_Giles_Laurent.webm"
VIDEO_URL = f"https://upload.wikimedia.org/wikipedia/commons/5/57/{VIDEO_NAME}"
SOURCE_URL = f"https://commons.wikimedia.org/wiki/File:{VIDEO_NAME}"


def download(url, target):
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    offset = temporary.stat().st_size if temporary.exists() else 0
    headers = {"User-Agent": "BirdShot-local-demo/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    print(f"Downloading {url}", flush=True)
    with urllib.request.urlopen(request, timeout=120) as response:
        resume = offset > 0 and response.status == 206
        expected = int(response.headers["Content-Length"]) if response.headers.get("Content-Length") else None
        received = 0
        with temporary.open("ab" if resume else "wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                received += len(chunk)
        if expected is not None and received != expected:
            raise RuntimeError(f"Incomplete download: {received} of {expected} bytes. Rerun to resume.")
    temporary.replace(target)


def prepare_models():
    models = ASSETS / "models"
    models.mkdir(parents=True, exist_ok=True)
    weights = [
        ("bird-yolo11s-seg", "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s-seg.pt"),
        ("eye", f"https://huggingface.co/synthet/eye-pose-v0/resolve/{EYE_REVISION}/eye_pose_v0.pt"),
    ]
    records = []
    for name, url in weights:
        path = TOOLS / "weights" / f"{name}.pt"
        download(url, path)
        output = models / f"{name}.onnx"
        if not output.exists():
            exported = Path(YOLO(path).export(format="onnx", imgsz=640, opset=17, simplify=False, dynamic=False))
            exported.replace(output)
        records.append({
            "name": name, "source": url, "license": "AGPL-3.0",
            "weightsSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "onnxSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        })
    (models / "provenance.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


def prepare_samples(start, count):
    video = TOOLS / "sample.webm"
    download(VIDEO_URL, video)
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError("Cannot decode the source video.")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        raise RuntimeError("Source video has no valid frame rate.")
    first = round(start * fps)
    capture.set(cv2.CAP_PROP_POS_FRAMES, first)
    folder = ASSETS / "samples" / "kingfisher"
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    tiles = []
    try:
        for offset in range(count):
            decoded, frame = capture.read()
            if not decoded:
                raise RuntimeError(f"Missing source frame {first + offset}; refusing an incomplete sequence.")
            name = f"frame-{first + offset:05d}.jpg"
            target = folder / name
            if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 96]):
                raise RuntimeError(f"Cannot save {target}")
            check = cv2.imread(str(target))
            if check is None or check.shape != frame.shape:
                raise RuntimeError(f"Cannot decode exported frame {name}")
            frames.append({
                "name": name, "url": f"/culling/samples/kingfisher/{name}",
                "frame": first + offset, "time": (first + offset) / fps,
                "width": frame.shape[1], "height": frame.shape[0],
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            })
            if offset % max(1, count // 12) == 0:
                tile = cv2.resize(frame, (480, 270))
                cv2.putText(tile, f"frame {first + offset} / {(first + offset) / fps:.3f}s",
                            (12, 252), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 2)
                tiles.append(tile)
    finally:
        capture.release()
    manifest = {
        "title": "斑鱼狗 · 真实视频连续帧", "kind": "video-frames",
        "notice": "视频连续帧，非相机连拍，无人工质量标签。",
        "author": "Giles Laurent", "source": SOURCE_URL, "download": VIDEO_URL,
        "license": "CC BY-SA 4.0", "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0/",
        "changes": "原视频按连续帧顺序抽取，保持 3840×2160 分辨率，编码为 JPEG（质量 96）。",
        "fps": fps, "frames": frames,
    }
    (ASSETS / "samples" / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    while len(tiles) % 4:
        tiles.append(tiles[-1])
    sheet = cv2.vconcat([cv2.hconcat(tiles[i:i + 4]) for i in range(0, len(tiles), 4)])
    cv2.imwrite(str(TOOLS / "contact-sheet.jpg"), sheet)
    print(f"Decoded {count} consecutive {frames[0]['width']}x{frames[0]['height']} frames at {fps} fps.", flush=True)


def prepare_source():
    ASSETS.mkdir(parents=True, exist_ok=True)
    paths = [
        *sorted((ROOT / "src").rglob("*")),
        ROOT / "package.json", ROOT / "package-lock.json", ROOT / "index.html",
        *ROOT.glob("tsconfig*.json"), ROOT / "vite.config.ts", ROOT / ".oxlintrc.json",
        ROOT / "LICENSE-CULLING.txt", ROOT / "docs" / "culling-demo.md",
        ROOT / "scripts" / "prepare-culling.py", ROOT / "scripts" / "culling-requirements.txt",
        ROOT / "scripts" / "prepare-local-photos.py", ROOT / "scripts" / "local-photos-plugin.ts",
        ROOT / "scripts" / "export-culling-diagnostics.mjs",
        ROOT / "scripts" / "start-culling.ps1",
        ROOT / "scripts" / "batch_culling.py", ROOT / "scripts" / "batch_inference.py",
        ROOT / "scripts" / "batch-policy.mjs", ROOT / "scripts" / "batch-report.html",
        ROOT / "scripts" / "verify_batch.py",
        ROOT / "README.md", ROOT / ".gitignore",
        *ROOT.glob("public/*.js"), *ROOT.glob("public/*.svg"),
        *ROOT.glob("public/models/*.json"), *ROOT.glob("public/models/*.txt"),
        ROOT / "docs" / "model-evaluation.md",
        ROOT / "tests" / "culling.test.ts",
        ROOT / "tests" / "smoke-culling.mjs",
        ROOT / "tests" / "test_batch.py",
        *[path for path in (ROOT / "scripts").iterdir()
          if path.is_file() and path.suffix in {".py", ".mjs", ".ts", ".html", ".ps1", ".txt"}],
        *[path for path in (ROOT / "tests").iterdir()
          if path.is_file() and path.suffix in {".py", ".mjs", ".ts"}],
        *sorted((ROOT / "docs").glob("*.md")),
    ]
    with zipfile.ZipFile(ASSETS / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            if path.is_file():
                archive.write(path, path.relative_to(ROOT))


def prepare_multi_sample():
    url = "https://upload.wikimedia.org/wikipedia/commons/b/b0/Anas_platyrhynchos_male_female.jpg"
    target = ASSETS / "samples" / "mallards" / "pair.jpg"
    download(url, target)
    image = cv2.imread(str(target))
    if image is None:
        raise RuntimeError("Cannot decode the original public mallard photograph.")
    manifest = {
        "title": "绿头鸭雌雄 · 公开多鸟单张示例", "kind": "still-image",
        "notice": "真实多鸟照片，仅一张，非连拍，不参与连拍质量评测。",
        "author": "Richard Bartz", "source": "https://commons.wikimedia.org/wiki/File:Anas_platyrhynchos_male_female.jpg",
        "license": "CC BY-SA 2.5", "licenseUrl": "https://creativecommons.org/licenses/by-sa/2.5/",
        "changes": "原始 JPEG 未改写；界面叠加模型预测的实例掩码和六点姿态。",
        "resultUrl": "/culling/multi-results.json",
        "frames": [{
            "name": "mallards-pair.jpg", "url": "/culling/samples/mallards/pair.jpg",
            "width": image.shape[1], "height": image.shape[0],
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }],
    }
    (ASSETS / "samples" / "multi-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=float, default=4)
    parser.add_argument("--count", type=int, default=36)
    parser.add_argument("--samples-only", action="store_true")
    parser.add_argument("--source-only", action="store_true")
    args = parser.parse_args()
    if not args.source_only:
        if not args.samples_only:
            prepare_models()
        prepare_samples(args.start, args.count)
        prepare_multi_sample()
    prepare_source()
