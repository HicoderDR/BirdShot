# BirdShot

本地鸟类筛片研究 Demo：**读取照片 → YOLO 鸟体/分割 → 候选姿态与原像素局部纹理 → Birder 验鸟 → 同对象组内技术推荐 → 人工覆核**。模型不上传个人照片，不删除、移动或改写原 JPEG / RAW。报告默认受限，可显式开启全来源只读浏览；开启后可达客户端会收到照片、原图及登记元数据。

**桌面版 0.4.1**：保留组优先的[沉浸连拍审阅](docs/review.md)，并修复大旧库启动阻塞与超时后状态调用无法恢复的问题：启动阶段可查询，初始化/服务失败可重试，不通过增加超时或清空用户库掩盖错误。原图/轮廓/裁剪联动、快捷键、照片级人工多选及需确认的系统回收站保持。完整Windows x64当前用户安装器随附运行时和模型；来源库、逐组增量、数据保留与手动更新继续支持。排错、构建、安装/卸载及许可见[桌面版说明](docs/desktop.md)。旧制品和PWA保留；默认仅本机5274，不接管旧5174。

## 当前功能与边界

- `/cull`：浏览器本地分析 JPEG / PNG、真实公开视频连续帧及公开多鸟样例。样例明确是**视频连续帧，非相机连拍，无人工质量标签**。
- 批处理：顺序分析、断点恢复，只保存实例裁剪、缩略图、选中裁剪副本及 JSON 溯源；不整批复制原片。YOLO11s-seg 使用全图640与原像素局部精查，非固定框或假掩码。
- 优选图墙：全类别 Birder 鸟/非鸟/未知竞争、技术分层、独立审核与地点/相机日期组合筛选。主页Top3跨满主卡可用全宽、三等分轻量标签，每项一行中文标题+一行概率，长名省略但保留完整提示；去明显描边、只留微弱高光/短阴影。首页与详情统一用蓝色按原概率强调，不显示候选图、英文或重复拍摄组行。底部单行左时间右地点。拍摄场次导航不替代原连拍 `groupId`，不重算技术评分。
- 沉浸式详情：鸟名/实例与轻量关闭图标在同一页头；图片底部三个高通透纯按钮切换轮廓、姿态与全图，以`aria-pressed`及强调色表示状态，不嵌套小switch、不整体降低文字透明度。轮廓/姿态默认关闭。**鸟体对比**仅在原组有两个以上实例时显示，支持单张多鸟；不同鸟体可切换浏览，不代表能直接比较同鸟清晰度。
- 详情Top3保留中英文和公开参考图的三列等高图卡，名称自然换行、不机械预留多行空白；百科入口融合为卡片底栏，仅上分隔线、底部撑满对齐，无独立按钮边框。按原概率降序、强调色使用原值，两处概率仅显示一位小数、不重新归一化。参考图与百科精确匹配学名，作者/来源/许可集中在默认收起的“图片来源”；不是本次照片的人工鉴定。
- 原位全图与手势 viewer：按需960/1920/3840预览，放大后才升级，保留缩放/平移；单击退出、捏合/拖动。系统长按保存当前显示档位，独立“保存原图”下载未经改写的原文件。

完整画面可继续放大至原像素对应尺度，不再让固定8倍上限阻断6K横图升级。只有当前可见原文件成功解码且尺寸匹配登记照片才标“原文件已加载”；预览到源像素尺寸、最大缩放倍率和原文件加载是不同状态，失败继续显示低档并可重试。

眼睛不可见或定位不可靠时明确未判定，**不把主体纹理冒充眼部合焦**；默认自动推荐不使用候选眼区。闭集概率不是正确率、Tenengrad 不是跨物种绝对清晰度，也没有人工选片真值评测。RAW 当前仅清点/与同名 JPEG 配对及可选元数据核查，**不支持 RAW 像素筛片或显影**。已检查的一批 JPEG/配对 ARW 均无位置来源只是该样本事实，不表示其他照片也没有 GPS。

