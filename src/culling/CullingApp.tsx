// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useRef, useState } from 'react'
import { CullingDetector, DETECTOR_REVISION } from './detector'
import { groupByModifiedTime, matchSubjects, recommend, selectInstance, type Analysis, type Box, type Decision } from './scoring'
import './culling.css'

type Photo = { id: string; name: string; url: string; group: string; time?: number; hash?: string; capturedAt?: string }
type LocalGroup = { id: string; title: string; photos: { name: string; url: string; capturedAt: string | null }[] }
type LocalManifest = { notice: string; jpegCount: number; rawCount: number; warnings: string[]; groups: LocalGroup[] }
type Manifest = {
  title: string; notice: string; author: string; source: string; license: string; licenseUrl: string; changes: string
  kind?: string; resultUrl?: string
  frames: { name: string; url: string; time?: number; sha256: string }[]
}
type Report = { version: number; generatedAt: string; source: string; photos: Photo[]; results: Analysis[]; overrides: Record<string, Decision>; useEyeComparison?: boolean }
const labels: Record<Decision, string> = { keep: '优先保留', review: '待审', reject: '建议淘汰' }
const sourceLink = '/culling/source.zip'
const POSE_LABELS = ['喙', '左眼', '右眼', '头顶', '左肩部', '右肩部']
const INSTANCE_COLORS = ['#c5eb75', '#ffb879', '#83cafa', '#eb96c7', '#83dfca', '#c2a7ff']

async function readSamples(path = '/culling/samples/manifest.json'): Promise<{ manifest: Manifest; report: Report | null }> {
  const response = await fetch(path)
  if (!response.ok || !response.headers.get('content-type')?.includes('json')) {
    throw new Error('样例尚未准备好，请按运行说明执行 python scripts\\prepare-culling.py')
  }
  const manifest: Manifest = await response.json()
  if (!Array.isArray(manifest.frames) || !manifest.frames.length) throw new Error('样例清单为空')
  const stored = await fetch(manifest.resultUrl ?? '/culling/results.json')
  const report: Report | null = stored.ok && stored.headers.get('content-type')?.includes('json') ? await stored.json() : null
  return { manifest, report }
}

async function loadImage(url: string) {
  const image = new Image()
  image.src = url
  await image.decode()
  if (!image.naturalWidth || !image.naturalHeight) throw new Error('图像解码后没有有效尺寸')
  if (image.naturalWidth * image.naturalHeight > 60_000_000) throw new Error('超过 6000 万像素，请先导出较小的 JPEG')
  return image
}

function EyeZoom({ photo, patch }: { photo: Photo; patch: Box | null }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    const context = canvas.current?.getContext('2d')
    context?.clearRect(0, 0, 240, 240)
    if (patch) {
      void loadImage(photo.url).then((image) => {
        if (!active || !context) return
        setError('')
        context.imageSmoothingEnabled = false
        context.drawImage(image, ...patch, 0, 0, 240, 240)
      }).catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause))
      })
    }
    return () => { active = false }
  }, [photo.url, patch])
  return <div className="eye-zoom">
    {patch ? <canvas ref={canvas} width={240} height={240} aria-label="模型候选区域原像素放大，眼睛可见性需覆核" />
      : <span>眼区尚未可靠定位<br />不以头部清晰度替代</span>}
    {error && <p role="alert">{error}</p>}
  </div>
}

