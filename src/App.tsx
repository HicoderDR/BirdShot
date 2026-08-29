import { useCallback, useEffect, useRef, useState } from 'react'
import { updateTracks, type BirdTrack } from './lib/tracker'
import { YoloBirdDetector } from './lib/yoloDetector'
import { BirdSpeciesClassifier, type SpeciesPrediction } from './lib/speciesClassifier'
import './App.css'

type AppStatus = 'idle' | 'requesting' | 'loading-model' | 'running' | 'error'

type Snapshot = {
  id: number
  dataUrl: string
  time: string
  trackId: number
}

type BirdPrediction = {
  index: number
  scientificName: string
  commonName: string
  commonNameI18n: string
  confidence: number
  geoscore?: number
}

type BirdNetMessage = {
  message: string
  progress?: number
  pooled?: BirdPrediction[]
}

type Toast = BirdPrediction & {
  id: number
}

type AudioModelStatus = 'idle' | 'loading' | 'ready' | 'error'

const DETECTION_INTERVAL_MS = 80
const SNAPSHOT_COOLDOWN_MS = 5000
const AUDIO_SAMPLE_RATE = 48000
const AUDIO_WINDOW_SAMPLES = AUDIO_SAMPLE_RATE * 3
const AUDIO_THRESHOLD = 0.15
const AUDIO_CONFIDENT_THRESHOLD = 0.5
const VISUAL_CONFIDENT_THRESHOLD = 0.65
const SPECIES_CLASSIFICATION_INTERVAL_MS = 2500

