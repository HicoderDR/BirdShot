# 分类验鸟与技术优选画廊

旧版 YOLO 检测/分割可能把叶片、树枝当成鸟；这些区域也有可重复的主体纹理，因此“局部纹理高”“同对象组内较好”并不能证明区域是鸟。旧版默认最大对象/跟踪关注对象还可能把背景当主角，忽略同帧实际鸟实例。

已经在真实旧报告的叶片、树枝错误中复现：原图 → 框 → 掩码 → 裁剪坐标对应正确，不是纯 UI 或坐标错位；同图的鸟体裁剪仍存在。另一个缩略图疑似草茎的区域放大后有被遮挡的鸟羽，不把它臆断为非鸟。这些是个案诊断，不是误检率或准确率评测。

## 新版数据不覆盖旧结果

对全部原有 **5504 个实例裁剪**重新本机验鸟，不仅检查旧343个入选项，不再对全量调用懂鸟。原片、原裁剪、旧 `selected` 和旧 JSON 均保持不变。

按用户明确选择，正式修订写入 `revisions/verified-birds-birder-v2`。早期 BioCLIP 小批诊断保留在 `verified-birds-v1`，不作为当前决策。旧343个不能默认为新优选。

## 验鸟不同于识别鸟种

主分类器使用官方 Birder `hieradet_d_small_dino-v2-inat21`，384输入，保留完整 **10,000 生物类的全类别softmax，其中1,486类为Aves**。不是先筛鸟种再归一化，不是江苏专用权重。通过原标签层级识别Aves/非Aves，记录总概率、各自最强候选和竞争差值。

- `bird`：全类Top1为Aves、Aves总概率≥0.50、最强鸟原概率≥0.25、最强鸟减最强非鸟≥0.20、原检测置信度≥0.50。
- `nonBird`：全类Top1非Aves、非鸟总概率≥0.80、最强非鸟原概率≥0.05。
- 其他或失败/未处理：`unknown`，待审。

这套门槛在真实叶片、树枝和同帧真鸟上检查，**不是校准正确概率**。固定分类头对域外/未知物体仍可能过度自信，证据相近留待审，不把最高鸟概率单独作结论。早期BioCLIP广域竞争诊断保留，按用户选择正式全量优先Birder。

中文Top3使用官方eBird/Clements **v2025 / locale=zh_SIM** 的学名精确查表，不机器直译、不猜异名或拆分合并。源数据URL与SHA256保存。Birder的1,486个鸟类学名匹配中文 **1,409**，缺 **77** 个标“中文名待补”，同时保留学名和原分类名。`species.top3`与兼容`top5`保留全10,000类的原概率、`isBird`、`originalName`、`nameSource`和`nameStatus`。审核项若Top3含非鸟，明确标非鸟类别，不隐藏后硬凑三种鸟。

鸟种候选与 `bird/nonBird/unknown` 分开显示。没有真值不输出准确率。江苏地区是用户提供的信息，本版没有偷偷按当地种类缩小竞争范围。

## 逐实例重新选择

原组所有鸟实例都可参与，不再只保留旧关注对象。先过滤已验鸟实例，再做既有 IoU / 运动候选跟踪及严格对象一致性检查；候选匹配相近或断开则待审。

使用同一份组内主体内部 Tenengrad 策略，两张及以上同候选鸟可比较。眼区不参与全量自动判断，背面和无眼不等于失焦。单张仍无经校准绝对合焦阈值，待审。原图触边风险、有效原像素不足也不进入严格优选。不因“没有眼睛”或“多鸟”直接拒绝。

## 技术分层，不构造跨物种绝对清晰度总分

只对 `decision=keep` 且 `birdVerification.status=bird` 的裁剪建立优选索引：

1. 高可用原像素：有效主体像素至少100000，主体框短边至少256像素，未触边。
2. 标准可用原像素：至少10000主体像素、短边至少120像素，未触边。
3. 低像素细节受限：其余有足够可比证据的实例。
4. 原图触边风险：仅作为待审风险，不宣称检测了完整解剖部位。

