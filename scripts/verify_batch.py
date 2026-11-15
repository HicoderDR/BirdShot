# SPDX-License-Identifier: AGPL-3.0-only
"""Verify a completed crop-only job without modifying source photographs."""
import argparse
from collections import Counter
from pathlib import Path

from PIL import Image

from batch_culling import atomic_json, digest, read_json, PHOTO_FORMATS, RAW


def verify(job):
    manifest, state = read_json(job / "manifest.json"), read_json(job / "status.json")
    if state["status"] != "completed":
        raise RuntimeError(f"全量尚未结束：{state['processedPhotos']}/{state['totalPhotos']}，不能声明完成")
    source = Path(manifest["source"]).resolve()
    expected_ids = {p["id"] for p in manifest["photos"]}
    actual_ids, records, selected, counters, expected_images = [], [], [], Counter(), set()
    originals = {r["relative"]: r for p in manifest["photos"] for r in [p["primary"], *p["companions"]]}
    actual_source = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file() and p.suffix.lower() in PHOTO_FORMATS}
    if actual_source != set(originals):
        raise RuntimeError("源目录图像清点集合发生变化，存在新增/遗漏，不能声称当前目录已全量完成")
    for name, record in originals.items():
        path = (source / name).resolve()
        stat = path.stat()
        if not path.is_relative_to(source) or stat.st_size != record["size"] or stat.st_mtime_ns != record["mtimeNs"]:
            raise RuntimeError(f"原片尺寸/修改时间变化：{name}")
    for group in manifest["groups"]:
        result = read_json(job / "groups" / f"{group['id']}.json")
        if result["versions"] != manifest["versions"]:
            raise RuntimeError("组结果版本不一致")
        for frame in result["frames"]:
            actual_ids.append(frame["id"])
            if frame.get("sourceSha256") and digest(source / frame["source"]["primary"]["relative"]) != frame["sourceSha256"]:
                raise RuntimeError("原JPEG内容指纹变化")
            status = frame.get("processingStatus")
            counters["failed" if status == "failed" else "unsupported" if status == "unsupported" else frame["decision"]] += 1
            if group["count"] == 1 and (frame["decision"] != "review" or frame["relative"] is not None or frame.get("selectedCrop")):
                raise RuntimeError("严格单张策略违反：不得自身归一化为保留证据")
            for bird in frame["instances"]:
                expected_images.update([bird["crop"], bird["thumbnail"]])
        records.extend(result["cropMapping"])
        selected.extend(result["selectedCrops"])
        expected_images.update(result["selectedCrops"])
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
        raise RuntimeError("逻辑照片有重复或遗漏")
    mapping = read_json(job / "mapping.json")
    if {r["crop"] for r in mapping["crops"]} != {r["crop"] for r in records}:
        raise RuntimeError("全局JSON映射与组结果不一致")
    for record in records:
        x, y, width, height = record["cropRect"]
        original_width, original_height = record["displayOriginalSize"]
        if x < 0 or y < 0 or width < 1 or height < 1 or x + width > original_width or y + height > original_height:
            raise RuntimeError("裁剪坐标越出原图")
        if digest(job / record["crop"]) != record["cropSha256"]:
            raise RuntimeError("裁剪内容完整性校验失败")
        with Image.open(job / record["crop"]) as image:
            if list(image.size) != record["cropRect"][2:]:
                raise RuntimeError("原像素裁剪尺寸和追溯坐标不一致")
        if record.get("selected"):
            if record["decision"] != "keep" or not record["isFocusInstance"] or digest(job / record["selectedPath"]) != record["cropSha256"]:
                raise RuntimeError("选中裁剪未满足严格策略或复制不完整")
    if set(selected) != {p.relative_to(job).as_posix() for p in (job / "selected").glob("*.jpg")}:
        raise RuntimeError("selected目录有遗漏或额外文件")
    if any(p.suffix.lower() in RAW for p in job.rglob("*") if p.is_file()):
        raise RuntimeError("裁剪专用输出中不应复制RAW原片")
    preview_cache = job / "revisions" / "image-preview-cache-v1"
    public_reference_cache = job / "revisions" / "species-reference-cache-v1"
    actual_images = {p.relative_to(job).as_posix() for p in job.rglob("*")
                     if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
                     and not p.is_relative_to(preview_cache) and not p.is_relative_to(public_reference_cache)}
    if actual_images != expected_images:
        raise RuntimeError("输出存在未映射图像或遗漏裁剪，不能宣称完整")
    if state["processedPhotos"] != len(actual_ids) or state["selectedCrops"] != len(selected) or state["cropCount"] != len(records):
        raise RuntimeError("最终计数不一致")
    if (state["selectedPhotos"], state["reviewPhotos"], state["failedPhotos"], state["unsupportedPhotos"]) != (
        counters["keep"], counters["review"], counters["failed"], counters["unsupported"]):
        raise RuntimeError("分类计数不一致")
    proof = {
        "verified": True, "logicalPhotos": len(actual_ids), "physicalPhotos": len(originals),
        "pairedRaw": manifest["pairedRaw"], "cropCount": len(records), "selectedCrops": len(selected),
        "keep": counters["keep"], "review": counters["review"], "failed": counters["failed"], "unsupported": counters["unsupported"],
        "sourceSizesAndMtimesUnchanged": True, "sourceJpegHashesUnchanged": True,
        "cropHashesAndNativeDimensionsVerified": True, "fullOriginalsCopied": 0, "versions": manifest["versions"],
    }
    atomic_json(job / "verification.json", proof)
    return proof


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.job.resolve(strict=True)), ensure_ascii=False))
