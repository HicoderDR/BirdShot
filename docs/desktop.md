# BirdShot 桌面版（Windows x64）

桌面版是 **Electron原生桌面壳复用现有Web图墙和本机Python推理**，不是把所有控件改写为系统原生组件。文件夹选择器是系统对话框；图墙、蓝色候选标签、参考图卡和手势浏览器沿用同一实现。PWA及独立报告服务仍保留。

## 0.3.0 当前用户安装包

0.3.0 使用 `BirdShot-0.3.0-Setup-x64.exe`，完整离线、Windows x64，默认只为当前用户安装，不要求管理员。可选择安装路径，有开始菜单入口及系统“已安装应用”卸载入口。旧0.2.0便携ZIP作为基线保留，不覆盖或删除。

安装器只在安装/覆盖升级时解压一次；正常启动直接运行已安装Python/模型，不是每次展开2GB的单文件程序。安装与卸载只处理登记的应用文件；不会删除照片来源、userData、已完成裁剪、用户选输出或未登记文件。升级时先从App安全退出，保留任务检查点，再运行新安装器。安装器不强制关闭其他Python或旧报告服务，不更改防火墙/执行策略/NAT。

本机构建产物在被Git忽略的`desktop-artifacts`，不提交运行时、模型或EXE/ZIP到普通Git。发布时以旁边的版本/哈希清单和实际测量为准，不把`npm run desktop:dev`当作制品。

完整包包含：

- `BirdShot.exe`与Electron内置Chromium/Node，主界面不需要系统Node。
- `resources\runtime\python`：可重定位CPython3.12、Torch CPU、ONNX Runtime、OpenCV、Pillow、Birder及经实测保留的依赖DLL，不引用开发venv。按wheel RECORD及生产依赖闭包选择，标准库源码打入`python312.zip`，不是完整复制构建环境。
- `resources\runtime\node`：独立Node24，用于现有TypeScript跟踪/技术推荐worker，不依赖系统PATH。
- `resources\backend\public\culling\models`：YOLO11s-seg与六点姿态ONNX；`models\birder`为固定官方10000全类分类权重及配置，`assets\taxonomy`为中英文名录。
- `resources\backend\assets-manifest.json`：必需资产路径、大小、SHA256与版本；启动核对模型，不在第一次运行时偷偷联网下载。
- `resources\licenses`：代码AGPL、模型卡、Python依赖许可清单及对应源代码ZIP；wheel原许可按路径及SHA保存在`python-third-party.zip`并附索引，每个dist-info指向该归档。Electron的MIT/Chromium与Python许可证仍随原分发保留。

体积包含真正运行时和权重，不能按“一个Electron壳”的大小估算。未包含巨大BioCLIP/Huge研究权重、懂鸟Key、个人照片、裁剪、EXIF清单、研究对比结果或个人配置。

保留的0.2.0便携ZIP仍可解压后运行`BirdShot.exe`。其大量第三方开发/测试小文件是本次优化的基线；支持中文和空格，不需要更改Windows长路径/执行策略：

```powershell
New-Item -ItemType Directory -Force "C:\Apps\BirdShot 桌面" | Out-Null
tar.exe -xf .\BirdShot-0.2.0-win-x64.zip -C "C:\Apps\BirdShot 桌面"
```

安装器和便携制品均未代码签名，SHA只证明文件完整性，不等价于发布者签名。不要关闭Defender、绕过组织策略或默认提权；若组织策略拦截，请先核对来源/SHA并由管理员审核。其他操作系统当前仅有源码设计，未经实际打包验证。

## 首次打开与照片库

默认元数据、任务、派生裁剪、缓存和日志保存在Electron的用户数据目录，不写入原片目录或包内resources。界面展示当前输出位置，可用“选择输出目录”修改；不能放在原片文件夹内或包内resources。

1. “添加照片文件夹”打开系统目录选择器。可以添加多个来源，包含子目录；路径规范化、相同/嵌套来源按原照片身份去重。
2. 扫描与顺序处理状态保留在库中。真实执行检测、实例分割、姿态、原像素主体指标和Birder全类竞争，之后显示裁剪图墙。不能识别/损坏/目录失效会明确报错，不默认为空或失焦。
3. 关闭App后可重开恢复；同一目录不会因再次添加而变成重复照片。移除来源只是解绑，不删除源照片和历史输出。
4. 已有BirdShot批次用“加载已有批次”选择其输出目录。读取既有JSON/裁剪并在用户数据目录生成聚合视图，不要求重新跑一遍旧照片；不要将已有任务当作新照片目录加入。

