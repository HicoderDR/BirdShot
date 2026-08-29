# BirdShot

手机优先、完全本地推理的鸟类观察 PWA 原型。

当前版本使用 YOLO11n ONNX 在浏览器内检测鸟类，以 ByteTrack 风格的高低置信度两阶段关联和运动预测维持多目标 ID。稳定目标会交给 EfficientNet Birds-525 进行二阶段视觉鸟种分类，并在确认后自动拍照。BirdNET TFJS 使用连续三秒麦克风音频在本地进行物种分类，达到阈值的候选会显示在声音面板并通过 toast 提示。

YOLO 权重约 10 MB，视觉鸟种分类模型约 34 MB，BirdNET 权重约 50 MB。首次运行需要联网下载，之后由 PWA 和浏览器缓存；推理过程不会上传画面或录音。

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