export default function CullingApp() {
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [photos, setPhotos] = useState<Photo[]>([])
  const [results, setResults] = useState<Analysis[]>([])
  const [selected, setSelected] = useState('')
  const [overrides, setOverrides] = useState<Record<string, Decision>>({})
  const [filter, setFilter] = useState('all')
  const [overlay, setOverlay] = useState(true)
  const [maskOverlay, setMaskOverlay] = useState(true)
  const [poseOverlay, setPoseOverlay] = useState(true)
  const [confirmedSubject, setConfirmedSubject] = useState(false)
  const [useEyeComparison, setUseEyeComparison] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState('')
  const [origin, setOrigin] = useState('sample')
  const [reportTime, setReportTime] = useState('')
  const [singleGroup, setSingleGroup] = useState(false)
  const [localManifest, setLocalManifest] = useState<LocalManifest | null>(null)
  const [localGroup, setLocalGroup] = useState('')
  const detector = useRef<CullingDetector | null>(null)
  const objectUrls = useRef<string[]>([])

  function applySamples({ manifest: data, report }: { manifest: Manifest; report: Report | null }) {
    setError('')
    setResults([])
    setReportTime('')
    setOverrides({})
    setOrigin('sample')
    setFilter('all')
    setSingleGroup(false)
    setConfirmedSubject(false)
    setUseEyeComparison(false)
    setManifest(data)
    setProgress('')
    const nextPhotos = data.frames.map((frame): Photo => ({
      id: frame.name, name: frame.name, url: frame.url, group: data.title, time: frame.time, hash: frame.sha256,
    }))
    setPhotos(nextPhotos)
    setSelected(nextPhotos[0].id)
    for (const url of objectUrls.current) URL.revokeObjectURL(url)
    objectUrls.current = []
    if (report) {
      if (report.version === 3 && report.source === data.source && report.photos.length === nextPhotos.length
        && nextPhotos.every((p) => report.photos.some((old) => old.id === p.id && old.hash === p.hash))
        && report.results.length === nextPhotos.length) {
        setResults(recommend(report.results, { useEyes: false }))
        setReportTime(report.generatedAt)
      } else {
        setProgress('已有结果与当前样例不匹配，请重新分析')
      }
    }
  }

  async function openSamples(path?: string) {
    try {
      applySamples(await readSamples(path))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  useEffect(() => {
    let active = true
    void readSamples().then((data) => {
      if (active) applySamples(data)
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : String(cause))
    })
    return () => {
      active = false
      for (const url of objectUrls.current) URL.revokeObjectURL(url)
    }
  }, [])

  function importPhotos(files: FileList | null) {
    if (!files?.length) return
    const list = [...files]
    if (list.length > 100) { setError('简单 Demo 每次最多导入 100 张，请分批处理'); return }
    if (list.some((file) => !/\.(jpe?g|png)$/i.test(file.name))) {
      setError('只支持 JPEG / PNG；RAW 和其他格式请先导出'); return
    }
    if (list.some((file) => file.size > 80 * 1024 * 1024)) { setError('单张文件不能超过 80 MB'); return }
    for (const url of objectUrls.current) URL.revokeObjectURL(url)
    objectUrls.current = []
    const keyed = list.map((file, i) => ({ name: `${i}/${file.name}`, lastModified: file.lastModified, file }))
    const groups = groupByModifiedTime(keyed)
    const nextPhotos = keyed.toSorted((a, b) => a.lastModified - b.lastModified
      || a.file.name.localeCompare(b.file.name, undefined, { numeric: true })).map((entry): Photo => {
      const url = URL.createObjectURL(entry.file)
      objectUrls.current.push(url)
      return { id: entry.name, name: entry.file.name, url, group: groups.get(entry.name)! }
    })
    setPhotos(nextPhotos)
    setSelected(nextPhotos[0].id)
    setResults([])
    setOverrides({})
    setFilter('all')
    setOrigin('import')
    setError('')
    setProgress('')
    setReportTime('')
    setSingleGroup(false)
    setConfirmedSubject(false)
    setUseEyeComparison(false)
  }

  function selectLocalGroup(group: LocalGroup) {
    if (group.photos.length > 100) { setError('该时间组超过 100 张，请使用手动文件导入选择较小一组'); return }
    const next = group.photos.map((p): Photo => ({
      id: p.name, name: p.name, url: p.url, group: group.id, capturedAt: p.capturedAt ?? undefined,
    }))
    for (const url of objectUrls.current) URL.revokeObjectURL(url)
    objectUrls.current = []
    setPhotos(next)
    setSelected(next[0].id)
    setResults([])
    setOverrides({})
    setFilter('all')
    setOrigin('local')
    setLocalGroup(group.id)
    setReportTime('')
    setError('')
    setSingleGroup(false)
    setConfirmedSubject(false)
    setUseEyeComparison(false)
    setProgress('按 EXIF 时间得到候选组；分析后检查主体尺度/位置/朝向，不能据此确认同一个体。')
  }

  async function openLocal() {
    try {
      const response = await fetch('/culling/local/manifest.json')
      if (!response.ok || !response.headers.get('content-type')?.includes('json')) {
        throw new Error('未配置本机照片目录。按运行说明准备本地清单并设置 BIRDSHOT_PHOTO_DIR；也可直接导入 JPEG / PNG。')
      }
      const data: LocalManifest = await response.json()
      if (!data.groups.length) throw new Error('目录没有可解码的 JPEG / PNG；RAW 请先导出')
      setLocalManifest(data)
      selectLocalGroup(data.groups.find((g) => g.photos.length >= 3 && g.photos.length <= 100) ?? data.groups[0])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  async function analyze() {
    setBusy(true)
    setError('')
    setOverrides({})
    setResults([])
    setReportTime('')
    try {
      setProgress('加载本地鸟体 / 鸟眼 ONNX 模型…')
      if (detector.current?.revision !== DETECTOR_REVISION) {
        await detector.current?.dispose?.()
        detector.current = new CullingDetector()
      }
      await detector.current.load()
      const next: Analysis[] = []
      for (let i = 0; i < photos.length; i++) {
        const photo = photos[i]
        setProgress(`正在分析 ${i + 1} / ${photos.length} · ${photo.name}`)
        const image = await loadImage(photo.url)
        next.push(await detector.current.analyze(image, photo.id, singleGroup ? '手动连续组' : photo.group,
          (message) => setProgress(`${i + 1}/${photos.length} · ${photo.name} · ${message}`)))
        setResults([...next])
        await new Promise((resolve) => setTimeout(resolve, 0))
      }
      const matched = matchSubjects(next)
      setResults(recommend(confirmedSubject ? matched.map((frame) => ({ ...frame, subject: `${frame.group}:confirmed` })) : matched, { useEyes: useEyeComparison }))
      setReportTime(new Date().toISOString())
      setProgress(`已完成 ${photos.length} 张 · 多鸟与无眼仍有鸟体、分割及独立技术指标；推荐依据明确区分`)
    } catch (cause) {
      setError(`分析未完成，不产生淘汰推荐：${cause instanceof Error ? cause.message : String(cause)}。请检查本地模型及图像文件。`)
    } finally {
      setBusy(false)
    }
  }

  const photo = photos.find((p) => p.id === selected) ?? photos[0]
  const analysis = results.find((r) => r.id === photo?.id)
  const selectedBird = analysis?.instances?.find((bird) => bird.id === analysis.selectedInstance)
  const allInstances = results.flatMap((frame) => frame.instances ?? [])
  const legacyResults = results.some((frame) => !frame.instances)
  const decision = (id: string): Decision => overrides[id] ?? results.find((r) => r.id === id)?.decision ?? 'review'
  const visible = photos.filter((p) => filter === 'all' || decision(p.id) === filter)
  const counts = {
    keep: photos.filter((p) => decision(p.id) === 'keep').length,
    review: photos.filter((p) => decision(p.id) === 'review').length,
    reject: photos.filter((p) => decision(p.id) === 'reject').length,
  }

  function chooseBird(id: number) {
    if (!analysis) return
    const chosen = analysis.instances?.find((bird) => bird.id === id)
    if (!chosen) return
    setUseEyeComparison(false)
    const next = results.map((frame) => {
      const target = frame.id === analysis.id ? chosen
        : frame.group === analysis.group && chosen.trackId !== null ? frame.instances?.find((bird) => bird.trackId === chosen.trackId) : null
      return target ? selectInstance(frame, target.id) : frame
    })
    setResults(recommend(confirmedSubject ? next.map((frame) => ({ ...frame, subject: `${frame.group}:confirmed` })) : next, { useEyes: false }))
  }

  function confirmIdentity(value: boolean) {
    setConfirmedSubject(value)
    const next = results.map((frame) => frame.selectedInstance ? selectInstance(frame, frame.selectedInstance) : frame)
    setResults(recommend(value ? next.map((frame) => ({ ...frame, subject: `${frame.group}:confirmed` })) : next, { useEyes: useEyeComparison }))
  }

  function exportReport() {
    const report: Report = {
      version: 3, generatedAt: reportTime, source: origin === 'sample' ? manifest?.source ?? '' : '用户本地导入',
      photos: photos.map((p) => ({ ...p, url: origin === 'sample' ? p.url : '' })), results, overrides, useEyeComparison,
    }
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'birdshot-culling-results.json'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <div className="culling">
    <header className="cull-nav">
      <a className="cull-brand" href="/">◈ <strong>BirdShot</strong><span> / 筛片实验室</span></a>
      <span className="local-pill"><i /> 完全本地 · 原片只读</span>
      <a className="back-link" href="/">返回观鸟</a>
    </header>
    <main className="cull-main">
      <div className="cull-heading">
        <div><p className="eyebrow">BIRD CULLING / RESEARCH DEMO</p><h1>留住那一眼<span>。</span></h1>
          <p>逐鸟看分割与姿态。眼睛是可选指标，让每一次取舍都有明确依据。</p></div>
        <div className="cull-actions">
          <button disabled={busy} onClick={() => void openSamples()}>载入真实样例</button>
          <button disabled={busy} onClick={() => void openSamples('/culling/samples/multi-manifest.json')}>公开多鸟图</button>
          <button disabled={busy} onClick={() => void openLocal()}>本机照片目录</button>
          <label className={`import-button ${busy ? 'disabled' : ''}`}>导入 JPEG / PNG
            <input aria-label="导入自己的 JPEG 或 PNG 照片" type="file" accept=".jpg,.jpeg,.png" multiple
              disabled={busy} onChange={(e) => { importPhotos(e.target.files); e.target.value = '' }} />
          </label>
        </div>
      </div>
      <div className="model-version">修复版 {DETECTOR_REVISION} · 鸟体 YOLO11s-seg / 640 输入 + 原图局部精查 · 姿态 eye-pose-v0 六点
        {legacyResults && <strong> 当前是旧版结果，请重新分析或刷新页面。</strong>}</div>
      <div className="source-notice">
        <span className="notice-icon">i</span><div>
          {origin === 'sample' ? <><strong>{manifest?.title ?? '真实连续鸟帧样例'}</strong>
            <span> · {manifest?.notice ?? '视频连续帧，非相机连拍，无人工质量标签。'}</span><br />
            <small>{manifest?.author ?? 'Giles Laurent'} · {manifest?.license ?? 'CC BY-SA 4.0'} · {manifest?.changes ?? '原分辨率抽帧，JPEG 编码。'}
              {' '}<a href={manifest?.source} target="_blank" rel="noreferrer">查看来源 ↗</a></small></>
            : origin === 'local' ? <><strong>本机原目录只读，不会上传。</strong><span> {localManifest?.notice}</span><br />
              <small>{localManifest?.jpegCount} 张 JPEG / PNG，{localManifest?.rawCount} 张 ARW 未加载。{localManifest?.warnings.length ? `${localManifest.warnings.length} 张读取失败，详见本机清单。` : ''}</small></>
            : <><strong>你的本地照片，不会上传。</strong><span>默认按文件修改时间分组：相邻不超过 2 秒、整组不超过 10 秒；不是 EXIF 拍摄时间。</span>
              <br /><label className="manual-group"><input type="checkbox" checked={singleGroup} disabled={busy || results.length > 0}
                onChange={(e) => setSingleGroup(e.target.checked)} /> 我确认这些照片为同一连续拍摄组（分析前可选）</label></>}
        </div>
      </div>
      {origin === 'local' && localManifest && <label className="local-group-picker">按拍摄时间选择候选连续组
          <select aria-label="选择本机 EXIF 时间候选组" value={localGroup} disabled={busy}
            onChange={(e) => { const group = localManifest.groups.find((g) => g.id === e.target.value); if (group) selectLocalGroup(group) }}>
            {localManifest.groups.map((g) => <option key={g.id} value={g.id}>{g.title}</option>)}
          </select>
      </label>}
      {error && <p className="cull-error" role="alert">{error}</p>}
      <div className="cull-summary">
        <div><span>本组照片</span><strong>{photos.length}<small> 张</small></strong></div>
        <div className="keep-stat"><span>优先保留</span><strong>{counts.keep}</strong></div>
        <div><span>待审 / 未判定</span><strong>{counts.review}</strong></div>
        <div><span>建议淘汰 · 不删除</span><strong>{counts.reject}</strong></div>
        <button className="analyze-button" disabled={busy || !photos.length} onClick={() => void analyze()}>
          {busy ? '本地分析中…' : results.length ? '重新分析这组照片' : '分析这组照片'} <span>↗</span>
        </button>
      </div>
      <div className="analysis-status" role="status" aria-live="polite">
        {progress || (reportTime ? `显示已实际运行的样例结果 · ${new Date(reportTime).toLocaleString('zh-CN')} · 可重新分析`
          : '模型定位置信度不代表清晰度；眼睛不确定的照片永远保留待审。')}
        {busy && <progress value={results.length} max={photos.length} />}
      </div>
      {results.length > 0 && <section className="stage-diagnostics" aria-label="实际分析阶段覆盖率">
        <div><strong>{results.filter((frame) => frame.birdCount > 0).length}/{photos.length} 帧 · {allInstances.length} 只</strong><span>鸟体检出</span></div>
        <div><strong>{allInstances.filter((bird) => bird.mask?.path).length}/{allInstances.length}</strong><span>实际实例分割</span></div>
        <div><strong>{allInstances.filter((bird) => bird.posePoints.some((point) => point.confidence >= .65)).length}/{allInstances.length}</strong><span>至少一个高置信度候选点</span></div>
        <div><strong>{results.filter((frame) => frame.sharpness !== null).length}/{results.length}</strong><span>模型候选眼区可测（非可见性真值）</span></div>
        <div><strong>{results.filter((frame) => frame.rank !== null).length}/{results.length}</strong><span>同对象参与比较</span></div>
      </section>}
      {results.length > 0 && <label className="identity-confirm"><input type="checkbox" checked={confirmedSubject} disabled={busy}
        onChange={(event) => confirmIdentity(event.target.checked)} /> 我已逐帧覆核：各帧选中的是同一只鸟（可用于自动匹配失效的连拍）
        <small>默认只比较同一候选跟踪对象；编号匹配不是身份保证。</small></label>}
      {results.length > 0 && <label className="identity-confirm eye-confirm"><input type="checkbox" checked={useEyeComparison} disabled={busy}
        onChange={(event) => { setUseEyeComparison(event.target.checked); setResults(recommend(results, { useEyes: event.target.checked })) }} />
        我已检查本组选中鸟的眼睛可见且候选位置正确，允许眼区参与推荐
        <small>默认只按独立主体指标推荐；高置信度眼点也可能是背向误定位，未覆核不参与眼部建议。</small></label>}
      {photo && <div className="cull-workspace">
        <section className="photo-panel">
          <div className="panel-toolbar"><div><span className={`decision-tag ${decision(photo.id)}`}>{labels[decision(photo.id)]}</span>
            <strong>{photo.name}</strong><small>{photo.time !== undefined ? `视频 ${photo.time.toFixed(3)} 秒` : photo.capturedAt ?? photo.group}</small></div>
            <div className="overlay-toggles">
              <label><input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} /> 鸟框</label>
              <label><input type="checkbox" checked={maskOverlay} onChange={(e) => setMaskOverlay(e.target.checked)} /> 分割</label>
              <label><input type="checkbox" checked={poseOverlay} onChange={(e) => setPoseOverlay(e.target.checked)} /> 姿态</label>
            </div>
          </div>
          <div className="photo-stage">
            <img src={photo.url} alt={`原始照片 ${photo.name}`} />
            {(overlay || maskOverlay || poseOverlay) && analysis && <svg className="detection-overlay" viewBox={`0 0 ${analysis.width} ${analysis.height}`}>
              {(analysis.instances ?? []).map((bird) => {
                const color = INSTANCE_COLORS[(bird.id - 1) % INSTANCE_COLORS.length]
                const isSelected = bird.id === analysis.selectedInstance
                return <g key={bird.id}>
                  {maskOverlay && bird.mask?.path && <path data-instance-mask={bird.id} d={bird.mask.path} fill={color} fillOpacity={isSelected ? .25 : .13}
                    fillRule="evenodd" stroke={color} strokeWidth={Math.max(2, analysis.width / 900)} />}
                  {overlay && <rect className="bird-hitbox" x={bird.body[0]} y={bird.body[1]} width={bird.body[2]} height={bird.body[3]}
                    stroke={color} strokeWidth={Math.max(3, analysis.width / (isSelected ? 600 : 1000))} fill="transparent"
                    role="button" tabIndex={0} aria-label={`选择鸟 #${bird.id}`} onClick={() => chooseBird(bird.id)}
                    onKeyDown={(event) => { if (event.key === 'Enter') chooseBird(bird.id) }}>
                    <title>{`鸟 #${bird.id} · 检测 ${(bird.bodyConfidence * 100).toFixed(1)}% · 候选轨迹 ${bird.trackId ?? '未匹配'}`}</title>
                  </rect>}
                  {overlay && <text x={bird.body[0]} y={Math.max(analysis.height / 60, bird.body[1] - analysis.height / 150)}
                    fill={color} fontSize={analysis.width / 65} stroke="#172016" strokeWidth={analysis.width / 2400} paintOrder="stroke">
                    {`#${bird.id} ${(bird.bodyConfidence * 100).toFixed(0)}%`}</text>}
                  {poseOverlay && bird.posePoints.filter((point) => point.confidence >= .65 && point.x >= 0 && point.y >= 0
                    && point.x <= analysis.width && point.y <= analysis.height
                    && (!bird.eyeIssue || (point.index !== 1 && point.index !== 2))).map((point) => <g key={point.index}>
                    <circle data-pose-point={`${bird.id}-${point.index}`} cx={point.x} cy={point.y} r={Math.max(5, analysis.width / 450)} fill={color} stroke="#102016" strokeWidth={2} />
                    {isSelected && <text x={point.x + analysis.width / 150} y={point.y} fill={color} fontSize={analysis.width / 85}
                      stroke="#172016" strokeWidth={analysis.width / 2400} paintOrder="stroke">{POSE_LABELS[point.index]}</text>}
                  </g>)}
                </g>
              })}
              {analysis.patch && <rect x={analysis.patch[0]} y={analysis.patch[1]} width={analysis.patch[2]} height={analysis.patch[3]}
                stroke="#ffb879" strokeWidth={Math.max(3, analysis.width / 650)} fill="none" />}
              {analysis.eye && analysis.patch && <circle cx={analysis.eye.x} cy={analysis.eye.y} r={Math.max(5, analysis.width / 700)} fill="#ffb879" />}
            </svg>}
          </div>
          <div className="photo-bottom"><span>颜色区分鸟实例 · 真实模型掩码与六点候选姿态 · 可点击鸟框选主体</span>
            <a href={photo.url} target="_blank" rel="noreferrer">查看原图 ↗</a>
          </div>
        </section>
        <aside className="detail-panel">
          <div className="detail-heading"><h2>关注鸟 #{analysis?.selectedInstance ?? '—'}</h2><span>INSTANCE</span></div>
          {(analysis?.instances?.length ?? 0) > 0 && <div className="instance-picker" aria-label="切换关注鸟实例">
            {analysis!.instances!.map((bird) => <button key={bird.id} disabled={busy} aria-pressed={bird.id === analysis?.selectedInstance}
              onClick={() => chooseBird(bird.id)} style={{ borderColor: INSTANCE_COLORS[(bird.id - 1) % INSTANCE_COLORS.length] }}>
              #{bird.id} · {(bird.bodyConfidence * 100).toFixed(0)}%</button>)}
          </div>}
          <div className="score-row body-score"><span>分割主体内部纹理<br /><small>原像素 · 非眼部合焦</small></span>
            <strong>{analysis?.bodySharpness != null ? Math.round(analysis.bodySharpness).toLocaleString('zh-CN') : '未测量'}</strong></div>
          {selectedBird?.maskIssue && <p className="metric-warning">{selectedBird.maskIssue}</p>}
          <EyeZoom key={`${photo.id}-${analysis?.patch?.join('-') ?? 'pending'}`} photo={photo} patch={analysis?.patch ?? null} />
          <div className="zoom-caption">模型候选区域 · 未必是可见眼睛 · 原像素测量</div>
          <div className="score-row"><span>候选眼区梯度<br /><small>{useEyeComparison ? '已启用覆核后比较' : '未覆核，不参与推荐'}</small></span><strong>{analysis?.sharpness != null ? Math.round(analysis.sharpness).toLocaleString('zh-CN') : '未判定'}</strong></div>
          {analysis?.issue && <p className="metric-warning">眼部：{analysis.issue}</p>}
          <div className="relative-score"><div><span>{analysis?.basis === 'body' ? '主体纹理相对最佳（非眼部）' : '眼区相对可比最佳'}</span><strong>{analysis?.relative != null ? `${Math.round(analysis.relative * 100)}%` : '—'}</strong></div>
            <div className="score-track"><i style={{ width: `${(analysis?.relative ?? 0) * 100}%` }} /></div>
            <small>{analysis?.rank ? `同一候选对象 ${analysis.cohortSize} 帧中的第 ${analysis.rank} 名` : '至少 2 张同对象可比帧；两帧或主体指标不自动淘汰'}</small>
          </div>
          <dl className="detection-details">
            <div><dt>鸟体定位置信度</dt><dd>{analysis?.bodyConfidence != null ? `${(analysis.bodyConfidence * 100).toFixed(1)}%` : '—'}</dd></div>
            <div><dt>鸟眼定位置信度</dt><dd>{analysis?.eye ? `${(analysis.eye.confidence * 100).toFixed(1)}%` : '—'}</dd></div>
            <div><dt>测量区域 / 原图</dt><dd>{analysis?.patch ? `${analysis.patch[2]}×${analysis.patch[3]}` : '—'} / {analysis ? `${analysis.width}×${analysis.height}` : '—'}</dd></div>
            <div><dt>主体有效原像素</dt><dd>{analysis?.bodyPixels?.toLocaleString('zh-CN') ?? '—'}</dd></div>
            <div><dt>实例分割</dt><dd>{selectedBird?.mask?.path ? '真实模型掩码' : '未判定'}</dd></div>
            <div><dt>跨帧候选对象</dt><dd>{selectedBird?.trackId != null ? `轨迹 ${selectedBird.trackId}` : '未匹配；可人工覆核'}</dd></div>
          </dl>
          <div className="pose-table"><strong>模型支持的六点姿态（无人体骨架）</strong>
            {POSE_LABELS.map((label, index) => {
              const point = selectedBird?.posePoints.find((item) => item.index === index)
              const uncertainEye = (index === 1 || index === 2) && selectedBird?.eyeIssue
              return <div key={label}><span>{label}</span><span>{point && point.confidence >= .65 && !uncertainEye
                ? `${(point.confidence * 100).toFixed(1)}%`
                : point ? `不确定 ${(point.confidence * 100).toFixed(0)}%` : '未定位'}</span></div>
            })}
            {selectedBird?.poseIssue && <small>{selectedBird.poseIssue}</small>}
          </div>
          {analysis?.detectionMode && <p className="metric-warning">推理：{analysis.detectionMode}</p>}
          <div className="reason-box"><strong>为什么这样推荐？</strong>
            <p>{analysis?.reason ?? '点击分析后显示实际检测与测量结果。'}</p>
            {overrides[photo.id] && <p>你已手动标注为「{labels[overrides[photo.id]]}」。算法建议仍保留在报告中。</p>}
          </div>
          <div className="manual-actions" aria-label="人工覆核标注">
            {(['keep', 'review', 'reject'] as const).map((d) => <button key={d} disabled={busy}
              aria-pressed={overrides[photo.id] === d} onClick={() => setOverrides((old) => ({ ...old, [photo.id]: d }))}>
              {d === 'keep' ? '保留' : d === 'review' ? '待审' : '淘汰'}</button>)}
          </div>
          <small className="readonly-note">仅修改标签，不删除、不移动、不改写原照片。</small>
        </aside>
      </div>}
      <section className="filmstrip-section">
        <div className="filmstrip-heading"><h2>连续组 <small>按原始顺序浏览</small></h2>
          <div className="filter-tabs" aria-label="按推荐状态筛选">{['all', 'keep', 'review', 'reject'].map((value) =>
            <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>
              {value === 'all' ? '全部' : labels[value as Decision]}</button>)}</div>
          <button disabled={busy || !reportTime || legacyResults || results.length !== photos.length} onClick={exportReport}>导出标签 JSON ↓</button>
        </div>
        <div className="filmstrip">{visible.map((p, i) => <button key={p.id} className={`frame-card ${p.id === photo?.id ? 'selected' : ''}`}
          onClick={() => setSelected(p.id)} aria-label={`查看 ${p.name}，${labels[decision(p.id)]}`}>
          <div><img src={p.url} alt="" loading="lazy" /><span className={`frame-dot ${decision(p.id)}`} /></div>
          <strong>{photos.indexOf(p) + 1 < 10 ? '0' : ''}{photos.indexOf(p) + 1} <small>{p.time !== undefined ? `${p.time.toFixed(2)}s` : p.name}</small></strong>
          <span>{results.find((r) => r.id === p.id)?.sharpness != null
            ? `眼区 ${Math.round(results.find((r) => r.id === p.id)!.sharpness!).toLocaleString('zh-CN')}`
            : results.find((r) => r.id === p.id)?.bodySharpness != null ? `主体 ${Math.round(results.find((r) => r.id === p.id)!.bodySharpness!).toLocaleString('zh-CN')} · 非眼部`
            : busy && i >= results.length ? '等待分析' : '等待 / 需覆核'}</span>
        </button>)}</div>
      </section>
      <footer className="cull-footer">
        <div><strong>分割与姿态是模型预测，不做最后决定。</strong><p>主体纹理不等于眼部合焦；无眼、多鸟仍有可用分析，不自动淘汰。仅眼区 3+ 可比帧使用 35% 建议淘汰规则。<br />
          无人工真值评测；梯度受姿态、噪声、压缩和曝光影响。候选跟踪可能串鸟，请逐帧选择并覆核。</p></div>
        <div><a href={sourceLink} target="_blank" rel="noreferrer">源代码 / AGPL-3.0 ↗</a>
          <a href="https://huggingface.co/synthet/eye-pose-v0" target="_blank" rel="noreferrer">鸟眼模型与限制 ↗</a>
          <a href={manifest?.licenseUrl ?? 'https://creativecommons.org/licenses/by-sa/4.0/'} target="_blank" rel="noreferrer">公开样例 {manifest?.license ?? 'CC BY-SA 4.0'} ↗</a>
          <small>鸟眼模型由 CUB 训练；训练图像限非商业研究与教育。<br />本 Demo 用于本地研究，不作商业授权保证。</small></div>
      </footer>
    </main>
  </div>
}