普通新用户可从空库开始。支持JPEG/PNG像素分析；RAW仍仅登记/配对，不宣称RAW显影或全部格式支持。原片保持只读，裁剪与预览不是原文件替代。

### 逐连拍组可见，不等全目录结束

0.3.0 先只读清点形成稳定连拍组，然后按组执行并发布。界面显示清点、本地模型加载、检测/分割/姿态/裁剪、拍摄元数据、Birder全10000类、同组技术比较、发布图库这些真实阶段，分别有待处理/进行中/部分完成/完成/失败状态与照片、组或裁剪计数。

一组真实裁剪写完即出现在独立审核：此时明确待分类/尚未推荐，不把YOLO置信度当作已验鸟。该组分类和组内技术比较完成后，才原子发布优选/nonBird/最终unknown；下一组仍处理也能浏览已发布组。模型在同轮worker内热复用，不每个组重复加载大权重。

每组检测/分类与最终发布有持久检查点和策略/图片指纹，恢复跳过已完成内容；错误不计成功，已处理未知与尚未处理分开。组提交后才通知桌面读取索引，刷新不整页重载或强行关闭当前详情/缩放。共享网页也能读取部分结果；单组没有足够比较依据仍待审，不伪造绝对合焦。

### 已加入目录有新照片

默认是**手动检查新增**，不是持续文件监控。使用“检查新增照片”；再次选择同一目录也安排检查，未变化的旧结果复用，不重复识别整目录。没有新增稳定文件时明确提示、不创建空job或启动模型。

正在处理时请求会持久合并为一次`scanRequested`，当前快照结束后再检查一轮，不同时开第二个处理器，关闭重开仍保留请求。文件新增按有限快照轮次处理，不因为持续复制使当前批次永远无法完成。

扫描比较size/mtime，连续一致且文件mtime至少早于检查1秒才进入快照；最多6次有限检查，仍在写入的文件留在`pendingFiles`、提示下次手动检查。`.jpg.part`等临时扩展不直接识别，也不永久当损坏文件。

同路径变化/删除用`sourceIssues`标记`changed/missing`，有已缓存SHA时也验SHA；原图/preview会拒绝异常映射，不能把旧裁剪配给已改变的原图。历史裁剪保留，独立新增仍可处理；不自动重分析全部旧片或删除旧结果。新增配对RAW只更新`associations`，不重推理旧JPEG；旧RAW-only随后出现JPEG时，该JPEG真正进入新增分析，RAW像素仍不支持。

若新图按原EXIF规则接到旧连拍尾部，仅扩展受影响组，复用旧检测/分类缓存，生成新的`groupRevision`；即便原job为只读历史，派生结果也写到新任务，不覆盖旧mapping/selected。模型/策略不兼容或主体身份不稳时，组内新旧帧仍可浏览对比，但明确`history-tail`待确认，不制造优选或名次。顶部日期/目录/地点导航不参与这项同鸟比较判断。

## “拍摄组”是筛选器

顶部分类方式可切换：

| 方式 | 规则 |
| --- | --- |
| 拍摄日期 | 真实相机拍摄日；未知时间单列，不用文件mtime或导入时间补 |
| 来源文件夹 | 来源目录与相对子目录；重名保留来源身份消歧 |
| 拍摄地点 | 可信元数据/确认地名；没有来源就是未知地点，不根据画面或用户地区猜公园 |

切方式只改变元数据导航；日期可跨来源合并，文件夹按来源分开。不会复制照片、重跑模型、更改原连拍`groupId`、候选跟踪或质量cohort。

筛选同时作用于优选/审核、搜索、技术等级、分页和计数；目录分别统计原图/裁剪/优选，没有检测裁剪的原图仍计入原图数。合法选择在重启/刷新后保留；原有日期-地点`shootGroups`兼容旧报告。

## 本机与网络服务

新App默认`127.0.0.1:5274`，不抢占独立旧服务5174。设置可以修改端口；已占用时明确显示错误/建议端口，由用户应用新配置，不连接或杀死占用者。

- **仅本机**：默认，关闭LAN新连接，桌面iframe仍从回环加载。
- **局域网只读**：显式选择本机IPv4与端口，只在指定接口/回环提供已登记报告与媒体。
- **所有来源只读**：显式开启后监听所有IPv4；任何可达设备可能看到照片、完整原文件和登记元数据，没有认证保证。保留Host/同源/路径/注册表约束，不打开任意磁盘代理。

