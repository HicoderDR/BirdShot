# 十张本地裁剪的识别对比

所有图片均在本地预测前固定，清单含 SHA-256、原组/原文件、实例编号和裁剪坐标。使用不同十个拍摄组、近景/背面/小目标等，不等于十种已知物种；没有人工物种真值。

四方使用同一份去 EXIF 的 RGB JPEG 裁剪，不上传原照片、RAW 或位置元数据。只有这十张按用户授权送懂鸟，后续全量验鸟不调用懂鸟。私人报告仅在本机 loopback 展示，不提交 Git 或作为云附件。

## 两个 BioCLIP

- BioCLIP 2.5 Huge：官方 `imageomics/bioclip-2.5-vith14`，ViT-H/14，固定 revision `6e3d04e3d6522012c88181085c5ae666e14c45cd`，约 3.94 GB safetensors，MIT。
- BioCLIP 2：官方 `imageomics/bioclip-2`，ViT-L/14，固定 revision `2957b322090f9cb17ae72c71981c7218a28d81e0`，约 1.71 GB safetensors，MIT。
- 下载后核对 Hugging Face 官方 LFS SHA-256。CPU FP32 实际运行，不改用小模型。相同 224 预处理，但官方图像编码器/文本编码器各自独立，Huge 不复用 BioCLIP 2 文本特征。
- Cornell eBird/Clements 官方 API `version=2025`，过滤 `CATEGORY=species` 得 **11,167** 个全球鸟种，包括清单里的灭绝种；原 CSV 与哈希、实际候选、提示词持久化。提示为 `a photo of {scientificName}, {commonName}, a bird.`。
- 用户明确拍摄地区中国江苏，但本轮两个模型保留相同全球候选，没有未经溯源地缩小到江苏或中国名单，也没有从这十张的预测反构候选。
- 文本移除 EOS 后因果不可见的尾部 padding，权重与有效 token 不变，运行时对完整 77 token 编码做数值等价验证。FP32、优化方法、差异和实际耗时一并记录。

## Birder

使用官方 `birder-project/hieradet_d_small_dino-v2-inat21`，固定 revision `cbd6a5e2e2647374d8f1c66a3a515560b2d8fa4b`，384 输入、DINOv2 预训练后在 iNaturalist 2021 微调。官方权重和代码许可 Apache-2.0，已核验官方 SHA-256。

分类头是全球 iNat2021 的 **10,000 个生物类，其中 1,486 鸟类**，不是欧洲/以色列地域头，也不是穷尽全球/江苏鸟种。保留完整 10,000 类的原始 softmax，不暗中只取鸟类重新归一化。分类头没有的鸟会被迫选成别的类，不能把近似种当正确结果；逐项报告其他模型 Top1 学名是否在此固定头内，缺失就是“范围外（相对该候选）”，不是人工真值判断。学名从官方完整分类标签提取，原标签同时保存；英文名仅在同一学名与 eBird 精确匹配时显示，不臆造中文名。

## 懂鸟

官方接口 `POST https://ai.open.hhodata.com/api/v2/dongniao`，`api_key` 仅从 `DONGNIAO_API_KEY` 运行时环境读取。图片 multipart 字段为 `image`、`upload=1`、`class=B`，无可靠江苏 area 代码映射，因此没有猜测地区参数。

识别提交意图先写入不含凭据的持久日志，再上传；每图最多一次，全部最多十次。不确定是否被接收时不会重传；保存 resultid，有界轮询。解析业务 `[code,payload]`，不能将 HTTP200 当成功；异常、额度、无目标分别记录。报告保留经凭据字段/回显清理的响应，提交数与查询数分开显示。

## 解读和运行

报告展示每张四方 Top1/最多 Top5、原始 cosine/logit/softmax/API score、模型加载时间、全球文本准备时间、单图预处理/推理/端到端时间。各分数不同标度且不是校准正确率。学名一致仅按字面精确匹配，不自行猜异名，不把懂鸟当真值，不输出准确率优劣。

```powershell
.\.tools\venv\Scripts\python.exe -m pip install -r scripts\comparison-requirements.txt
.\.tools\venv\Scripts\python.exe scripts\comparison_local.py --job .tools\species-comparison-10 --model bioclip2
.\.tools\venv\Scripts\python.exe scripts\comparison_local.py --job .tools\species-comparison-10 --model huge
.\.tools\venv\Scripts\python.exe scripts\comparison_birder.py --job .tools\species-comparison-10
.\.tools\venv\Scripts\python.exe scripts\comparison_report.py --job .tools\species-comparison-10 --port 5175
```

懂鸟已成功的日志不可删除后重跑，以免重复消耗额度。设置用户级环境变量后，新进程须读取 User 范围再注入环境，密钥不写命令参数、源代码、文件或前端。模型缓存与私人结果都在 `.tools`，不提交。
