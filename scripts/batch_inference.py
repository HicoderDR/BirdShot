# SPDX-License-Identifier: AGPL-3.0-only
"""Native CPU version of the demo's preprocessing, masks and original-pixel metrics."""
import math
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from onnxruntime.capi.onnxruntime_pybind11_state import Fail, InvalidArgument, RuntimeException

ROOT = Path(__file__).resolve().parents[1]
INFERENCE_VERSION = "native-onnx-v1"


def iou(a, b):
    intersection = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(
        0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    union = a[2] * a[3] + b[2] * b[3] - intersection
    return intersection / union if union else 0


def nms(rows, threshold=.45):
    selected = []
    for row in sorted(rows, key=lambda x: x["confidence"], reverse=True):
        if all(iou(row["box"], previous["box"]) < threshold for previous in selected):
            selected.append(row)
    return selected


def encode_mask(pixels):
    flat = pixels.ravel().astype(np.uint8)
    changes = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    runs = np.diff(np.r_[0, changes, flat.size]).tolist()
    if flat[0]:
        runs.insert(0, 0)
    return runs


def decode_mask(mask):
    lengths = np.asarray(mask["runs"], dtype=np.int64)
    if np.any(lengths < 0) or int(lengths.sum()) != mask["width"] * mask["height"]:
        raise ValueError("Invalid model mask run lengths")
    return np.repeat(np.arange(len(lengths)) % 2, lengths).reshape(mask["height"], mask["width"]).astype(np.uint8)


def mask_path(binary, region, scale, pad_x, pad_y):
    height, width = binary.shape
    edges = {}
    def add(x, y, tx, ty):
        edges.setdefault((x, y), []).append((tx, ty))
    for y, x in np.argwhere(binary):
        if y == 0 or not binary[y - 1, x]:
            add(x, y, x + 1, y)
        if x == width - 1 or not binary[y, x + 1]:
            add(x + 1, y, x + 1, y + 1)
        if y == height - 1 or not binary[y + 1, x]:
            add(x + 1, y + 1, x, y + 1)
        if x == 0 or not binary[y, x - 1]:
            add(x, y + 1, x, y)
    paths = []
    while edges:
        start = next(iter(edges))
        key = start
        points = []
        while True:
            points.append(key)
            targets = edges.get(key)
            if not targets:
                raise ValueError("Model mask contour is not closed")
            next_key = targets.pop()
            if not targets:
                del edges[key]
            key = next_key
            if key == start:
                break
        simplified = []
        for i, point in enumerate(points):
            before, after = points[i - 1], points[(i + 1) % len(points)]
            if not (before[0] == point[0] == after[0] or before[1] == point[1] == after[1]):
                simplified.append(point)
        commands = []
        for i, (x, y) in enumerate(simplified):
            ox = max(region[0], min(region[0] + region[2], (x * 640 / width - pad_x) / scale + region[0]))
            oy = max(region[1], min(region[1] + region[3], (y * 640 / height - pad_y) / scale + region[1]))
            commands.append(f"{'L' if i else 'M'}{ox:.1f},{oy:.1f}")
        paths.append("".join(commands) + "Z")
    return "".join(paths)


def prototype_mask(coefficients, prototype, model_box, region, scale, pad_x, pad_y):
    _, height, width = prototype.shape
    logits = np.einsum("c,chw->hw", coefficients, prototype)
    mx = (np.arange(width) + .5) * 640 / width
    my = (np.arange(height) + .5) * 640 / height
    valid_x = (mx >= model_box[0]) & (mx <= model_box[0] + model_box[2]) & (mx >= pad_x) & (mx <= pad_x + region[2] * scale)
    valid_y = (my >= model_box[1]) & (my <= model_box[1] + model_box[3]) & (my >= pad_y) & (my <= pad_y + region[3] * scale)
    binary = ((logits > 0) & valid_y[:, None] & valid_x[None, :]).astype(np.uint8)
    return {
        "width": width, "height": height, "runs": encode_mask(binary), "region": region,
        "scale": scale, "padX": pad_x, "padY": pad_y,
        "path": mask_path(binary, region, scale, pad_x, pad_y),
    }


def masked_metric(bgr, mask=None):
    gray = bgr[:, :, 2].astype(np.float64) * .299 + bgr[:, :, 1] * .587 + bgr[:, :, 0] * .114
    sx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    valid = np.ones(gray.shape, dtype=np.uint8) if mask is None else cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8))
    valid[0, :] = valid[-1, :] = 0
    valid[:, 0] = valid[:, -1] = 0
    count = int(valid.sum())
    if count < (1 if mask is None else 64):
        return None, count
    return float((sx * sx + sy * sy)[valid.astype(bool)].mean()), count


