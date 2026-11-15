# 本机优选鸟体图墙

`scripts\gallery-report.html` 是独立、无构建依赖的新版入口。旧版 `scripts\batch-report.html`、推理与选择策略均不改动。后端需将新版文件作为 `/` 提供，旧版报告保留在 `/legacy/`；新入口缺失索引时明确报错，不降级旧版保留项。

所有 API、派生媒体和原图均使用相对同源路径，不硬编码回环地址；同一入口可以通过后端开放的本地内网地址在手机浏览。监听地址、LAN 访问控制和防火墙由后端负责，前端不创建服务、不把访问范围扩大到公网。浏览器不直连第三方字体、图片、脚本、图床或模型；后端仅按需以公开学名查询百科/许可参考，不发送照片或位置。分享 URL 不携带 APIKey。完整画面先按需请求本地分档预览；只有放大确需原分辨率或明确保存原文件时才请求 `/original/{frameId}`，不跳转新页面。不要将个人照片、真实截图或输出目录提交到 Git 或发布附件。

## 已确认的 v1 合同

`GET /gallery.json` 返回以下结构。**下面是模拟数据，不是真实验鸟或质量结果**：

```json
{
  "version": 1,
  "status": "processing",
  "updatedAt": "2026-11-15T04:00:00Z",
  "progress": { "processed": 3, "total": 5504 },
  "summary": {
    "preferred": 1, "bird": 1, "nonBird": 1, "unknown": 1,
    "oldSelected": 2, "removedOld": 1, "addedNew": 0
  },
  "items": [{
    "id": "simulation-only-p000001-i0",
    "groupId": "g00001",
    "frameId": "p000001",
    "instanceId": 0,
    "crop": "crops/simulation-only.jpg",
    "thumbnail": "crops/simulation-only-thumb.jpg",
    "sourceLabel": "模拟样本.jpg",
    "decision": "keep",
    "reason": "模拟判据，不是真实验鸟结果",
    "birdVerification": {
      "status": "bird", "model": "仅合同示例",
      "evidence": "仅用于说明字段；不应作为真实结果展示"
    },
    "species": {
      "top3": [{
        "scientificName": "Simulation only", "commonName": "模拟鸟种", "englishName": "Simulated bird",
        "cosine": 0.3, "probability": null, "isBird": true,
        "nameSource": "仅合同示例", "nameStatus": "模拟名称"
      }],
      "coverage": "模拟候选覆盖说明"
    },
    "quality": {
      "tier": "good", "tierLabel": "模拟技术等级",
      "withinGroupRelative": 0.91, "withinGroupRank": 1, "cohortSize": 3,
      "bodyPixels": 12000, "bodyTenengrad": 800, "edgeRisk": "模拟完整性依据",
      "representative": true
    },
    "rank": 1
  }]
}
```

- `status` 为 `processing | completed | error`，schema `version` 为数字 1，模型 / 分析版本另保存在 `analysisVersion` 数据中，不占用精简界面；不把旧版 `running` 或版本字符串默认为成功。`progress` 单位统一为候选。处理中的有效索引每 10 秒刷新；完成与错误后只手动刷新。首次加载、空结果、API 失败、无效版本与处理中没有合格项都单独呈现。
- `summary` 中七个计数为非负整数；`preferred` 必须等于 `items` 中 `decision === "keep" && birdVerification.status === "bird"` 的项数。可选 `pending` 单独表示未处理候选，此时 `unknown` 只统计已处理拒识；缺少 `pending` 时，前端由 `total - processed` 明示待处理量，并把兼容的 `unknown` 标为“未知/未确认”，不暗称所有候选均已处理。其余计数可覆盖尚未全部写入 `items` 的已处理候选，UI 不用它们伪造卡片。明确的待处理 / 失败状态或缺少 evidence 且原验证理由说明“尚未完成”等时简写“待识别”，其余未知简写“待确认”；筛选仍区分未完成与已处理未知，原证据保持在数据中。
- `items` 可以包含非鸟、未知以及已验鸟但未保留项，统一放在“独立审核”。`birdVerification.status` 为 `bird | nonBird | unknown`，`decision` 为 `keep | review | reject`。缺少明确验鸟状态的索引被拒绝，不能把旧版检测当分类。
- 仅优选项强制要求 `rank` 为有效正数，数值越小越优先；审核项可为 `null` 或缺失，显示“未排名”，任何排序方向都将它们稳定放在已排名项后，不把 `null` 当零、不生成质量名次。同名次和未排名项保留服务端顺序。反向浏览只反转有效名次，不新增评分。`quality.tier` / `tierLabel` 不硬编码等级语义；可选指标缺失时显示未提供 / 未判定，不补零。
- 用户已选择 Birder 作为检测后裁剪分类器；前端不下载模型、不训练、不翻译鸟名。图墙和详情优先显示 `species.top3`，缺少该数组时兼容 `species.top5.slice(0, 3)`；不展示第四、第五候选，也不补齐缺项。精简候选列表仅显示 `commonName` 规范中文、`englishName` 常用英文和 `probability` 原分类概率；缺名分别标“中文名待补”“英文名待补”，不把 `scientificName` 拉丁学名或 `originalName` 分类 ID 当英文。学名、原始类别、名称来源 / 状态、余弦、模型和类别覆盖仍原样保留在数据中，不在可见候选列表展示。明确 `isBird: false` 的分类显示“非鸟类别”，不冒充鸟种。概率不按 Top3 重新归一化，也不是正确率或技术质量分；界面只保留一条“识别结果仅供参考”。
- `id` 唯一，`groupId` 沿用 `g00001` 格式，`frameId` / `instanceId` 必须精确对应组详情。实例 ID 支持数字或字符串。`reason` 非空，说明后端入选 / 未选依据。
- `crop` / `thumbnail` 为已有 `/media/` 根下 JPEG / PNG 的相对路径，不能带根路径、反斜线、目录穿越或 URL。路径每段分别编码，文件名支持空格、中文、`#`、`?` 等字符的百分号编码传输；合同中的路径应为未编码的相对文件路径，页面统一编码。

