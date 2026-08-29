# BirdShot

手机优先、完全本地推理的鸟类观察 PWA 原型。

当前版本使用 YOLO11n ONNX 在浏览器内检测鸟类，以 ByteTrack 风格的高低置信度两阶段关联和运动预测维持多目标 ID。稳定目标会交给中国常见鸟类 ConvNeXt 模型进行 373 类二阶段视觉分类，并在确认后自动拍照。BirdNET TFJS 使用连续三秒麦克风音频在本地分类，再由相同的中国常见种名单以及可选 GPS/月份出现概率过滤结果。

视觉置信度达到 65%、声音置信度达到 50% 时直接显示鸟名；较低置信度明确标记为“可能是”或“低置信度”。YOLO 权重约 10 MB，中国视觉模型约 47 MB，BirdNET 权重约 50 MB。首次运行需要联网下载远程模型，之后由 PWA 和浏览器缓存；推理过程不会上传画面或录音。

## 本地运行

```bash
npm install
npm run dev -- --host
```

`localhost` 在电脑本机可直接使用。手机测试必须通过 HTTPS 部署地址或 HTTPS 开发隧道访问；普通局域网 HTTP 地址无法取得相机和麦克风权限。

## 构建

```bash
npm run build
npm run preview -- --host
```

构建产物是可安装的 PWA。模型候选、许可证注意事项和评测门槛见 [docs/model-evaluation.md](docs/model-evaluation.md)。
