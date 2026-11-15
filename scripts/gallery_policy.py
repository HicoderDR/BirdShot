# SPDX-License-Identifier: AGPL-3.0-only
"""Transparent heuristic gate, separate from closed-set bird species predictions."""
POLICY_VERSION = "birder-fullclass-bird-gate-v2"
THRESHOLDS = {"minimumBirdMass": .5, "minimumTopBird": .25, "minimumCompetingMargin": .20,
              "minimumNonBirdMass": .8, "minimumTopNonBird": .05, "minimumDetectionConfidence": .5}


def bird_state(evidence):
    if evidence and evidence.get("status") == "error":
        return "unknown", "本地Birder分类失败，待审：" + evidence.get("error", "未提供错误")
    if not evidence or evidence.get("status") != "completed":
        return "unknown", "尚未完成本机鸟/非鸟竞争验证，或分类失败；不能沿用旧优选"
    if evidence.get("scoreType") != "birder-fullclass" or evidence.get("classCount") != 10000:
        return "unknown", "不是当前Birder全类别分类版本，旧缓存不作为当前验鸟依据"
    if evidence["top1IsBird"] and evidence["birdMass"] >= THRESHOLDS["minimumBirdMass"] \
            and evidence["maxBird"] >= THRESHOLDS["minimumTopBird"] \
            and evidence["margin"] >= THRESHOLDS["minimumCompetingMargin"] \
            and evidence["detectionConfidence"] >= THRESHOLDS["minimumDetectionConfidence"]:
        return "bird", "Birder完整10000类竞争的Aves证据与检测一致；模型验鸟候选，非专家真值"
    if not evidence["top1IsBird"] and evidence["nonBirdMass"] >= THRESHOLDS["minimumNonBirdMass"] \
            and evidence["maxNonBird"] >= THRESHOLDS["minimumTopNonBird"]:
        return "nonBird", "Birder全类别竞争偏向非鸟类；未筛鸟类重新归一化"
    return "unknown", "完整类别分布或检测证据不足以确认鸟；可能未知或类别覆盖受限，待审"