`GET /gallery/group/{groupId}.json` 返回增强组 JSON，保留现有 `frames[].id/source/instances`。每个实例保留 `id/crop/thumbnail/cropRect/bodyConfidence/bodySharpness/bodyPixels/mask/posePoints/trackId/eyeIssue/poseIssue`，并可提供与索引同形的 `birdVerification/species/quality/decision/reason`（或 `selectionReason`）。同实例索引的新版判定优先；其他实例缺少新版分类时简写“未识别”，不继承旧版帧级选择。索引对应实例缺失时明确报错，不替换为组内另一只鸟。

掩码直接使用既有 `mask.path`，以原像素 `cropRect` 对齐；姿态沿用当前 `src\culling\CullingApp.tsx` 六点：喙、左右眼、头顶、左右肩部，仅绘制置信度至少 0.65 的点，不画骨架、不声称眼睛可见。“分割轮廓”“姿态点”采用原生 checkbox + `role=switch` 的独立开关外观，默认都关闭；键盘与屏幕阅读器可用，44px 标签触控区，切换实例 / 重新打开详情时归零，坐标无效时停用。

## 沉浸式详情与公开参考

页头为 `minmax(0,1fr) + 44px` 单行布局，鸟名/实例是主标题，原文件为次要行；轻量SVG关闭按钮无重边框/底色，手机不会被挤到第二行。切实例同步标题，右栏不再重复鸟名。轮廓、姿态、全图三个独立控件位于图片底部半透明浮层，启用为强调色，不触发图片viewer。

原“连拍对比”改称**鸟体对比**，按原组 `frames[].instances` 的实际实例总数决定：0/1实例整块隐藏；单帧多实例、多帧多实例显示；多帧只有一个实例仍隐藏，不以未检出的原图冒充比较鸟体。每个候选保留来源照片及实例标识。跨鸟切换是浏览，不表示同鸟身份或可直接做相对清晰度比较，不改变后端判定/cohort。

详情Top3为三列参考图卡，按原概率降序，概率越高强调色越重，原值不重归一化。只在详情请求前三个唯一鸟类学名，切实例/关闭即中止并忽略迟到结果；图墙不预抓整库。非鸟和缺名/缺图候选仍保留真实名称/概率，图片是物种示意，不是本次照片的确认。

`GET /species-reference?scientificName=...` 只接受本批实际鸟类候选，返回：

```json
{
  "status": "ready",
  "scientificName": "Copsychus saularis",
  "article": {"title": "鵲鴝", "url": "https://zh.wikipedia.org/wiki/%E9%B5%B2%E9%B4%9D", "language": "zh"},
  "image": null,
  "message": "条目和图片独立可用；此处仅演示无图结构"
}
```

`status` 为 `ready | notFound | error`；条目/图各自可空。无效参数400，网络失败502且JSON明确error，不永久缓存成不存在。Wikidata搜索结果必须以P225精确学名、P105物种级别复核，唯一对应实体的中文sitelink优先、无中文才英文。只用同实体P18，经Commons核对CC BY/CC BY-SA或明确公有领域；不使用随机搜索首图、近似种、非自由fair-use或无许可图。

