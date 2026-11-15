// SPDX-License-Identifier: AGPL-3.0-only
import { readFile, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'

const artifacts = resolve(process.env.BIRDSHOT_ARTIFACT_DIR ?? '.tools')
const rows = JSON.parse(await readFile(resolve('.tools', 'local-coverage.json'), 'utf8'))
const sample = JSON.parse(await readFile(resolve('public', 'culling', 'results.json'), 'utf8'))
const multi = JSON.parse(await readFile(resolve('public', 'culling', 'multi-results.json'), 'utf8'))
const sum = (key, selected = rows) => selected.reduce((n, row) => n + row[key], 0)
const decisions = (key) => rows.reduce((n, row) => n + row.counts[key], 0)
const baselineGroups = rows.filter((row) => ['local-25', 'local-29', 'local-5'].includes(row.group))
const anonymized = rows.map((row, index) => ({ ...row, group: `组 ${String.fromCharCode(65 + index)}` }))
const bodyOnly = rows.reduce((n, row) => n + row.basisCounts.body, 0)
const eye = rows.reduce((n, row) => n + row.basisCounts.eye, 0)
const markdown = `# 鸟类筛片修复版：真实运行诊断

修复版 **2026-11-14.4**，本机体验：<http://127.0.0.1:5173/cull>。

## 已复现的问题

旧版确实使用官方 YOLO11n nano ONNX，不是没有启用 YOLO：640×640 letterbox，COCO bird 类 14，检测阈值 0.35，无下载失败后的启发式替代。原版三个真实候选组共 16 帧，仅 4 帧检出鸟体、1 帧有可测眼区、0 帧参与比较。大原图缩小造成远鸟像素损失；单鸟、必须有眼和至少三帧的门槛又阻断了正常多鸟 / 无眼照片。

## 修复及实际结果

换为官方 **YOLO11s-seg**，使用真实 32 通道、160×160 实例掩码原型；640 全图结合 2048 原像素局部、512–1536 候选精查。最终可靠实例阈值仍为 0.35，0.15 只用于待精查候选。掩码与框逐实例映射回原图，提供半透明叠加、轮廓、编号、置信度和对象切换。

多鸟不再整体拒绝。没有眼睛仍显示分割、候选姿态和明确命名的**分割主体内部原像素纹理梯度**，不会冒充眼部合焦。眼区默认不参与推荐，须人工确认眼睛可见及候选点正确后再启用。至少两张同对象帧可比较；主体指标和两帧比较均不自动淘汰。候选跟踪和人工选择避免盲目混合不同鸟，身份仍须覆核。

对原版相同的 ${sum('frames', baselineGroups)} 帧，修复版有鸟体 ${sum('bodyFrames', baselineGroups)} 帧、主体指标 ${sum('bodyMetricFrames', baselineGroups)} 帧、参与比较 ${sum('comparableFrames', baselineGroups)} 帧。额外包括真实两帧组，合计只读验证 ${sum('frames')} 张 JPEG：

| 匿名候选组 | 帧数 | 鸟体检出帧 | 分割实例 | 有候选姿态点实例 | 模型候选眼区帧 | 可测主体帧 | 可比帧 | 保留候选 / 待审 / 淘汰 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
${anonymized.map((row) => `| ${row.group} | ${row.frames} | ${row.bodyFrames} | ${row.masks}/${row.instances} | ${row.poseInstances} | ${row.eyeFrames} | ${row.bodyMetricFrames} | ${row.comparableFrames} | ${row.counts.keep} / ${row.counts.review} / ${row.counts.reject} |`).join('\n')}

合计：**${decisions('keep')} 张保留候选、${decisions('review')} 张待审、${decisions('reject')} 张建议淘汰**；${bodyOnly} 帧比较依据为主体内部纹理，${eye} 帧依据为眼区。这里是实际运行覆盖率，不是有人工真值的准确率；检出的实例可能包括误检，须看原图。

## 姿态和分割的真实支持范围

[eye-pose-v0 的固定版本定义](https://huggingface.co/synthet/eye-pose-v0/blob/3025db2b961e2e0c611bcb7d13f179a5d5d085f2/wildlife_bird.yaml)只有喙、左眼、右眼、头顶、左肩、右肩。只显示高置信度候选点，低置信度 / 眼可见性矛盾明确标为不确定；没有通用翅膀、尾巴或人体骨架连线。高置信度也可能误定位，CUB 训练不能保证野外场景泛化。

真实掩码是模型原型输出，不是框 / 椭圆填充。160×160 原型在高分辨率图上的轮廓较粗，不代表精细羽毛真值。极远、运动模糊、遮挡仍可能检不出或无法跨帧匹配；不足时给出该阶段原因，不当成失焦。

## 公开可视化与隐私

公开连续鸟帧实测 ${sample.results.length} 张；公开雌雄绿头鸭原图实际分割 ${multi.results[0].instances.length} 个实例，可切换并开关掩码 / 姿态。公开数据分别来自 Giles Laurent（视频，CC BY-SA 4.0）和 Richard Bartz（单张，CC BY-SA 2.5）；单张多鸟图不是连拍。

个人原片只读并校验哈希，未上传、改写、移动或提交 Git。本摘要只包含匿名汇总，不含私人图像、文件名、拍摄时间或目录路径。公开示例没有合焦 / 选片真值，不声称算法已通过准确率评测。
`
await writeFile(join(artifacts, 'birdshot-diagnostics.md'), markdown)
await writeFile(join(artifacts, 'birdshot-coverage.json'), JSON.stringify(anonymized, null, 2))
console.log(JSON.stringify({ frames: sum('frames'), bodyFrames: sum('bodyFrames'), masks: sum('masks'), comparable: sum('comparableFrames'), keep: decisions('keep'), review: decisions('review'), reject: decisions('reject') }))
