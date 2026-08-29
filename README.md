# BirdShot

手机优先、完全本地推理的鸟类观察 PWA 原型。

当前版本使用 COCO-SSD Lite MobileNet v2 在浏览器内检测鸟类，以轻量 IoU tracker 维持多目标 ID，并在目标连续稳定后自动拍照。麦克风输入电平已接入；BirdNET TFJS 物种分类是下一阶段工作，界面不会在模型接入前伪造声音识别结果。

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