`image` 含 `url/sourceUrl/author/license/licenseUrl`，url为本机 `/species-media/{64位哈希}.jpg`。署名许可可展开，标明缩小/重编码；仅点击百科/来源/许可才打开外部站点。作者元数据按纯文本处理，不插入远端HTML。仅允许官方HTTPS API/图片域及受限重定向，超时/体积/并发/缓存预算见README。真实已核对鹊鸲、八哥、麻雀及两组Top3均有授权图，其他候选仍可能缺失或暂时网络不可用，不声称全库覆盖。

## 拍摄时间与 EXIF 位置

索引的 `item.capture` 与增强组详情的 `frame.capture` 使用同一结构。**以下同样是模拟合同示例，不是用户 EXIF**：

```json
{
  "status": "ready",
  "time": "2001-02-03T04:05:06.123456",
  "timeZone": null,
  "timeSource": "模拟 EXIF DateTimeOriginal",
  "location": { "latitude": -33.5, "longitude": -70.25, "crs": "WGS84", "source": "exif-gps" },
  "locationNote": "模拟坐标，非照片地点"
}
```

`status` 为 `pending | ready | error`。缺少 `capture` 或待处理时显示“元数据尚未读取”，失败显示读取失败，不能据此认定没有 GPS；只有 `ready` 且对应字段为空时显示未记录时间 / 位置。卡片简洁展示时间 / 地点，详情展示到秒及时间说明；来源诊断仍保留在数据，不堆到可见界面。详情优先使用当前精确帧 `frame.capture`，缺少时才使用匹配帧 / 实例的 `item.capture`；同一照片的其他实例可复用同组同帧索引元数据，绝不跨帧或继承之前浏览照片的位置。

时间含明确 offset / `Z`（或独立明确 offset 时区）时，以 `Intl zh-CN` 固定 `Asia/Shanghai` 显示北京时间，如“2023年1月2日 06:44”，不受访问者系统时区影响。无明确时区时按相机墙钟显示中文并标“相机时间，时区未记录”，不猜 UTC；毫秒 / ISO 原值保留在数据，不直接显示，不改变按相机原始日期生成的拍摄组。无效日期明确显示格式无效。地图仅接受有限数字、纬度 `[-90,90]`、经度 `[-180,180]`、`crs: WGS84`、`source: exif-gps`；支持南纬 / 西经及零坐标，拒绝字符串坐标或将 GCJ02 当成 WGS84。没有 GPS 或数据无效时不出现地图定位。

默认不加载任何在线地图、瓦片、地理编码，也不调用设备定位；仅在详情提供“在地图查看（将打开外部地图）”，说明地图服务会获知该位置。点击后以 `noopener noreferrer`、`referrerpolicy=no-referrer` 打开 OpenStreetMap，URL 只含数值经纬，不含照片文件名、路径或密钥。来源和说明均作为文本显示，不转为自动外部请求。

## 地点 / 拍摄日期分组（可选增量）

顶层 `shootGroups` 提供高层拍摄组目录，`item.shootGroup` 及增强详情 `frame.shootGroup` 提供对应组身份。**以下为模拟示例，地名 / 日期不代表用户拍摄地点**：

```json
{
  "shootGroups": [{
    "id": "simulation-place-a-day-1",
    "name": "模拟观鸟地甲 - 010203",
    "date": "2001-02-03",
    "placeName": "模拟观鸟地甲",
    "photoCount": 2,
    "cropCount": 3,
    "preferredCount": 1
  }]
}
```

`item.shootGroup` / `frame.shootGroup` 含相同的 `id/name/date/placeName`，不必重复目录计数。名称和日期由后端按真实大致地点及相机拍摄日生成；前端原样显示，不转浏览器时区、不以 mtime 或用户地区补地点。未知地点组名为“未知地点 - YYMMDD”，缺少日期为“地点 - 日期未知”；不会根据鸟种、示例或省份推断场所。

手机可用下拉支持“全部拍摄组”和单组选择，明确显示当前组名及目录的原图 / 裁剪 / 优选三类计数。筛选同时作用于优选池 / 审核池，可与鸟种搜索、技术等级、反向名次、分页和连拍折叠组合；所有筛选变化重置第一页，刷新保留仍有效的组，已从目录移除的选择回到全部。技术顺序和保留集合不变。这是高层导航，**不是**原 `groupId` 连拍组；点击裁剪始终按原 `/gallery/group/{groupId}.json` 精确实例加载，不用 `shootGroup.id` 代替。

