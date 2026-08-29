import { useCallback, useEffect, useRef, useState } from 'react'
import type { ObjectDetection } from '@tensorflow-models/coco-ssd'
import { updateTracks, type BirdTrack } from './lib/tracker'
import './App.css'

type AppStatus = 'idle' | 'requesting' | 'loading-model' | 'running' | 'error'

type Snapshot = {
  id: number
  dataUrl: string
  time: string
  trackId: number
}

const DETECTION_INTERVAL_MS = 180
const SNAPSHOT_COOLDOWN_MS = 5000

function App() {
  const videoRef = useRef<HTMLVideoElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const captureRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const detectorRef = useRef<ObjectDetection | null>(null)
  const tracksRef = useRef<BirdTrack[]>([])
  const animationRef = useRef<number | null>(null)
  const runningRef = useRef(false)
  const nextTrackIdRef = useRef(1)
  const lastSnapshotRef = useRef(0)
  const capturedTracksRef = useRef(new Set<number>())
  const audioContextRef = useRef<AudioContext | null>(null)
  const [status, setStatus] = useState<AppStatus>('idle')
  const [error, setError] = useState('')
  const [tracks, setTracks] = useState<BirdTrack[]>([])
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [audioLevel, setAudioLevel] = useState(0)
  const [fps, setFps] = useState(0)

  const stop = useCallback(() => {
    runningRef.current = false
    if (animationRef.current !== null) window.clearTimeout(animationRef.current)
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    void audioContextRef.current?.close()
    audioContextRef.current = null
    tracksRef.current = []
    capturedTracksRef.current.clear()
    setTracks([])
    setAudioLevel(0)
    setStatus('idle')
  }, [])

  useEffect(() => stop, [stop])

  const captureTrack = useCallback((track: BirdTrack) => {
    const video = videoRef.current
    const canvas = captureRef.current
    if (!video || !canvas || Date.now() - lastSnapshotRef.current < SNAPSHOT_COOLDOWN_MS) return false

    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    const context = canvas.getContext('2d')
    if (!context) return false

    context.drawImage(video, 0, 0)
    const [x, y, width, height] = track.bbox
    context.strokeStyle = '#d8ff3e'
    context.lineWidth = Math.max(4, canvas.width / 180)
    context.strokeRect(x, y, width, height)
    lastSnapshotRef.current = Date.now()
    setSnapshots((current) => [
      {
        id: Date.now(),
        dataUrl: canvas.toDataURL('image/jpeg', 0.88),
        time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        trackId: track.id,
      },
      ...current,
    ].slice(0, 12))
    return true
  }, [])

  const drawTracks = useCallback((nextTracks: BirdTrack[]) => {
    const video = videoRef.current
    const canvas = overlayRef.current
    if (!video || !canvas) return

    const width = video.videoWidth
    const height = video.videoHeight
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width
      canvas.height = height
    }

    const context = canvas.getContext('2d')
    if (!context) return
    context.clearRect(0, 0, width, height)
    context.font = `600 ${Math.max(16, width / 35)}px system-ui`
    context.textBaseline = 'top'

    nextTracks.forEach((track) => {
      const [x, y, boxWidth, boxHeight] = track.bbox
      const label = `鸟 #${track.id}  ${Math.round(track.score * 100)}%`
      const labelWidth = context.measureText(label).width + 20
      context.strokeStyle = '#d8ff3e'
      context.lineWidth = Math.max(3, width / 240)
      context.strokeRect(x, y, boxWidth, boxHeight)
      context.fillStyle = '#d8ff3e'
      context.fillRect(x, Math.max(0, y - 34), labelWidth, 34)
      context.fillStyle = '#11211b'
      context.fillText(label, x + 10, Math.max(3, y - 31))
    })
  }, [])

  const detect = useCallback(async function runDetection() {
    if (!runningRef.current || !videoRef.current || !detectorRef.current) return

    const startedAt = performance.now()
    try {
      const predictions = await detectorRef.current.detect(videoRef.current, 20, 0.35)
      const birdDetections = predictions
        .filter((prediction) => prediction.class === 'bird')
        .map(({ bbox, score }) => ({ bbox, score }))

      const result = updateTracks(tracksRef.current, birdDetections, nextTrackIdRef.current)
      nextTrackIdRef.current = result.nextId
      tracksRef.current = result.tracks
      setTracks(result.tracks)
      drawTracks(result.tracks)

      const stableTrack = result.tracks.find((track) => track.hits >= 3 && !capturedTracksRef.current.has(track.id))
      if (stableTrack && captureTrack(stableTrack)) capturedTracksRef.current.add(stableTrack.id)
      setFps(Math.round(1000 / Math.max(1, performance.now() - startedAt)))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '视觉推理失败')
      setStatus('error')
      runningRef.current = false
      return
    }

    animationRef.current = window.setTimeout(() => void runDetection(), DETECTION_INTERVAL_MS)
  }, [captureTrack, drawTracks])

  const startAudioMeter = useCallback((stream: MediaStream) => {
    const audioContext = new AudioContext()
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    audioContext.createMediaStreamSource(stream).connect(analyser)
    audioContextRef.current = audioContext
    const levels = new Uint8Array(analyser.frequencyBinCount)

    const sample = () => {
      if (!runningRef.current) return
      analyser.getByteFrequencyData(levels)
      const mean = levels.reduce((sum, value) => sum + value, 0) / levels.length
      setAudioLevel(Math.min(100, Math.round(mean * 1.3)))
      window.requestAnimationFrame(sample)
    }
    sample()
  }, [])

  const start = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('当前浏览器不支持相机/麦克风访问，请使用最新版 Chrome 或 Safari。')
      setStatus('error')
      return
    }

    setError('')
    setStatus('requesting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      })
      streamRef.current = stream
      if (!videoRef.current) throw new Error('相机预览初始化失败')
      videoRef.current.srcObject = stream
      await videoRef.current.play()
      runningRef.current = true
      startAudioMeter(stream)

      setStatus('loading-model')
      if (!detectorRef.current) {
        await import('@tensorflow/tfjs-backend-webgl')
        const cocoSsd = await import('@tensorflow-models/coco-ssd')
        detectorRef.current = await cocoSsd.load({ base: 'lite_mobilenet_v2' })
      }
      setStatus('running')
      void detect()
    } catch (cause) {
      streamRef.current?.getTracks().forEach((track) => track.stop())
      runningRef.current = false
      setError(cause instanceof Error ? cause.message : '无法启动设备')
      setStatus('error')
    }
  }, [detect, startAudioMeter])

  const statusText = {
    idle: '设备待命',
    requesting: '等待设备授权',
    'loading-model': '正在加载本地模型',
    running: '本地识别中',
    error: '启动失败',
  }[status]

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="/" aria-label="BirdShot 首页">
          <span className="brand-mark">B</span>
          <span>BirdShot <small>FIELD LAB</small></span>
        </a>
        <span className={`status ${status}`}>
          <i />
          {statusText}
        </span>
      </header>

      <section className="hero">
        <div className="eyebrow">ON-DEVICE BIRD INTELLIGENCE</div>
        <h1>看见，也听见<br />身边的鸟。</h1>
        <p>相机与声音只在你的设备上处理。第一阶段实验版已支持鸟类框选、持续追踪和自动拍照。</p>
      </section>

      <section className="camera-card">
        <div className="camera-stage">
          <video ref={videoRef} playsInline muted />
          <canvas ref={overlayRef} className="overlay" />
          <canvas ref={captureRef} hidden />
          {status !== 'running' && (
            <div className="camera-empty">
              <div className="focus-corners"><span /><span /><span /><span /></div>
              <strong>{status === 'loading-model' ? '加载视觉模型…' : '准备观察'}</strong>
              <p>建议使用后置相机，并保持设备稳定</p>
            </div>
          )}
          {status === 'running' && (
            <div className="live-badge">LIVE · {fps} FPS</div>
          )}
        </div>

        <div className="controls">
          <button className="primary" type="button" onClick={status === 'running' ? stop : start} disabled={status === 'requesting' || status === 'loading-model'}>
            <span className="button-icon">{status === 'running' ? '■' : '●'}</span>
            {status === 'running' ? '停止观察' : '启动相机与麦克风'}
          </button>
          <div className="privacy">不上传画面或录音</div>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
      </section>

      <section className="live-grid">
        <article className="panel detected-panel">
          <div className="panel-title">
            <div><span className="section-number">01</span><h2>画面目标</h2></div>
            <span>{tracks.length} 个</span>
          </div>
          {tracks.length > 0 ? (
            <div className="target-list">
              {tracks.map((track) => (
                <div className="target" key={track.id}>
                  <span className="target-id">#{track.id}</span>
                  <div><strong>鸟类目标</strong><small>{track.hits >= 3 ? '追踪稳定' : '正在确认'}</small></div>
                  <b>{Math.round(track.score * 100)}%</b>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-row">启动后，多个目标会在这里分别显示</div>
          )}
        </article>

        <article className="panel sound-panel">
          <div className="panel-title">
            <div><span className="section-number">02</span><h2>环境声音</h2></div>
            <span>{status === 'running' ? '监听中' : '待命'}</span>
          </div>
          <div className="sound-visual">
            {Array.from({ length: 24 }, (_, index) => (
              <i key={index} style={{ height: `${Math.max(8, status === 'running' ? audioLevel * (0.3 + ((index * 7) % 10) / 12) : 8)}%` }} />
            ))}
          </div>
          <p className="model-note"><strong>BirdNET TFJS</strong> 音频分类适配中；当前只显示真实输入电平，不生成虚假物种判断。</p>
        </article>
      </section>

      <section className="gallery">
        <div className="section-heading">
          <div><span className="section-number">03</span><h2>自动捕获</h2></div>
          <p>目标连续命中 3 帧后拍摄，5 秒内不重复触发。</p>
        </div>
        {snapshots.length > 0 ? (
          <div className="photo-strip">
            {snapshots.map((snapshot) => (
              <a href={snapshot.dataUrl} download={`birdshot-${snapshot.id}.jpg`} className="photo" key={snapshot.id}>
                <img src={snapshot.dataUrl} alt={`鸟类目标 ${snapshot.trackId}`} />
                <span>#{snapshot.trackId} · {snapshot.time}</span>
              </a>
            ))}
          </div>
        ) : (
          <div className="gallery-empty">稳定识别到鸟类后，照片会自动出现在这里</div>
        )}
      </section>

      <section className="models">
        <div className="section-heading">
          <div><span className="section-number">04</span><h2>模型实验路线</h2></div>
          <p>先验证延迟、准确率与耗电，再决定生产模型。</p>
        </div>
        <div className="model-grid">
          <article><span className="ready">已接入</span><h3>COCO-SSD Lite</h3><p>浏览器基线 · 只判断 bird，不区分物种</p><b>视觉检测</b></article>
          <article><span className="next">下一步</span><h3>YOLO Nano + ByteTrack</h3><p>更强的小目标、多鸟遮挡与稳定 ID</p><b>检测与追踪</b></article>
          <article><span className="next">下一步</span><h3>BirdNET TFJS</h3><p>3 秒音频窗 · 本地物种分类 · 中文标签</p><b>声音识别</b></article>
        </div>
      </section>

      <footer>BirdShot Prototype · LOCAL FIRST · 2026</footer>
    </main>
  )
}

export default App