App显示实际可连接地址与健康状态。开启/更改服务基于当前实例身份的health，不把“某个端口开着”当自己的服务。管理只走本App子进程私有stdin协议，LAN网页不能添加来源、改配置或执行任意命令；共享端口的管理POST仍受本机限制。

不自动配置NAT、路由转发、隧道或防火墙，不自动提权。需要时按README核对精确程序/端口规则；包内Python路径与开发机Python不同，不能假设旧规则自然覆盖新包。未从真实公网/手机测试时不能声称已互联网可达。

关窗口即退出并停止本App启动的后台服务；开启共享时提示其他浏览器会断开。不常驻托盘，不按进程名杀Python。Windows使用匿名JobObject保证控制器硬退出时清理它的子孙进程，旧5174和其他Python不受影响。

## 离线与公开资料

检测/分割/姿态/Birder及名录均从包内加载，不需要懂鸟Key或系统Python/Node。模型完整性失败要修复/重新解压完整包，不从不明网站下载替代。

百科参考图是可选联网能力：只发送公开学名，不传用户照片、Exif或位置。中文词条/明确中文搜索、Commons/iNaturalist授权小图、本机同源缓存及上游退避规则沿用README。离线时显示占位和明确资料错误，不能影响已完成的本地照片筛片。

可在进程环境设`BIRDSHOT_OFFLINE=1`用于明确的严格离线运行/验证，Python仅允许loopback连接；可选`BIRDSHOT_NETWORK_AUDIT`记录被拒外连的PID/目标，不记录照片内容。百科因此不可联网，但已缓存资料仍可用。此开关只减少网络能力，不是管理后门。

权重授权分别为YOLO/姿态AGPL与Birder Apache-2.0。姿态模型卡特别说明CUB训练图像的非商业研究/教育权利边界，权重许可不授予训练图片权利；包内不含CUB数据，不作商业数据/肖像权保证。再分发/提供网络服务需遵守对应许可证及源代码义务。

## 源码运行、构建与调试

在Windows x64准备Node24、CPython3.12及模型（只在构建机需要）：

```powershell
npm.cmd ci
python -m venv .venv
$env:BIRDSHOT_BUILD_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
& $env:BIRDSHOT_BUILD_PYTHON -m pip install -r scripts\desktop-requirements.txt
& $env:BIRDSHOT_BUILD_PYTHON scripts\prepare-culling.py
& $env:BIRDSHOT_BUILD_PYTHON scripts\comparison_prepare.py --job .tools\gallery-setup-check --taxonomy-only
& $env:BIRDSHOT_BUILD_PYTHON scripts\comparison_birder.py --prepare-only
```

CPU分发是已验证目标，GPU/CUDA依赖不是必需。开发模式指定本机解释器/Node，无个人绝对路径写入源码：

```powershell
$env:BIRDSHOT_PYTHON = $env:BIRDSHOT_BUILD_PYTHON
$env:BIRDSHOT_NODE = (Get-Command node).Source
npm.cmd run desktop:dev
npm.cmd run desktop:build
npm.cmd run desktop:installer
```

`desktop:build`按`package.json.desktopVersion`生成独立的完整可运行目录，不覆盖旧版本制品；已有同版运行时、仅修改本项目源码时可用`node scripts\build-desktop.mjs --refresh`增量刷新（先退出该包）。`desktop:installer`使用Inno Setup 7.1.1当前用户安装脚本，采用快速`zip/1`，不追求极限压缩；输出完整EXE及大小、SHA、源Digest、数据格式版本清单。

构建机首次需安装官方Inno编译器（用户运行App不需要）。可运行 `scripts\prepare-installer-tool.ps1`，它先验证官方SHA256及有效Pyrsys签名，再安装到`.tools`当前用户工具目录，不修改执行策略；如果本机策略不允许脚本，手动安装官方Inno Setup并设置`BIRDSHOT_ISCC`为其`ISCC.exe`。使用者需遵守编译工具许可。

若需要额外便携ZIP，源码提交后可以显式生成：

```powershell
& $env:BIRDSHOT_BUILD_PYTHON scripts\finalize_desktop.py --source-ref HEAD
```