原有 `/` 观鸟 PWA 仍保留：浏览器相机 YOLO11n、373类中国常见鸟视觉分类、BirdNET 本地音频识别。它与下面5174裁剪报告不是同一服务；手机相机/麦克风需要 HTTPS，内网 HTTP 报告浏览不需要这些权限。

## 环境与安装

实际验证环境：Windows、Node.js **24.11.1**、Python **3.12.12**、CPU 推理；Pillow13.0.0、Ultralytics8.3.237、ONNX Runtime1.23.2、Birder0.8.4、open_clip_torch3.3.0。Node 至少满足 Vite8 的22.12+要求，推荐24；当前环境 Torch2.15.0，无CUDA要求。

```powershell
npm.cmd ci
python -m venv .venv
$python = (Resolve-Path .venv\Scripts\python.exe).Path
& $python -m pip install -r scripts\culling-requirements.txt
# 仅分类验鸟/模型对比需要：
& $python -m pip install -r scripts\comparison-requirements.txt
& $python scripts\prepare-culling.py
```

准备脚本从官方来源下载 YOLO/姿态权重，导出640 ONNX并记录SHA；下载公开斑鱼狗视频、抽取连续36帧及公开双鸭照片。模型和样例在 `.tools`、`public\culling` 的忽略目录，**不包含在Git仓库中**。首次准备需联网；中断后可重跑。不要关闭TLS校验或使用不明权重。公开样例、CC BY-SA署名、AGPL及CUB训练素材限制见[筛片说明](docs/culling-demo.md)。

## 前后端调试

```powershell
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

打开 <http://127.0.0.1:5173/cull>。React/TypeScript由Vite热更新；浏览器开发工具可检查ONNX与本地请求。手动导入JPEG/PNG无需Python服务。可选本机目录通过配置传入，不写死路径：

```powershell
$photoFolder = Read-Host "照片目录完整路径"
& $python scripts\prepare-local-photos.py $photoFolder
$env:BIRDSHOT_PHOTO_DIR = $photoFolder
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

这是回环只读白名单，不应放到公网代理。`scripts\start-culling.ps1` 是可选Windows启动助手；执行策略不允许时直接使用上述命令，不绕过策略。批次图墙HTML独立于Vite，由 `batch_culling.py` 提供，修改HTML刷新即生效；修改Python路由需重启对应报告进程。

Windows示例使用Node随附的 `npm.cmd`，避免PowerShell将 `npm` 解析为受执行策略限制的 `npm.ps1`；不修改执行策略。非Windows使用 `npm`。

## 批处理、恢复与分类画廊

下面 `$job` 是工具生成的独立输出目录，不是原片目录。首次初始化后记录它；恢复时直接指定已有任务，**不要再次 init 创建另一批**：

```powershell
& $python scripts\batch_culling.py init --source (Read-Host "照片目录完整路径")
$job = (Get-Content .tools\batch-current.json -Raw | ConvertFrom-Json).job
$node = (Get-Command node).Source
& $python scripts\batch_culling.py serve --job $job --port 5174
# 另一个终端（重新设置相同 $python、$job、$node）：
& $python scripts\batch_culling.py run --job $job --node $node --threads 4
& $python scripts\verify_batch.py --job $job
```

默认 <http://127.0.0.1:5174/> 是分类画廊入口；尚未准备验鸟数据时明确等待，不把旧技术候选当新优选。旧批处理进度和安全暂停在 <http://127.0.0.1:5174/legacy/>。再次执行同一 `run` 恢复，先核对源与模型/策略指纹；不同时启动两个处理器。异常遗留 `run.lock` 只能在确认该批旧处理进程已停止后手动处理。

分类准备不必上传图片、不需要懂鸟Key，也不需要私人十图对比清单：

```powershell
$names = ".tools\gallery-taxonomy"
& $python scripts\comparison_prepare.py --job $names --taxonomy-only
& $python scripts\comparison_birder.py --prepare-only
$revision = Join-Path $job "revisions\verified-birds-birder-v2"
& $python scripts\gallery_birder.py --job $job --output $revision --comparison $names --threads 6
& $python scripts\capture_metadata.py --job $job
& $python scripts\gallery_build.py --job $job --revision $revision --comparison $names --node $node
& $python scripts\verify_gallery.py --job $job --revision $revision
```