def native_mask(mask, x, y, width, height):
    binary = decode_mask(mask)
    rx, ry, _, _ = mask["region"]
    px = np.floor(((x + np.arange(width) + .5 - rx) * mask["scale"] + mask["padX"]) * mask["width"] / 640).astype(int)
    py = np.floor(((y + np.arange(height) + .5 - ry) * mask["scale"] + mask["padY"]) * mask["height"] / 640).astype(int)
    valid_x = (px >= 0) & (px < mask["width"])
    valid_y = (py >= 0) & (py < mask["height"])
    values = binary[np.clip(py, 0, mask["height"] - 1)[:, None], np.clip(px, 0, mask["width"] - 1)[None, :]]
    return values * valid_y[:, None] * valid_x[None, :]


class NativeDetector:
    def __init__(self, threads=4):
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        models = ROOT / "public" / "culling" / "models"
        self.bird = ort.InferenceSession(str(models / "bird-yolo11s-seg.onnx"), options, providers=["CPUExecutionProvider"])
        self.pose = ort.InferenceSession(str(models / "eye.onnx"), options, providers=["CPUExecutionProvider"])
        cv2.setNumThreads(1)

    def infer(self, image, region, pose=False, threshold=.35):
        rx, ry, rw, rh = region
        scale = min(640 / rw, 640 / rh)
        pad_x, pad_y = (640 - rw * scale) / 2, (640 - rh * scale) / 2
        matrix = np.array([[scale, 0, pad_x - rx * scale], [0, scale, pad_y - ry * scale]], dtype=np.float64)
        canvas = cv2.warpAffine(image, matrix, (640, 640), flags=cv2.INTER_LINEAR, borderValue=(114, 114, 114))
        canvas[:max(0, math.floor(pad_y)), :] = 114
        canvas[min(640, math.ceil(pad_y + rh * scale)):, :] = 114
        canvas[:, :max(0, math.floor(pad_x))] = 114
        canvas[:, min(640, math.ceil(pad_x + rw * scale)):] = 114
        tensor = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
        session = self.pose if pose else self.bird
        try:
            outputs = session.run(None, {session.get_inputs()[0].name: tensor})
        except (Fail, InvalidArgument, RuntimeException) as error:
            raise RuntimeError(f"ONNX执行失败：{error}") from error
        prediction = outputs[0][0]
        if not np.isfinite(prediction).all():
            raise RuntimeError("模型输出包含非有限值，不能作为成功检测")
        if prediction.shape[0] != (23 if pose else 116):
            raise RuntimeError(f"Unexpected model output: {prediction.shape}; no heuristic substitution")
        valid = prediction[4 if pose else 18] >= threshold
        if not pose:
            valid &= prediction[4:84].argmax(axis=0) == 14
        rows = []
        for i in np.flatnonzero(valid):
            cx, cy, w, h = map(float, prediction[:4, i])
            model_box = [cx - w / 2, cy - h / 2, w, h]
            left = max(rx, (model_box[0] - pad_x) / scale + rx)
            top = max(ry, (model_box[1] - pad_y) / scale + ry)
            right = min(rx + rw, (model_box[0] + w - pad_x) / scale + rx)
            bottom = min(ry + rh, (model_box[1] + h - pad_y) / scale + ry)
            if right - left < 2 or bottom - top < 2:
                continue
            points = [{
                "x": float((prediction[5 + k * 3, i] - pad_x) / scale + rx),
                "y": float((prediction[6 + k * 3, i] - pad_y) / scale + ry),
                "confidence": float(prediction[7 + k * 3, i]),
            } for k in range(6)] if pose else []
            rows.append({"box": [left, top, right - left, bottom - top], "confidence": float(prediction[4 if pose else 18, i]),
                         "points": points, "mask": None, "modelBox": model_box, "index": int(i)})
        selected = nms(rows)
        if not pose:
            if len(outputs) != 2 or outputs[1].shape[1] != 32:
                raise RuntimeError("Actual segmentation prototype output missing")
            for row in selected:
                row["mask"] = prototype_mask(prediction[84:116, row["index"]], outputs[1][0], row["modelBox"],
                                             region, scale, pad_x, pad_y)
        return selected

    def detect(self, image, progress):
        height, width = image.shape[:2]
        full = self.infer(image, [0, 0, width, height], threshold=.15)
        scale = 640 / max(width, height)
        local = max(width, height) > 2048 and (not any(b["confidence"] >= .6 for b in full)
                 or any(min(b["box"][2:]) * scale < 48 for b in full))
        candidates = list(full)
        count = 0
        if local:
            def positions(length):
                size = min(2048, length)
                n = math.ceil((length - size) / (size * .75)) + 1
                return [0] if n == 1 else [(length - size) * i / (n - 1) for i in range(n)]
            xs, ys = positions(width), positions(height)
            for y in ys:
                for x in xs:
                    count += 1
                    progress(f"鸟体分割局部 {count}/{len(xs) * len(ys)}")
                    candidates.extend(self.infer(image, [x, y, min(2048, width), min(2048, height)], threshold=.15))
            regions = []
            for bird in sorted(candidates, key=lambda x: x["confidence"], reverse=True):
                bx, by, bw, bh = bird["box"]
                size = min(1536, max(512, max(bw, bh) * 3))
                rw, rh = min(size, width), min(size, height)
                region = [max(0, min(width - rw, bx + bw / 2 - rw / 2)),
                          max(0, min(height - rh, by + bh / 2 - rh / 2)), rw, rh]
                if all(iou(region, previous) < .5 for previous in regions):
                    regions.append(region)
                if len(regions) >= 8:
                    break
            for index, region in enumerate(regions):
                progress(f"小目标精查 {index + 1}/{len(regions)}")
                candidates.extend(self.infer(image, region))
        accepted = []
        for bird in sorted((b for b in candidates if b["confidence"] >= .35), key=lambda x: x["confidence"], reverse=True):
            rx, ry, rw, rh = bird["mask"]["region"]
            x, y, w, h = bird["box"]
            border = 6 / bird["mask"]["scale"]
            clipped = (rx > 0 and x - rx < border) or (ry > 0 and y - ry < border) or (
                rx + rw < width and rx + rw - x - w < border) or (ry + rh < height and ry + rh - y - h < border)
            if not clipped and all(iou(bird["box"], old["box"]) < .4 for old in accepted):
                accepted.append(bird)
        return sorted(accepted, key=lambda b: b["box"][0]), f"全图 + {count} 局部精查" if local else "全图640"

    def analyze_bird(self, image, detection, index):
        height, width = image.shape[:2]
        body = detection["box"]
        bird = {
            "id": index, "trackId": None, "body": body, "bodyConfidence": detection["confidence"],
            "mask": detection["mask"], "maskIssue": None, "posePoints": [], "poseIssue": None,
            "eye": None, "beak": None, "patch": None, "sharpness": None, "eyeIssue": None,
            "bodySharpness": None, "bodyPixels": 0,
        }
        if bird["mask"]["path"]:
            x, y = math.floor(body[0]), math.floor(body[1])
            w, h = min(width - x, math.ceil(body[2]) + 1), min(height - y, math.ceil(body[3]) + 1)
            bird["bodySharpness"], bird["bodyPixels"] = masked_metric(image[y:y + h, x:x + w], native_mask(bird["mask"], x, y, w, h))
            if bird["bodySharpness"] is None:
                bird["maskIssue"] = "分割内有效原像素不足"
        else:
            bird["maskIssue"] = "模型没有有效分割掩码，不用检测框代替"
        margin = max(body[2:]) * .1
        x, y = max(0, body[0] - margin), max(0, body[1] - margin)
        region = [x, y, min(width, body[0] + body[2] + margin) - x, min(height, body[1] + body[3] + margin) - y]
        matching = [p for p in self.infer(image, region, pose=True) if iou(p["box"], body) > .3]
        if len(matching) != 1:
            bird["poseIssue"] = "姿态没有唯一可靠匹配"
            bird["eyeIssue"] = "眼部未可靠定位；主体指标独立有效"
            return bird
        points = matching[0]["points"]
        bird["posePoints"] = [{**point, "index": k} for k, point in enumerate(points)]
        eyes = sorted([points[1], points[2]], key=lambda p: p["confidence"], reverse=True)
        eye = bird["eye"] = eyes[0]
        beak = bird["beak"] = points[0] if points[0]["confidence"] >= .5 else None
        if min(body[2:]) < 90:
            bird["eyeIssue"] = "主体原像素不足以判断眼部"
        elif eye["confidence"] < .65:
            bird["eyeIssue"] = "候选眼点置信度不足"
        elif .2 < eyes[1]["confidence"] < .65:
            bird["eyeIssue"] = "左右眼可见性矛盾，可能误定位"
        elif not (body[0] <= eye["x"] <= body[0] + body[2] and body[1] <= eye["y"] <= body[1] + body[3]):
            bird["eyeIssue"] = "候选眼点在鸟体外"
        if bird["eyeIssue"]:
            return bird
        distance = math.hypot(beak["x"] - eye["x"], beak["y"] - eye["y"]) if beak else body[2] * .08
        radius = math.floor(max(8, min(32, distance * .32)) + .5)
        x, y = math.floor(eye["x"] + .5) - radius, math.floor(eye["y"] + .5) - radius
        size = 2 * radius + 1
        if x < 0 or y < 0 or x + size > width or y + size > height:
            bird["eyeIssue"] = "候选区域无法完整测量"
            return bird
        bird["patch"] = [x, y, size, size]
        bird["sharpness"], _ = masked_metric(image[y:y + size, x:x + size])
        if not beak:
            bird["eyeIssue"] = "喙点不足以比较头部方向"
        return bird

    def analyze(self, image, photo_id, group_id, progress=lambda message: None):
        height, width = image.shape[:2]
        birds, mode = self.detect(image, progress)
        result = {
            "id": photo_id, "group": group_id, "width": width, "height": height,
            "body": None, "bodyConfidence": None, "birdCount": len(birds), "eye": None, "beak": None,
            "patch": None, "sharpness": None, "issue": None, "decision": "review", "reason": "尚未比较",
            "relative": None, "rank": None, "cohortSize": 0, "instances": [], "selectedInstance": None,
            "bodySharpness": None, "bodyPixels": 0, "basis": None, "detectionMode": mode,
        }
        for i, detection in enumerate(birds):
            progress(f"鸟实例 {i + 1}/{len(birds)}：原像素指标与六点候选姿态")
            result["instances"].append(self.analyze_bird(image, detection, i + 1))
        if not birds:
            result["issue"] = result["reason"] = "全图与局部仍未可靠检出鸟体，不等于失焦"
        return result