两个页签数量复用卡片的参数化过滤管线，各自统计拍摄组、搜索（含常用英文）、技术等级和连拍折叠后的分页前总量；审核类型只约束审核池，排序不改变数量。清空筛选恢复全局优选 / 审核集合，目录计数不随其他过滤而改写。原来的整条“已验鸟保留 / 筛选后”摘要及诊断声明已删除，不留空节点或 JS 引用。

增量字段尚未上线时筛选停用并显示“地点 / 日期目录尚未准备”，既有图墙照常浏览全部。可选目录字段格式错误只显示该目录错误并停用分组筛选，不让它阻断已验证的优选索引；不从文件名或别帧伪造目录。目录计数为非负整数，ID 唯一且组名非空。

`capture` 可另含 `placeName/placeSource/placeNote`：已确认的大致地点在卡片与详情展示，来源 / 说明保留在数据中，不堆到精简界面；详情优先当前精确帧数据，同帧其他实例可复用匹配索引，不跨帧。拍摄组地名不当作 EXIF GPS。地点 / 日期组可以在完全没有 GPS 时正常使用；有地名也不会凭空生成坐标或自动地图 / 地理编码请求。

## 原图原位切换与照片浏览

详情图片右下角为带 `aria-pressed`、清晰标签与 44px 触控区域的 icon toggle，语义是“完整画面 / 鸟体裁剪”，不表示已下载原始全部像素。默认只加载裁剪；开启后才请求当前精确帧的完整画面预览，状态明确标明预览有效长边。页面地址与详情上下文不变。原先顶部跳转新页入口已取消，不保留双入口。

完整照片分档合同为 `GET /preview/{frameId}/{edge}.jpg`，`edge` 只取 960 / 1920 / 3840；后端保持比例、应用 EXIF 方向、源图小于档位时不放大，并按源指纹原子缓存、私有 HTTP 缓存和条件请求处理。前端按可用 CSS slot、原完整照片比例、DPR 和自定义 zoom 选择最小足够档；fit 初看 DPR 上限 2 且目标长边不超过 1920，放大后使用实际 DPR，超过 3840 的实际需求才取 `/original/{frameId}`。不下载所有档，不引入瓦片 / CDN / 新 GIS 或图像依赖。

显示宽高和最大缩放范围按 `frame.width/height` 的原始几何计算，而非小预览的 `naturalWidth`。高档用新真实 `img` 解码完成后原位替换，保留低档直到可用，不改变当前缩放、平移或标注映射；viewer 与详情同时显示加载 / 错误状态。仅保留当前帧裁剪及最高已加载完整画面，缩小不降档，重复同档不重复下载；220ms 手势结束 / 缩放控件节流及 12% 临界滞回避免频繁请求。切照片、关闭和需求失效时取消 / 忽略未完成或过期请求。升级失败保留已加载低档并提供重试，不以 404 为由静默下载整文件；当前分辨率会明确展示。

裁剪叠加 `viewBox=cropRect`，原图叠加为 `0 0 frame.width frame.height`（EXIF 方向应用后的显示尺寸），绝不使用原编码尺寸猜测交换。缺少有效坐标 / 尺寸时停用叠加。切换模式仍使用原图坐标下的真实掩码 / 关键点，默认两个开关关闭。

点图片打开独立全屏 `dialog` viewer，移动当前真实 `img` 节点而不是生成 canvas 截图；保持当前裁剪 / 完整画面模式，不把标注烘焙进图片。单指短点返回详情，双指捏合缩放（上限由原始尺寸决定，最多 8 倍）、放大后拖动平移；通过 Pointer 轨迹、位移、多指与时长排除误关闭。超过 350ms 的按住及 `contextmenu` 不当作轻点，`pointercancel` 不关闭；不阻止原生右键 / 长按保存、不设置 `-webkit-touch-callout:none`。另有返回按钮、Esc 和缩放按钮，退出只关闭 viewer，恢复原详情实例、模式和滚动位置。全屏适配动态视口和安全区，尺寸变化时重算平移边界。

长按仅保存**当前显示分辨率**。Viewer 提供独立“保存原图”同源下载链接 `/original/{frameId}?download=1`（带 `download` 属性），只在明确点击时取未修改原文件，不返回旧的跳页浏览模式。后端负责附件响应和字节一致性；前端不声称系统长按菜单会自动保存原文件。

回归用 CDP 真实触控事件模拟双指 / 拖动 / 长按，验证合成 click 不误重开、默认零原图请求、原图错误回退、模式缓存、叠加坐标映射和手机横竖屏。原生手机系统保存菜单因浏览器 / 系统差异仍需实机确认，自动化不声称已经验证实际系统菜单。