鸟名来自官方eBird/Clements2025英文/`zh_SIM`表，按学名精确匹配，缺失不猜译。Birder固定官方revision并验证SHA，全10000生物类竞争（1486鸟类），不是地区专用头。模型、词表首次准备需访问官方网络；已完成的同指纹验证可恢复。更新位置文字审计/日期索引不重跑模型，详见[位置来源](docs/location-sources.md)与[分类画廊](docs/verified-gallery.md)。

上述流程先生成分类画廊；完整RAW/旁文件位置审计及地点日期目录另按[位置来源的命令](docs/location-sources.md#拍摄导航不是连拍重新分组)执行（需要经官方SHA校验的ExifTool及发行ZIP）。`shoot_groups.py` 复用已完成画廊，不重跑分类；完成后再次 `gallery_build.py`，传入相同 `$names` / `$node`。未完成该步骤时不伪造拍摄组。

输出的 `manifest.json` 是原片登记表；`mapping.json` 关联原片、配对RAW、SHA、EXIF转正后的坐标与裁剪；`groups`/`crops`/`selected` 保留旧结果，新分类与索引写在 `revisions`，不覆盖历史选择。没有检出的原图仍计入日期目录，不生成背景裁剪。JPEG-only不当作RAW读取失败。GPS缺失不推测地点，也不自动反向地理编码。

## 内网部署与更新

5174提供图墙、JSON、裁剪、原图、分档预览和物种参考图，手机全部同源。5175仅用于**已有固定十样本/四模型结果**的独立研究对比，默认只回环，不是主图床、不需要为普通筛片启动：

```powershell
& $python scripts\comparison_report.py --job (Read-Host "已有对比结果目录") --port 5175
```

先用 `Get-NetIPAddress -AddressFamily IPv4` 和 `Get-NetConnectionProfile` 查真实物理Wi-Fi地址/前缀及Private状态，不选VPN/虚拟网卡，不沿用他人的地址。明确授权内网访问后：

```powershell
$lan = Read-Host "本机物理Wi-Fi的内网IPv4"
& $python scripts\batch_culling.py serve --job $job --port 5174 --lan-ip $lan --lan-prefix 24
```

`--lan-prefix` 应按实际子网调整。只监听127.0.0.1和指定RFC1918地址，不绑0.0.0.0；限制Host、同源、子网及登记媒体，LAN管理POST拒绝。手机与电脑需在可互通子网，访客网络/AP隔离可能阻断。当前部署实际入口由运行者确认，不能把本机HTTP200当真机验收。

**可选全来源模式（风险较高、无认证）**：

```powershell
& $python scripts\batch_culling.py serve --job $job --port 5174 --lan-ip $lan --allow-all
# 若确实使用自有主机名，可额外 --allowed-host report.example.com
```

此模式监听`0.0.0.0:5174`的所有IPv4接口，解除GET客户端来源IP/子网限制；访问仍使用真实接口地址，如`http://<本机IPv4>:5174/`，不是把0.0.0.0当唯一用户入口。**任何可达设备都能读到私人照片、原文件及登记JSON（可能含本机路径/文件名）**，不适合未经评估的共享/公网环境。Host仅接受本机当前IPv4、localhost/机器名和显式`--allowed-host`，保留同源/CORP、媒体白名单和路径防护；不开放CORS星号。管理POST仍只接受127.0.0.1且Host为localhost/127.0.0.1，同一来源检查不取消。

监听所有接口**不等于互联网已经可达**：本程序不配置NAT、路由器转发、公网隧道或第三方代理；防火墙/路由仍决定可达范围。地址变化后重启或明确补充允许的本机主机名。页面底部和HTTP `X-BirdShot-Access-Mode`明示当前访问模式；不提供认证安全保证。

不要关闭防火墙或默认提权。有现存精确程序Private入站允许规则时可能无需新增；确实缺少时，管理员可按本机策略运行：

```powershell
.\scripts\allow-report-lan.ps1 -LocalAddress $lan
```

该脚本校验物理网卡/Private/当前BirdShot监听程序，只增加**精确Python程序 + 本机IP + TCP5174 + Private + LocalSubnet**规则；不覆盖现有不同规则，不开放5173/5175/公网。权限或执行策略失败时明确处理，不使用Bypass。

上面脚本仅供受限模式。全来源模式先只读核对现存精确Python程序规则；已有相应允许规则时不新增、不删除既有规则。本部署机当前Wi-Fi为Private，已存在该精确程序的Private TCP入站Allow（来源Any），无需修改防火墙。确实缺少时，管理员在核实监听进程确为本任务后，仅对该程序和5174端口按需增加：

```powershell
$servicePid = (Get-NetTCPConnection -LocalPort 5174 -State Listen | Select-Object -First 1).OwningProcess
$service = Get-CimInstance Win32_Process -Filter "ProcessId=$servicePid"
$service | Select-Object ProcessId,CommandLine
# 人工确认是本任务的 batch_culling.py serve 后：
New-NetFirewallRule -Name "BirdShot-5174-AllSources" -Direction Inbound -Action Allow `
  -Program $service.ExecutablePath -Protocol TCP -LocalPort 5174 `
  -LocalAddress Any -RemoteAddress Any -Profile Private
```

若确实要通过Public配置文件接口访问，规则只改为所需Public profile、仍限此程序/TCP5174；不改变全局网络类型、不开放所有端口。权限不足时报告实际阻碍，不擅自提权。没有独立外部设备/公网入口时不得声称已从互联网验收。

更新先正常拉取/切到所需提交，勿强制覆盖本地修改。重启只影响已核实的**报告服务PID**，不终止模型处理器、5175或其他Python：

```powershell
$listeners = Get-NetTCPConnection -LocalPort 5174 -State Listen
$listeners | Select-Object LocalAddress,OwningProcess
$servicePid = [int](Read-Host "核对后的BirdShot报告PID")
Get-CimInstance Win32_Process -Filter "ProcessId=$servicePid" | Select-Object ProcessId,CommandLine
# 确认为本任务的 batch_culling.py serve 后：
Stop-Process -Id $servicePid
# 在新终端用上面的 serve 命令重新启动，保留 $job / LAN 参数
Invoke-WebRequest "http://${lan}:5174/" -UseBasicParsing
Invoke-WebRequest "http://${lan}:5174/gallery.json" -UseBasicParsing
```

重启报告不会重推理或修改选择。用目录内真实登记的frameId检查 `/preview/{frameId}/960.jpg` 和 `/original/{frameId}`，用当前候选学名检查 `/species-reference?scientificName=...` 及返回的同源图片；未知ID/任意路径必须失败。Python标准错误保留明确缓存/网络错误，HTML与进度no-store，手机刷新后应读到同一版本。

## 私有图片与公开物种参考缓存

- `revisions\image-preview-cache-v1`：按请求生成960/1920/3840长边JPEG88，不裁比、不放大，正确EXIF方向和ICC；源size/mtime_ns及编码版本变更失效。同key互斥、最多2解码，512MiB LRU只淘汰自有派生文件。`private`、ETag/Last-Modified与304，不设public。`/original/{id}?download=1`仍返回源字节。详见[预览缓存](docs/preview-cache.md)。
- `revisions\species-reference-cache-v1`：只把**公开学名**发给Wikidata/Commons，不发送照片、EXIF、位置、私人路径或凭据。核对P225精确学名、P105物种、P18图和自由许可；百科主入口始终中文，无中文词条明确改为中文搜索，英文源保留数据中、不伪造中文词条。浏览器只加载本机480px缩略图，外部百科/许可仅点击打开。最多2个联网查询、单请求12秒超时、JSON4MiB/图3MiB上限；参考图128MiB预算只清登记派生图。热缓存不重新联网，资料7天、确无可靠对应6小时；错误从30秒有限退避到300秒，并尊重更长Retry-After，不把失败永久记成不存在。
- 许可/作者在分类区下方统一“图片来源”展开，不占每张图卡高度，必要署名及缩略改动声明并未删除。缺图/无条目/网络失败均有占位，分类概率与本地图片不受影响。参考图可能需要数秒冷获取，不保证所有模型类别都有条目/自由图。关闭服务后可只清理上述**明确派生缓存目录**再按需重建，不能删除原片、裁剪、selected或审计JSON。公开图片继续受各自CC BY/CC BY-SA/公有领域条款约束，不随代码改成AGPL。

参考资料区分上游繁忙/maxlag、超时、无精确实体及无授权图；图片阶段失败时保留已核实词条，旧成功资料可明确标旧继续查看，状态仍为error/partial而非假成功。只在允许的时机人工重试，不自动狂打上游；没有已核实条目也可点击清楚标记的“中文百科搜索”。

Wikimedia繁忙或无合适授权小图时，有限尝试iNaturalist备源：精确`name + species + is_active`核对物种和taxon_photos，只接受逐图CC BY / CC BY-SA / CC0，跳过默认无许可、NC、ND。来源/作者/原许可保留在统一“图片来源”。这不是国内图床，仅本机实测可达；未接入未经核验授权的百度/鸟网等图库，不能保证所有网络或物种可用。缓存成功后不依赖每次联网。

百科参考图只请求官方约500px小档：Commons同时限宽/高480并核对声明尺寸，iNaturalist用官方medium，不先下载1920或原始大图；最多接受640px邻近小档，落地最长480px、不上采样。注册JSON记录远端URL、实际尺寸/字节、许可及本机尺寸/字节；现有合格缓存不重抓。此限制**不影响个人照片**的1920/3840分档或完整原文件。

## 检查与源码

```powershell
npm.cmd run test:culling
npm.cmd run lint
npm.cmd run build
& $python -X utf8 -m unittest discover -s tests -p "test_*.py"
node tests\gallery-report.test.mjs
```

Python测试使用临时fixture，不读取个人目录；图墙测试用Playwright/Microsoft Edge（需已安装），包含360/390手机与横屏、鸟体对比显示规则、参考图失败/迟到、独立开关、原位预览和手势。其他浏览器可设置 `BIRDSHOT_BROWSER_CHANNEL`；`npm run test:demo` 另需已启动5173与准备公开样例，会实际运行模型。只读真实索引/指定帧联调方法见[图墙接口与测试](docs/gallery-report.md)。自动化不能代替真机系统长按保存菜单检查。

| 位置 | 用途 |
| --- | --- |
| `src\culling` / `src\lib` | 筛片UI、分组/指标、检测跟踪与原观鸟应用 |
| `scripts\batch_culling.py` / `batch_inference.py` | 本机批处理、受限报告HTTP服务、原生ONNX |
| `scripts\gallery_*.py` / `gallery_rank.mjs` | Birder验鸟缓存、原子索引和技术排序 |
| `scripts\gallery-report.html` | 无构建依赖的手机图墙和详情viewer |
| `scripts\preview_cache.py` / `species_reference.py` | 私有预览与公开精确物种参考缓存 |
| `scripts\inspect_*.py` / `capture_metadata.py` / `shoot_groups.py` | 只读位置来源审计、日期导航 |
| `tests` / `docs` | 定向行为测试及运行/许可说明 |

四方对比与各分数限制见[模型对比](docs/species-comparison.md)。懂鸟**完全可选**，仅在另行明确授权上传样本时使用运行时环境变量 `DONGNIAO_API_KEY`；不写入源码、URL、前端或日志，不因设置Key自动上传，不是本机Birder的依赖。

新增筛片实现遵循 [AGPL-3.0-only](LICENSE-CULLING.txt)；不替无关原有文件重新声明许可。YOLO/姿态权重及CUB来源限制、公开样例署名须分别保留，网络提供服务时应提供对应源代码。PWA候选模型与评测门槛见[模型说明](docs/model-evaluation.md)。