封包会核对对应源码清单与指定Git提交相同，`BUILD.json`记录版本、提交及sourceDigest；旁边的`*.manifest.json`记录ZIP字节数和SHA256。当前源码如有未提交变更应先提交/刷新，不用旧SHA冒充当前包。

安装器最终源码绑定可用 `node scripts\build-installer.mjs --source-ref=HEAD`。该命令核对所有随包源码与Git提交一致，记录`sourceCommit`；普通开发构建未指定源码提交时明确记录null，不假称已发布提交。

## 首发后的更新与数据格式

“应用更新 → 检查更新”只有用户点击时才请求公开GitHub发行列表，不上传照片、位置或库路径，不使用GitHub凭据。首版流程为检查版本 → 打开官方发行页 → 用户下载并自行运行新的当前用户安装器；**不自动下载执行、不静默提权**。未找到桌面发行时显示暂无更新，`v0.2.0-models`等模型发行不会误认成App。

未来官方稳定桌面发行使用严格tag `birdshot-desktop-v0.3.0`（后续按版本变更），同发行需含：

- `BirdShot-0.3.0-Setup-x64.exe`
- `BirdShot-0.3.0-Setup-x64.manifest.json`：`version/platform=win32-x64/channel=stable/filename/bytes/sha256/dataSchemaVersion/minSupportedDataSchema`。

检查使用语义版本而非字典序，忽略draft/prerelease、不匹配平台与无对应安装器/清单的tag。清单和GitHub资产摘要若存在必须一致；失败/离线/限流明确说明。只打开准确`HicoderDR/BirdShot`官方HTTPS发行页，不执行任意远程URL。该规范不代表本轮已上传App Release；本轮安装器仅在本机交付。

Inno AppId、应用名`BirdShot`与userData目录固定，不随版本新建库。当前桌面库仍为schema1，0.2的来源、原路径、用户选输出、网络设置及任务缓存应直接读取，不制造多余迁移。未来需要真实schema变更时才引入显式迁移链：先备份小型关键状态/检查点，再写新文件并原子替换；不复制所有原片/运行时，不删除原选择。未知/未来schema不得被旧App静默重置或写入；显示不兼容说明、保留原文件。缓存按原策略指纹失效重建，不把裁剪、selected或原图一起清掉。

构建按`desktop_runtime.py`明确的生产依赖和wheel RECORD制作独立分发，不把`pyvenv.cfg`或激活器当可搬运行时；去除pip、ONNX导出测试数据、未用的评测包、Torch编译头/静态库及明确包内测试树，保留实测需用的`torch.testing/numpy.f2py/sklearn/scipy/setuptools`和元数据。原生DLL/pyd与需要真实路径的包资源保留；不使用每次启动自解压的单文件Python。许可完整归档而非删除，运行时选择清单/哈希随包。Windows长路径只对自有构建路径处理，不改系统策略。Electron下载校验官方npm包随附SHA256；备用镜像也必须匹配该哈希。

桌面构建还显式启用`--zip-pure-python`，只对已验证的12个固定纯Python包合并源码ZIP（651份源码）；包元数据保留且按需要镜像，`inspect`与资源导入已实测。不是对所有site-packages一刀切ZIP：原生275文件及八项必需资产哈希不变。运行时布局指纹变化时刷新整个自有runtime，不把旧磁盘包残留混进新ZIP布局。

源码结构：`desktop/main.mjs`为进程/能力生命周期，`preload.cjs`仅暴露类型化最小IPC；renderer不启用Node，`contextIsolation/sandbox/webSecurity`开启。远端百科只在核验HTTPS白名单后交系统浏览器，不能进入有权限主renderer。`scripts/desktop_backend.py`负责库、作业与服务，`gallery_navigation.py`负责不改判定的视图分组。

```powershell
npm.cmd run test:desktop
node tests\desktop-navigation.test.mjs
& $env:BIRDSHOT_BUILD_PYTHON -X utf8 -m unittest discover -s tests -p test_desktop_library.py
```

Windows命令行回归使用`-X utf8`，避免旧控制台编码不能打印中文诊断；App自己的子进程已显式设置UTF-8。

运行日志在App用户数据目录的`runtime.log`及任务日志；不要默认公开这些日志/私人索引。排查先看运行时模型状态、源路径权限、端口占用与health实例；不能用关闭防火墙或终止所有Python来“修好”。

## 本次独立制品验证范围