跨组先按可用原像素层级，再按组的展示轮次避免同连拍刷屏，然后才使用同候选组内相对名次。**不跨鸟种/尺度混排原始Tenengrad，不用物种稀有度，不混合各模型未校准分数。** 这是实用浏览层级，不是科学绝对质量分数；纹理、噪声、姿态、曝光影响仍需人工覆核。

## 本机接口

- `/gallery.json`：版本、真实进度、验鸟状态汇总、新优选项目及技术层级。
- `/gallery/group/gXXXXX.json`：增强后的原组详情，保留每帧/每实例来源、裁剪、掩码、候选姿态与新版验鸟/物种/质量/原因。
- `/legacy/`：旧已完成报告。
- 原 `/status.json`、`/groups.json`、`/group/*`、`/media/*`、`/original/*` 仍可用。

首页由裁剪图墙承载新优选，非鸟/未知仅在审核筛选或详情中，不混入默认优选；懒加载和分页不默认加载原图。

## 执行与核对

```powershell
python scripts\chinese_bird_names.py --comparison .tools\species-comparison-10
python scripts\gallery_birder.py --job $job --output $revision --comparison .tools\species-comparison-10
python scripts\gallery_build.py --job $job --revision $revision --watch
python scripts\verify_gallery.py --job $job --revision $revision
```

小批诊断不改变正式裁剪与旧结果。全量只在同模型/名称来源/策略指纹下恢复缓存，旧BioCLIP证据不当Birder结果。完成后核对5504项无遗漏、非鸟/未知不入优选、严格单张/触边规则、排名、原裁剪哈希，以及真实叶/枝错误和同帧鸟恢复选择机会。验证文件不是人工真值。

## 手机与受限内网访问

默认仍仅回环；用户明确授权后，可用 `batch_culling.py serve --job $job --port 5174 --lan-ip <物理Wi-Fi的RFC1918地址> --lan-prefix 24` 同时监听127.0.0.1和指定内网地址，不监听0.0.0.0。仅允许该子网读取报告、登记JSON和媒体，使用同源相对URL；媒体必须在批次映射白名单。管理POST仍只接受本机回环来源和Host。

只有现有规则未允许访问时才需要新增放行，管理员备用脚本限制为Private、所选本机地址、LocalSubnet、此Python程序和TCP5174；不会删改已有规则。新增规则权限不足不等于手机必被拦截：本机后续只读核查发现当前精确Python程序已有启用的Private TCP入站允许规则，当前Wi-Fi也是Private，因此无需默认再新增规则或提权。既有规则保持不动。备用脚本仍可保留供必要时按本机策略使用，不关闭防火墙、不改全局网络类型、不开放5173/5175或公网。通过本机内网IP的HTTP检查不能代替独立真机、路由或AP隔离检查。

## 原照片拍摄时间与地点

`python scripts\capture_metadata.py --job $job` 只从对应原JPEG读取 DateTimeOriginal、SubSecTimeOriginal、OffsetTimeOriginal 和必要GPS字段，不复制设备序列号等整包EXIF，不用文件修改时间或浏览器位置代替拍摄事实。独立缓存为 `revisions/capture-exif-v1.json`，索引丰富化将同一照片的 `capture` 复用到所有实例及组详情；不重跑Birder。

相机时区未知就保持 `timeZone:null`、不附加伪UTC/Z。有效GPS度分秒按N/S/E/W转换并校验有限数值和经纬范围，保留WGS84；缺失是ready+null，损坏是error。当前另外完整只读核查了登记配对RAW、JPEG的IPTC/XMP及明确配对旁文件；来源、工具与原片不变验证见[位置来源](location-sources.md)。用户提供的地区只能作参考，不补成EXIF坐标。

地图不默认加载瓦片或调用反向地理编码；只有有效GPS时才提供明确点击后打开的OpenStreetMap WGS84外链，不把WGS84当GCJ02。真实本批1644张JPEG均有原始拍摄时间和时区、GPS为0、读取错误0，因此所有卡片地点如实显示未记录，不生成地图标点。