## 浏览与验证

默认先显示优选图片，分页每页 48 项，可选择 24 / 96。只为当前页创建缩略图，使用浏览器懒加载；点击后才请求组 JSON、大裁剪与同组候选缩略图，不预取完整原图。“每个连拍组仅显示首项”是可选显示折叠，不修改保留结果，也不伪造代表性；“连拍代表”只读取 `quality.representative`。Tag 简写“鸟类候选 / 待确认 / 待识别 / 非鸟”，质量等级仅映射为“高像素 / 标准像素 / 触边风险 / 低像素”，不改变 tier 或声称合焦。

手机图墙采用单列，横屏 / 平板按可用宽度恢复多列；详情Top3仍为三列，中英文可换行。按钮、选择器、搜索与技术依据折叠入口至少 44px，详情采用动态视口和安全区限制，内部可滚动、关闭按钮固定在弹层顶部。详情右栏依次为状态、分类图卡、拍摄信息、默认折叠的技术依据；模型 / 验鸟原理 / 证据不在可见详情展示，原 API 与审计数据不删。主界面和详情不再提供旧报告、JSON 等调试链接。

运行 `node tests\gallery-report.test.mjs` 使用既有 Playwright / Edge，在随机回环端口提供明确标注的模拟 JSON 与无照片占位图，覆盖优选隔离、中文 Top3 / Top5 兼容与缺名、原分类概率、后端排序、筛选分页、组详情、多鸟 / 无眼、叠加切换、自动更新、等待 / 错误、路径与文本安全。触控回归覆盖 360 / 390px 竖屏、横屏、768px 平板和桌面，检查长学名、状态、懒加载、详情滚动关闭与页面无横向溢出；另通过完全拦截的模拟内网来源验证前端不会拒绝非回环主机，JSON / 裁剪仍然同源。测试服务只绑定随机回环端口，不打开真实 LAN / 防火墙、不访问用户照片、不生成私人截图。

需要只读校验当前真实索引时，可设置 `BIRDSHOT_GALLERY_INDEX_URL=http://127.0.0.1:5174/gallery.json` 再运行同一测试：仅接受本机 `/gallery.json`，JSON 只保留在内存，媒体仍用模拟占位图；不保存私人索引、照片或截图。该模式验证新版 UI 接受真实数据形状，不宣称验证了模型准确率或真实 LAN 监听。

`tests\gallery-live.test.mjs` 是当前实际候选（含鹊鸲/八哥）批次的显式联调回归，不是通用准确率测试。设置 `BIRDSHOT_REPORT_URL=http://<本机已授权LAN地址>:5174/`（或127.0.0.1）、可选 `BIRDSHOT_EXPECT_PREFERRED` 后运行 `node tests\gallery-live.test.mjs`，会实际读取当前照片与参考缩略图到浏览器内存，核对360/390/横屏、源码入口一致、6种参考/百科/作者许可映射、标题/控件/鸟体对比、fit预览与放大原图、浏览前后索引不变；不保存原图、EXIF或截图。新批次没有这些确切候选时应使用合成回归或另选实际样本，不伪造鸟种来通过。

如需显式校验某张真实原 JPEG，可另外设置 `BIRDSHOT_GALLERY_ORIGINAL_FRAME` 为**实际索引中存在**的单帧 ID。测试仅将该帧通过当前本地服务读到内存 / 浏览器，核对 fit 使用真实分档预览、放大才升级原文件、EXIF 定向后的尺寸 / 比例、叠加坐标和 viewer 返回；不写入原图或截图。真实分档接口必须已经部署，测试不会用模拟预览冒充联调成功。未配置该变量时媒体仍全部模拟；无可靠鸟体裁剪、未出现在索引的原图不能伪造实例来做验证。

拍摄元数据回归均使用模拟值，覆盖有 / 无 GPS、缺字段、待读取、错误、未知时区、南西经、零坐标、越界 / 错误坐标系、组帧与索引优先级及切换帧不串用位置。地图点击由测试拦截为模拟页面，不真正连接地图；验证加载页面不会发送坐标，点击后不带报告 referrer，且手机可换行。

地点日期筛选回归覆盖同地不同日、同日不同地、未知地点 / 缺日期、原图与多实例裁剪计数区分、优选 / 审核组合过滤、分页重置 / 刷新、原连拍详情路由、目录尚未准备 / 无效、长地名文本安全及手机触控。可选真实索引模式会在内存验证后端组名原样展示、目录计数及优选集合一致，不保存或输出具体地名 / 拍摄日。
