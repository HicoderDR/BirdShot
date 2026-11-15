# 拍摄地点来源检查与地点日期导航

“补地点”只丰富本机报告和索引，不写改 JPEG / RAW 的 EXIF，不移动文件，也不重新识别、分割或裁剪照片。用户举出的场所名不是已确认的照片映射，不能套用；用户地区参考同样不是 GPS 或拍摄点。

## 只读来源核查

- 使用 ExifTool 13.63 官方 Windows64 发行包，来源为官方主页指向的 SourceForge；执行前匹配官方公布的 SHA-256。工具只在 `.tools` 缓存，不提交。
- 完整检查登记的 1628 个 Sony ARW 配对，不将 16 个 JPEG-only 算作失败。仅导出必要 GPS、GeoTIFF、IPTC/XMP位置文字和原始拍摄时间，不输出序列号/机主等无关标签；禁用派生反向地理定位。
- 全部1628个RAW另用独立的经典TIFF目录解析遍历主IFD / ExifIFD / SubIFD的 GPSInfo 指针，不解码高分辨率像素或读取压缩像素块。
- 另对全部1644原JPEG读取必要 GPS/IPTC/XMP 的 Location / City / Sublocation / State / Country 等字段；只查同目录、同stem或完整照片文件名明确配对的 XMP / DOP 等元数据旁文件，不读取无关旁文件内容。
- 每文件对照原清点及读取前后的大小、mtime，解析失败/不支持/警告/空值/异常坐标分别保存，不把解析失败当作缺GPS。

本次实际结果：RAW **1628/1628**、JPEG **1644/1644** 均解析成功；均无GPS或地点文字，错误/不支持/警告0。另1个明确配对DOP成功解析、同样无位置，1个孤立DOP仅按文件名排除而未读内容。全部RAW的独立TIFF核查也没有GPSInfo目录，源文件大小和修改时间不变。

这意味着当前文件没有可自动恢复的场所来源，不是“通过时区确定在江苏”。未调用任何反向地理编码、地图或模型服务，未上传照片或坐标。

## 派生记录

在原批次输出的 `revisions` 下保存：

- `paired-raw-location-v1.json`：工具来源/版本/校验、逐配对RAW的最小位置字段、状态、独立目录结果及完整性记录。
- `jpeg-sidecar-location-v1.json`：登记JPEG和明确配对sidecar的最小位置字段与读取状态。
- `capture-enriched-v2.json`：保留原JPEG时间，补充RAW/其他位置来源的核查说明与可追溯地名字段；没有来源仍为null。
- `shoot-groups-v1.json`：全部原JPEG的地点/相机日期导航及各单位计数。
- `location-and-shoot-groups-validation.json`：完整覆盖、原文件不变、逐图/逐实例继承及分类/技术选择不变的验证。

原始JPEG时间缓存、旧批次结果及分类原始输出不覆盖。任何有效经纬度必须保留方向，基准未知不能当成 WGS84 画地图；单纯焦点/画面内坐标不当地理位置。若元数据仅有明确场所文字，可用于地点导航，但没有GPS就不画地图。

## 拍摄导航不是连拍重新分组

`capture` 可增加 `placeName/placeSource/placeNote`，`item.shootGroup` 与对应 `frame.shootGroup` 使用相同对象。组名为“地点简名 - YYMMDD”，没有地点显示“未知地点 - YYMMDD”，无时间显示“地点 - 日期未知”。

日期直接取相机原始 ISO 时间的本地日，保留其offset，不经浏览器时区或UTC转换；ID由真实地点键和相机日期稳定散列生成，不是文件顺序别名。同日不同真实地点、同地点不同日期分开；未知地点不能推断公园。如果只有未命名的真实GPS点，保留坐标键以免把同日不同点混为一个确认场所。

顶层 `shootGroups` 中 `photoCount` 是登记原JPEG数、`cropCount` 是全部裁剪实例数、`preferredCount` 是新版优选裁剪数，不是三个“照片数”。本次生成13个未知地点日期组，三列合计 **1644 / 5504 / 544**；包括4个没有检测裁剪的日期组，16个JPEG-only也计入原图数。此导航不改变原 `groupId`、候选跟踪、相对评分cohort、质量排名或544个优选。

```powershell
python scripts\inspect_paired_raw.py --job $job --exiftool $exiftool --package $verifiedZip
python scripts\inspect_location_sources.py --job $job --exiftool $exiftool --package $verifiedZip
python scripts\shoot_groups.py --job $job
python scripts\gallery_build.py --job $job --revision $revision
python scripts\verify_location_metadata.py --job $job
```

英文常用名另外从已缓存的官方 eBird/Clements v2025 英文名录按学名精确匹配，字段为 `englishName`，缺失为null；不把拉丁学名或原始taxonomy类别ID当英文名。只是显示名丰富化，原分类候选、全类别概率和技术选择保持不变。