function App() {
  const videoRef = useRef<HTMLVideoElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const captureRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const detectorRef = useRef<YoloBirdDetector | null>(null)
  const speciesClassifierRef = useRef<BirdSpeciesClassifier | null>(null)
  const tracksRef = useRef<BirdTrack[]>([])
  const animationRef = useRef<number | null>(null)
  const runningRef = useRef(false)
  const nextTrackIdRef = useRef(1)
  const lastSnapshotRef = useRef(0)
  const capturedTracksRef = useRef(new Set<number>())
  const audioContextRef = useRef<AudioContext | null>(null)
  const audioWorkletRef = useRef<AudioWorkletNode | null>(null)
  const birdNetWorkerRef = useRef<Worker | null>(null)
  const birdNetReadyRef = useRef(false)
  const audioBufferRef = useRef(new Float32Array(AUDIO_WINDOW_SAMPLES))
  const audioWriteIndexRef = useRef(0)
  const audioSamplesRef = useRef(0)
  const audioInferenceRef = useRef<number | null>(null)
  const audioInferenceInFlightRef = useRef(false)
  const notificationTimesRef = useRef(new Map<number, number>())
  const speciesClassificationInFlightRef = useRef(new Set<number>())
  const speciesClassificationTimesRef = useRef(new Map<number, number>())
  const speciesByTrackRef = useRef<Record<number, SpeciesPrediction[]>>({})
  const chinaBirdNamesRef = useRef(new Map<string, string>())
  const locationRef = useRef<{ latitude: number; longitude: number } | null>(null)
  const [status, setStatus] = useState<AppStatus>('idle')
  const [error, setError] = useState('')
  const [tracks, setTracks] = useState<BirdTrack[]>([])
  const [speciesByTrack, setSpeciesByTrack] = useState<Record<number, SpeciesPrediction[]>>({})
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [audioLevel, setAudioLevel] = useState(0)
  const [audioModelStatus, setAudioModelStatus] = useState<AudioModelStatus>('idle')
  const [audioModelProgress, setAudioModelProgress] = useState(0)
  const [audioRegionMode, setAudioRegionMode] = useState('中国常见种')
  const [soundPredictions, setSoundPredictions] = useState<BirdPrediction[]>([])
  const [toasts, setToasts] = useState<Toast[]>([])
  const [fps, setFps] = useState(0)

  const stop = useCallback(() => {
    runningRef.current = false
    if (animationRef.current !== null) window.clearTimeout(animationRef.current)
    if (audioInferenceRef.current !== null) window.clearTimeout(audioInferenceRef.current)
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    void audioContextRef.current?.close()
    audioContextRef.current = null
    audioWorkletRef.current = null
    birdNetWorkerRef.current?.terminate()
    birdNetWorkerRef.current = null
    birdNetReadyRef.current = false
    audioWriteIndexRef.current = 0
    audioSamplesRef.current = 0
    audioInferenceInFlightRef.current = false
    tracksRef.current = []
    capturedTracksRef.current.clear()
    speciesClassificationInFlightRef.current.clear()
    speciesClassificationTimesRef.current.clear()
    speciesByTrackRef.current = {}
    chinaBirdNamesRef.current.clear()
    locationRef.current = null
    setTracks([])
    setSpeciesByTrack({})
    setAudioLevel(0)
    setAudioModelStatus('idle')
    setAudioModelProgress(0)
    setAudioRegionMode('中国常见种')
    setSoundPredictions([])
    setToasts([])
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
      const species = speciesByTrackRef.current[track.id]?.[0]
      const speciesLabel = species
        ? ` · ${species.score >= VISUAL_CONFIDENT_THRESHOLD ? '' : '可能是 '}${species.label}`
        : ''
      const label = `鸟 #${track.id}${speciesLabel}`.slice(0, 34)
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

  const classifyTrack = useCallback((track: BirdTrack) => {
    const classifier = speciesClassifierRef.current
    const video = videoRef.current
    if (!classifier || !video || speciesClassificationInFlightRef.current.has(track.id)) return

    speciesClassificationInFlightRef.current.add(track.id)
    speciesClassificationTimesRef.current.set(track.id, Date.now())
    void classifier.classify(video, track.bbox)
      .then((predictions) => {
        if (!runningRef.current) return
        setSpeciesByTrack((current) => {
          const next = { ...current, [track.id]: predictions }
          speciesByTrackRef.current = next
          return next
        })
      })
      .catch((cause) => {
        setError(cause instanceof Error ? `鸟种分类失败：${cause.message}` : '鸟种分类失败')
      })
      .finally(() => {
        speciesClassificationInFlightRef.current.delete(track.id)
      })
  }, [])

  const detect = useCallback(async function runDetection() {
    if (!runningRef.current || !videoRef.current || !detectorRef.current) return

    const startedAt = performance.now()
    try {
      const birdDetections = await detectorRef.current.detect(videoRef.current)

      const result = updateTracks(tracksRef.current, birdDetections, nextTrackIdRef.current)
      nextTrackIdRef.current = result.nextId
      tracksRef.current = result.tracks
      setTracks(result.tracks)
      drawTracks(result.tracks)

      const stableTrack = result.tracks.find((track) => track.hits >= 3 && !capturedTracksRef.current.has(track.id))
      if (stableTrack && captureTrack(stableTrack)) capturedTracksRef.current.add(stableTrack.id)
      const trackForClassification = result.tracks.find((track) => (
        track.hits >= 3
        && track.missed === 0
        && Date.now() - (speciesClassificationTimesRef.current.get(track.id) ?? 0) >= SPECIES_CLASSIFICATION_INTERVAL_MS
        && !speciesClassificationInFlightRef.current.has(track.id)
      ))
      if (trackForClassification) classifyTrack(trackForClassification)
      setFps(Math.round(1000 / Math.max(1, performance.now() - startedAt)))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '视觉推理失败')
      setStatus('error')
      runningRef.current = false
      return
    }

    animationRef.current = window.setTimeout(() => void runDetection(), DETECTION_INTERVAL_MS)
  }, [captureTrack, classifyTrack, drawTracks])

  const showPredictionToasts = useCallback((predictions: BirdPrediction[]) => {
    const now = Date.now()
    predictions.forEach((prediction) => {
      const lastNotification = notificationTimesRef.current.get(prediction.index) ?? 0
      if (now - lastNotification < 8000) return
      notificationTimesRef.current.set(prediction.index, now)
      const toast = { ...prediction, id: now + prediction.index }
      setToasts((current) => [toast, ...current].slice(0, 3))
      window.setTimeout(() => {
        setToasts((current) => current.filter((item) => item.id !== toast.id))
      }, 6000)
    })
  }, [])

  const startAudioPipeline = useCallback(async (stream: MediaStream) => {
    const labelsResponse = await fetch('/models/china-birds-labels.json')
    if (!labelsResponse.ok) throw new Error(`中国鸟种名单加载失败：HTTP ${labelsResponse.status}`)
    const chinaLabels = await labelsResponse.json() as Array<{ scientificName: string; commonName: string }>
    chinaBirdNamesRef.current = new Map(chinaLabels.map((label) => [label.scientificName, label.commonName]))

    const audioContext = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE })
    await audioContext.resume()
    if (audioContext.sampleRate !== AUDIO_SAMPLE_RATE) {
      throw new Error(`浏览器音频采样率为 ${audioContext.sampleRate} Hz，BirdNET 需要 48000 Hz`)
    }
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    const source = audioContext.createMediaStreamSource(stream)
    const highPass = audioContext.createBiquadFilter()
    highPass.type = 'highpass'
    highPass.frequency.value = 200
    highPass.Q.value = 0.707
    source.connect(highPass)
    highPass.connect(analyser)
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

    if (!audioContext.audioWorklet) throw new Error('当前浏览器不支持 AudioWorklet')
    await audioContext.audioWorklet.addModule('/audio-processor.js')
    const worklet = new AudioWorkletNode(audioContext, 'birdshot-audio-processor')
    const silentOutput = audioContext.createGain()
    silentOutput.gain.value = 0
    highPass.connect(worklet)
    worklet.connect(silentOutput)
    silentOutput.connect(audioContext.destination)
    audioWorkletRef.current = worklet
    audioBufferRef.current = new Float32Array(AUDIO_WINDOW_SAMPLES)

    worklet.port.onmessage = ({ data }: MessageEvent<Float32Array>) => {
      const buffer = audioBufferRef.current
      data.forEach((sampleValue) => {
        buffer[audioWriteIndexRef.current] = sampleValue
        audioWriteIndexRef.current = (audioWriteIndexRef.current + 1) % buffer.length
      })
      audioSamplesRef.current = Math.min(buffer.length, audioSamplesRef.current + data.length)
    }

    setAudioModelStatus('loading')
    const workerParams = new URLSearchParams({
      tf: 'https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.22.0/dist/tf.min.js',
      root: 'https://birdnet-team.github.io/real-time-pwa/models',
      lang: 'zh',
    })
    const worker = new Worker(`/birdnet-worker.js?${workerParams}`)
    birdNetWorkerRef.current = worker

    navigator.geolocation?.getCurrentPosition(
      ({ coords }) => {
        locationRef.current = { latitude: coords.latitude, longitude: coords.longitude }
        setAudioRegionMode('中国地域与月份先验')
        if (birdNetReadyRef.current) {
          worker.postMessage({ message: 'area-scores', ...locationRef.current })
        }
      },
      () => {
        locationRef.current = null
        setAudioRegionMode('中国常见种')
      },
      { enableHighAccuracy: false, maximumAge: 24 * 60 * 60 * 1000, timeout: 8000 },
    )

    const scheduleInference = () => {
      if (!runningRef.current) return
      if (
        birdNetReadyRef.current
        && !audioInferenceInFlightRef.current
        && audioSamplesRef.current >= AUDIO_WINDOW_SAMPLES
      ) {
        const circular = audioBufferRef.current
        const windowed = new Float32Array(circular.length)
        const writeIndex = audioWriteIndexRef.current
        windowed.set(circular.subarray(writeIndex))
        windowed.set(circular.subarray(0, writeIndex), circular.length - writeIndex)
        audioInferenceInFlightRef.current = true
        worker.postMessage({
          message: 'predict',
          pcmAudio: windowed,
          overlapSec: 1.5,
          sensitivity: 1,
        }, [windowed.buffer])
      }
      audioInferenceRef.current = window.setTimeout(scheduleInference, 500)
    }

    worker.onmessage = ({ data }: MessageEvent<BirdNetMessage>) => {
      if (['load_model', 'warmup', 'load_geomodel', 'load_labels'].includes(data.message)) {
        setAudioModelProgress(data.progress ?? 0)
      }
      if (data.message === 'loaded') {
        birdNetReadyRef.current = true
        setAudioModelStatus('ready')
        setAudioModelProgress(100)
        if (locationRef.current) {
          worker.postMessage({ message: 'area-scores', ...locationRef.current })
        }
      }
      if (data.message === 'pooled' && data.pooled) {
        audioInferenceInFlightRef.current = false
        const predictions = data.pooled
          .filter((prediction) => chinaBirdNamesRef.current.has(prediction.scientificName))
          .filter((prediction) => !locationRef.current || (prediction.geoscore ?? 0) >= 0.05)
          .map((prediction) => ({
            ...prediction,
            commonNameI18n: chinaBirdNamesRef.current.get(prediction.scientificName) ?? prediction.commonNameI18n,
          }))
          .toSorted((a, b) => b.confidence - a.confidence)
          .slice(0, 3)
        setSoundPredictions(predictions)
        showPredictionToasts(predictions.filter((prediction) => prediction.confidence >= AUDIO_THRESHOLD))
      }
    }
    worker.onerror = (event) => {
      setAudioModelStatus('error')
      setError(`BirdNET 加载失败：${event.message || '请检查网络后重试'}`)
    }
    scheduleInference()
  }, [showPredictionToasts])

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
          channelCount: { ideal: 1 },
          sampleRate: { ideal: AUDIO_SAMPLE_RATE },
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
      await startAudioPipeline(stream)

      setStatus('loading-model')
      detectorRef.current ??= new YoloBirdDetector()
      speciesClassifierRef.current ??= new BirdSpeciesClassifier()
      await Promise.all([
        detectorRef.current.load(),
        speciesClassifierRef.current.load(),
      ])
      setStatus('running')
      void detect()
    } catch (cause) {
      streamRef.current?.getTracks().forEach((track) => track.stop())
      birdNetWorkerRef.current?.terminate()
      birdNetWorkerRef.current = null
      birdNetReadyRef.current = false
      void audioContextRef.current?.close()
      audioContextRef.current = null
      runningRef.current = false
      setError(cause instanceof Error ? cause.message : '无法启动设备')
      setStatus('error')
    }
  }, [detect, startAudioPipeline])

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

      <aside className="toast-stack" aria-live="polite">
        {toasts.map((toast) => (
          <div className="bird-toast" key={toast.id}>
            <span className="toast-icon">♪</span>
            <div>
              <small>检测到鸟鸣</small>
              <strong>
                {toast.confidence >= AUDIO_CONFIDENT_THRESHOLD ? '' : '可能是 '}
                {toast.commonNameI18n || toast.commonName}
              </strong>
              <em>{toast.scientificName}</em>
            </div>
            <b>{Math.round(toast.confidence * 100)}%</b>
          </div>
        ))}
      </aside>

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
                  <div>
                    <strong>
                      {speciesByTrack[track.id]?.[0]
                        ? `${speciesByTrack[track.id][0].score >= VISUAL_CONFIDENT_THRESHOLD ? '' : '可能是 '}${speciesByTrack[track.id][0].label}`
                        : track.hits >= 3 ? '正在分类鸟种…' : '鸟类目标'}
                    </strong>
                    <small>
                      {speciesByTrack[track.id]?.slice(1).map((prediction) => prediction.label).join(' · ')
                        || (track.hits >= 3 ? '追踪稳定' : '正在确认')}
                    </small>
                  </div>
                  <b>
                    {speciesByTrack[track.id]?.[0]
                      ? `${Math.round(speciesByTrack[track.id][0].score * 100)}%`
                      : `${Math.round(track.score * 100)}%`}
                  </b>
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
            <span>
              {audioModelStatus === 'loading' && `模型 ${audioModelProgress}%`}
              {audioModelStatus === 'ready' && `BirdNET 就绪 · ${audioRegionMode}`}
              {audioModelStatus === 'error' && '模型错误'}
              {audioModelStatus === 'idle' && '待命'}
            </span>
          </div>
          <div className="sound-visual">
            {Array.from({ length: 24 }, (_, index) => (
              <i key={index} style={{ height: `${Math.max(8, status === 'running' ? audioLevel * (0.3 + ((index * 7) % 10) / 12) : 8)}%` }} />
            ))}
          </div>
          {soundPredictions.length > 0 ? (
            <div className="sound-results">
              {soundPredictions.map((prediction) => (
                <div className="sound-result" key={prediction.index}>
                  <div>
                    <strong>
                      {prediction.confidence >= AUDIO_CONFIDENT_THRESHOLD
                        ? ''
                        : prediction.confidence >= AUDIO_THRESHOLD ? '可能是 ' : '低置信度 · '}
                      {prediction.commonNameI18n || prediction.commonName}
                    </strong>
                    <small>{prediction.scientificName}</small>
                  </div>
                  <b>{Math.round(prediction.confidence * 100)}%</b>
                </div>
              ))}
            </div>
          ) : (
            <p className="model-note">
              {audioModelStatus === 'ready'
                ? '正在分析连续 3 秒音频窗；达到 15% 阈值的候选会显示并弹出提示。'
                : 'BirdNET 首次需要下载约 50 MB 权重，之后可从浏览器缓存加载。'}
            </p>
          )}
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
          <article><span className="ready">已接入</span><h3>YOLO11n ONNX</h3><p>640 px 输入 · WASM 本地推理 · bird 专用解码</p><b>视觉检测</b></article>
          <article><span className="ready">已接入</span><h3>ByteTrack</h3><p>高低置信度两阶段关联、运动预测与稳定 ID</p><b>多目标追踪</b></article>
          <article><span className="ready">已接入</span><h3>China Birds ConvNeXt</h3><p>中国常见 373 种 · 对稳定追踪框输出前三候选</p><b>视觉鸟种分类</b></article>
          <article><span className="ready">已接入</span><h3>BirdNET + 中国先验</h3><p>中国常见种过滤 · 可选 GPS 与月份出现概率</p><b>声音识别</b></article>
        </div>
      </section>

      <footer>BirdShot Prototype · LOCAL FIRST · 2026</footer>
    </main>
  )
}

export default App