- 真正的`BirdShot.exe`搬到临时中文/空格路径启动，干净配置，PATH仅Windows System32；模型状态为`packaged-verified`，包内三模型大小/SHA通过。
- 两个不同父目录下的同名公开样例文件夹，3张真实授权鸟图，完成YOLO分割/姿态、Birder全类识别、裁剪与预览，实际产出4裁剪/2技术优选。为了确定验证分组，副本加入了**明确受控的EXIF日期**，不冒充原视频相机时间或选片真值。
- 目录去重、原片SHA不变、重启恢复、日期1组/文件夹2组/未知地点1组、筛选与同源原图/preview通过。模型阶段外连审计为0；可选百科在离线时允许失败占位。
- 实际启用局域网/全来源共享后本机与物理LAN IP的页面、JSON、裁剪、原图、preview和预先登记的公开参考小图可读，LAN管理POST拒绝；关闭共享后LAN新连接失败、本机仍可读。没有修改防火墙/全局网络类型或NAT。
- 独立占用5274时App明确报错，不接管占用者；显式采用建议端口后就绪。模拟包内姿态模型缺失时明确未就绪，不下载替代；测试后恢复原模型。
- 旧批次只读接入仍为5504/5504处理完成、544优选、日期13组/来源目录1组/未知地点1组，未重跑私人照片。完成拒识的unknown不误计为pending。

桌面流程自动化通过受控的系统选择器返回值调用真实preload/主进程/后台，产品使用Electron原生系统选择器。另实际请求了系统目录对话框，但当前自动化桌面无法用UI Automation观察该模态控件；因此不声称物理鼠标实选/原生取消按钮已实测。其他OS、真实手机、互联网路由和原生长按保存菜单均未据此获得验证。

## 打包/安装性能实测

旧包慢的主要文件数量来源是整份构建venv，不是几个生产模型：旧ZIP的35961文件中30800小于16KiB，site-packages35028项；其中Torch12128项、ONNX8220项（包含测试模型/协议数据），生产模型只有5项约209MiB。未测量或禁用防护软件，因此不把慢归咎于Defender。

同一Windows x64电脑、同一卷、各一次完整操作，未清除系统磁盘缓存；构建与安装方法不同，下面不是统计显著或跨机器保证：

| 项目 | 0.2.0 原便携包 | 0.3.0 安装候选 |
| --- | ---: | ---: |
| 安装内容文件数 | 35961 | 8049 |
| 展开字节 | 2006320308 | 1615819890 |
| 分发字节 | 800666326 | 725477226 |
| 生成耗时 | 1557.182秒（ZIP deflate6） | 497.371秒（Inno zip/1） |
| 全量解压/新目录安装 | 104.566秒（Python zipfile解压） | 96.704秒（含快捷方式及卸载登记、无诊断LOG） |

旧生成计时分两段：1498.327秒主体加58.855秒补全Windows深层路径，最终全部35961路径覆盖，原0.2.0包未改。新版额外两轮编译曾为444.217/155.403秒，缓存及并行活动影响明显，不能只选最低值宣称固定倍率。第一版精简运行时的带诊断LOG安装为200.139秒，不比旧ZIP快；经过有限纯Python ZIP及取消默认详尽安装日志后，最终候选无日志新目录96.704秒、覆盖88.455秒、卸载34.095秒。多项变化同时存在，不能把差异全部归因于某个开关。

文件数减少约77.6%、展开体积减少约19.5%；生成时间下降较明显，但此次安装只比旧ZIP裸解压略短，且包含不同工作、单次缓存条件，**不保证用户机器固定提速**。源码/BUILD清单更新会使最终EXE字节稍有变化，准确大小与SHA以最终旁边的manifest为准；以上保留被实际安装验证的候选数据，不伪造最终测量。可选`/LOG=文件`用于诊断，默认不持续写详细安装日志。安装器只安装/升级时解压，不影响平时启动路径。

实际无管理员安装到中文/空格目录后，瘦身runtime再次完成3张公开真鸟的全链，4裁剪/2优选、离线模型外连0、目录分组/恢复/共享验证通过；覆盖与卸载后真实库配置、原图SHA和用户自建哨兵仍在。单独验证旧schema1库读取不重新推理。真实增量测试还证明第1组pending裁剪和最终结果可HTTP浏览时第2组未检测、模型每轮热载一次；历史2帧追加1帧只检测/分类新增一张，旧job所有字节不变，旧/新三帧可比较。不兼容/身份不稳仍明确待审。
