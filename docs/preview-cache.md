# 按需私有图片预览

报告仍使用5174原有本机/授权内网入口，不另开图床或公网端口。只改变图片传输，不重新推理、评分或选片，也不修改原 JPEG / RAW。

## 固定 API

- `GET /preview/{frameId}/960.jpg`
- `GET /preview/{frameId}/1920.jpg`
- `GET /preview/{frameId}/3840.jpg`
- `GET /original/{frameId}`：完整且未经改写的原文件，普通图片请求保持inline。
- `GET /original/{frameId}?download=1`：相同原字节，明确attachment与原文件名，用于用户保存原文件。

预览的数字是**长边上限**，保持原比例、不裁成16:9、不上采样。仅接受本批已登记frameId；非法档位/路径参数400，未知ID404。不能指定任意文件路径、尺寸或目录。

## 编码与缓存

首次请求才生成相应档位，缓存于批次 `revisions/image-preview-cache-v1`。没有一次性生成1644张或复制原 JPEG。JPEG draft减少冷请求解码开销，按EXIF正确转正后缩放，JPEG88渐进编码、去额外EXIF；RGB ICC原样保留，必要的其他带ICC模式明确转sRGB。缓存不携带设备/位置EXIF。渐进JPEG不是减少整文件传输的替代，主要收益来自按需要发送较少像素。

键包含登记帧身份、源实际size/mtime_ns、档位、编码策略和Pillow版本。源变化产生新键；缓存命中直接复用已完成文件，不再解码源。相同键互斥生成，全局最多同时解码2张，临时文件原子写入并fsync。缓存预算512 MiB，按最近使用时间淘汰**这个独立目录中匹配自有哈希命名的派生JPEG**；不删除源图、裁剪、selected、JSON审计或其他命名文件。

响应为 `Cache-Control: private, max-age=0, must-revalidate`，提供ETag与Last-Modified，支持304。If-None-Match优先，Last-Modified按HTTP整秒比较；索引/状态仍no-store，不冻结进度。缓存失败显式HTTP错误和日志，不返回假图片。

前端按slot/DPR和缩放需求选择档位，CSS缩放本身不能自动提升图片源；放大升级后不应重置zoom/pan。原文件保存与当前预览保存是不同入口。没有引入IIIF/切片服务器、第三方图床、地图服务或新模型。

## 实测与验证

`measure_previews.py` 对3张真实登记照片、各3档，保存本机 `revisions/image-preview-measurements-v1.json`：原字节、实际尺寸、冷/命中请求耗时、headers、源SHA及原图保存一致性。测量是同一电脑经LAN地址请求，不是独立手机Wi-Fi性能。

其中真实Orientation8帧p000150：原JPEG为5,522,479字节，显示尺寸4128×6192：

| 长边档位 | 实际显示尺寸 | 字节 | 冷请求秒 | 命中秒 |
| --- | --- | ---: | ---: | ---: |
| 960 | 640×960 | 33,221 | 0.198 | 0.061 |
| 1920 | 1280×1920 | 132,070 | 0.373 | 0.060 |
| 3840 | 2560×3840 | 661,601 | 1.445 | 0.089 |

另外测量横图与登记集中最小字节图。所有原图编码尺寸相同，因此“小原图不上采样”使用临时受控fixture验证，不声称存在真实小原图。3张原SHA/mtime均未变，inline与download原字节完全相同，9个预览命中未重新编码、ETag条件请求304。临时fixture另验证小数mtime条件请求、源变化失效、8并发同key只生成1次、预算仅清自有缓存及路径边界；不更改真实源mtime。

```powershell
python tests\test_preview_cache.py
python scripts\measure_previews.py --job $job --base http://127.0.0.1:5174
```

不要把此实测外推为固定“快多少”，也不要把预览误当原始分辨率照片。真手机原生保存菜单仍需实际设备验证。
