# BirdShot 桌面版（Windows x64）

桌面版是 **Electron原生桌面壳复用现有Web图墙和本机Python推理**，不是把所有控件改写为系统原生组件。文件夹选择器是系统对话框；图墙、蓝色候选标签、参考图卡和手势浏览器沿用同一实现。PWA及独立报告服务仍保留。

## 完整便携包

当前验证版本为`0.2.0`，Windows x64；本机构建产物在被Git忽略的`desktop-artifacts`，不提交运行时、模型或ZIP到普通Git。发布时以随包版本/哈希清单和实际测量为准，不把`npm run desktop:dev`当作制品。

完整包包含：

- `BirdShot.exe`与Electron内置Chromium/Node，主界面不需要系统Node。
- `resources\runtime\python`：可重定位CPython3.12、Torch CPU、ONNX Runtime、OpenCV、Pillow、Birder及依赖DLL，不引用开发venv。
- `resources\runtime\node`：独立Node24，用于现有TypeScript跟踪/技术推荐worker，不依赖系统PATH。
- `resources\backend\public\culling\models`：YOLO11s-seg与六点姿态ONNX；`models\birder`为固定官方10000全类分类权重及配置，`assets\taxonomy`为中英文名录。
- `resources\backend\assets-manifest.json`：必需资产路径、大小、SHA256与版本；启动核对模型，不在第一次运行时偷偷联网下载。
- `resources\licenses`：代码AGPL、模型卡、Python依赖许可清单及对应源代码ZIP；Electron的MIT/Chromium和Python/各wheel许可证仍随原分发保留。

体积包含真正运行时和权重，不能按“一个Electron壳”的大小估算。未包含巨大BioCLIP/Huge研究权重、懂鸟Key、个人照片、裁剪、EXIF清单、研究对比结果或个人配置。

便携ZIP解压后运行`BirdShot.exe`即可。建议用较短的安装位置，支持中文和空格；大量第三方许可/开发文件层级很深，Windows内置`tar.exe`或支持长路径的解压工具更合适，不需要更改Windows长路径/执行策略：

```powershell
New-Item -ItemType Directory -Force "C:\Apps\BirdShot 桌面" | Out-Null
tar.exe -xf .\BirdShot-0.2.0-win-x64.zip -C "C:\Apps\BirdShot 桌面"
```

制品未代码签名。不要关闭Defender、绕过组织策略或默认提权；若组织策略拦截，请先核对来源/SHA并由管理员审核。其他操作系统当前仅有源码设计，未经实际打包验证。

## 首次打开与照片库

默认元数据、任务、派生裁剪、缓存和日志保存在Electron的用户数据目录，不写入原片目录或包内resources。界面展示当前输出位置，可用“选择输出目录”修改；不能放在原片文件夹内或包内resources。

1. “添加照片文件夹”打开系统目录选择器。可以添加多个来源，包含子目录；路径规范化、相同/嵌套来源按原照片身份去重。
2. 扫描与顺序处理状态保留在库中。真实执行检测、实例分割、姿态、原像素主体指标和Birder全类竞争，之后显示裁剪图墙。不能识别/损坏/目录失效会明确报错，不默认为空或失焦。
3. 关闭App后可重开恢复；同一目录不会因再次添加而变成重复照片。移除来源只是解绑，不删除源照片和历史输出。
4. 已有BirdShot批次用“加载已有批次”选择其输出目录。读取既有JSON/裁剪并在用户数据目录生成聚合视图，不要求重新跑一遍旧照片；不要将已有任务当作新照片目录加入。

普通新用户可从空库开始。支持JPEG/PNG像素分析；RAW仍仅登记/配对，不宣称RAW显影或全部格式支持。原片保持只读，裁剪与预览不是原文件替代。

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
```

`desktop:build`生成完整可运行目录；已存在同版运行时、仅修改本项目源码时可用`node scripts\build-desktop.mjs --refresh`增量刷新（先退出该包），不重复复制整个Python分发。源码提交后生成一个可分发ZIP及大小/SHA清单：

```powershell
& $env:BIRDSHOT_BUILD_PYTHON scripts\finalize_desktop.py --source-ref HEAD
```

封包会核对对应源码清单与指定Git提交相同，`BUILD.json`记录版本、提交及sourceDigest；旁边的`*.manifest.json`记录ZIP字节数和SHA256。当前源码如有未提交变更应先提交/刷新，不用旧SHA冒充当前包。

构建使用当前Python的标准库/依赖制作独立分发，不把`pyvenv.cfg`或venv激活器当可搬运行时；排除pyc/开发缓存，保留wheel许可。Windows深层Torch路径通过受控扩展路径处理，不改系统策略。Electron下载校验官方npm包随附SHA256；网络限速时的备用镜像也必须完整匹配该哈希，否则不运行。

源码结构：`desktop/main.mjs`为进程/能力生命周期，`preload.cjs`仅暴露类型化最小IPC；renderer不启用Node，`contextIsolation/sandbox/webSecurity`开启。远端百科只在核验HTTPS白名单后交系统浏览器，不能进入有权限主renderer。`scripts/desktop_backend.py`负责库、作业与服务，`gallery_navigation.py`负责不改判定的视图分组。

```powershell
npm.cmd run test:desktop
node tests\desktop-navigation.test.mjs
& $env:BIRDSHOT_BUILD_PYTHON -m unittest discover -s tests -p test_desktop_library.py
```

运行日志在App用户数据目录的`runtime.log`及任务日志；不要默认公开这些日志/私人索引。排查先看运行时模型状态、源路径权限、端口占用与health实例；不能用关闭防火墙或终止所有Python来“修好”。

## 本次独立制品验证范围

- 真正的`BirdShot.exe`搬到临时中文/空格路径启动，干净配置，PATH仅Windows System32；模型状态为`packaged-verified`，包内三模型大小/SHA通过。
- 两个不同父目录下的同名公开样例文件夹，3张真实授权鸟图，完成YOLO分割/姿态、Birder全类识别、裁剪与预览，实际产出4裁剪/2技术优选。为了确定验证分组，副本加入了**明确受控的EXIF日期**，不冒充原视频相机时间或选片真值。
- 目录去重、原片SHA不变、重启恢复、日期1组/文件夹2组/未知地点1组、筛选与同源原图/preview通过。模型阶段外连审计为0；可选百科在离线时允许失败占位。
- 实际启用局域网/全来源共享后本机与物理LAN IP的页面、JSON、裁剪、原图、preview和预先登记的公开参考小图可读，LAN管理POST拒绝；关闭共享后LAN新连接失败、本机仍可读。没有修改防火墙/全局网络类型或NAT。
- 独立占用5274时App明确报错，不接管占用者；显式采用建议端口后就绪。模拟包内姿态模型缺失时明确未就绪，不下载替代；测试后恢复原模型。
- 旧批次只读接入仍为5504/5504处理完成、544优选、日期13组/来源目录1组/未知地点1组，未重跑私人照片。完成拒识的unknown不误计为pending。

桌面流程自动化通过受控的系统选择器返回值调用真实preload/主进程/后台，产品使用Electron原生系统选择器。另实际请求了系统目录对话框，但当前自动化桌面无法用UI Automation观察该模态控件；因此不声称物理鼠标实选/原生取消按钮已实测。其他OS、真实手机、互联网路由和原生长按保存菜单均未据此获得验证。
