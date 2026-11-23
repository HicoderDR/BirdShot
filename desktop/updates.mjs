// SPDX-License-Identifier: AGPL-3.0-only
// User-requested public release checks only. Never downloads or executes an installer.
const repository = 'HicoderDR/BirdShot'
export const desktopTag = /^birdshot-desktop-v(\d+\.\d+\.\d+)$/
const assetHosts = new Set(['github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'])

export function compareVersions(left, right) {
  if (![left, right].every(value => /^\d+\.\d+\.\d+$/.test(value))) throw new Error('无效的桌面版本号')
  for (let position = 0; position < 3; position++) {
    const difference = Number(left.split('.')[position]) - Number(right.split('.')[position])
    if (difference) return Math.sign(difference)
  }
  return 0
}

export function releasePage(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.hostname === 'github.com' && !url.username && !url.password &&
      !url.port && !url.search && !url.hash &&
      /^\/HicoderDR\/BirdShot\/releases\/tag\/birdshot-desktop-v\d+\.\d+\.\d+$/.test(url.pathname)
  } catch { return false }
}

function downloadAsset(value, filename) {
  return value === `https://github.com/${repository}/releases/download/birdshot-desktop-v${filename.match(/\d+\.\d+\.\d+/)?.[0]}/${filename}`
}

async function boundedJson(url, fetcher, asset = false) {
  let target = url
  for (let redirects = 0; redirects <= 3; redirects++) {
    const parsed = new URL(target)
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.port ||
        !(asset ? assetHosts.has(parsed.hostname) : parsed.hostname === 'api.github.com')) throw new Error('更新资料来源不在官方白名单')
    const response = await fetcher(target, { headers: { 'User-Agent': 'BirdShot-update-check', Accept: 'application/json' },
      redirect: 'manual', signal: AbortSignal.timeout(12000) })
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      if (!response.headers.get('location')) throw new Error('官方更新重定向缺失')
      target = new URL(response.headers.get('location'), target).href
      continue
    }
    if (response.status === 404 && !asset) return []
    if (!response.ok) throw new Error(response.status === 403 || response.status === 429 ?
      'GitHub限流或访问受限，请稍后重试' : `更新接口返回HTTP ${response.status}`)
    const length = Number(response.headers.get('content-length') || 0)
    if (length > 2 * 1024 * 1024) throw new Error('更新资料超过大小限制')
    const reader = response.body.getReader(), chunks = []
    let bytes = 0
    try {
      while (true) {
        const chunk = await reader.read()
        if (chunk.done) break
        bytes += chunk.value.length
        if (bytes > 2 * 1024 * 1024) throw new Error('更新资料超过大小限制')
        chunks.push(chunk.value)
      }
    } finally { await reader.cancel() }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  }
  throw new Error('更新资料重定向次数超过限制')
}

export async function checkUpdates(currentVersion, fetcher = fetch, offline = false) {
  const base = { currentVersion, dataSchemaVersion: 1, automaticInstallation: false }
  if (offline) return { ...base, status: 'error', message: '当前为离线模式，不连接GitHub；本机筛片仍可使用' }
  try {
    compareVersions(currentVersion, currentVersion)
    const releases = await boundedJson(`https://api.github.com/repos/${repository}/releases?per_page=100`, fetcher)
    if (!Array.isArray(releases)) throw new Error('GitHub发行列表格式错误')
    const candidates = releases.filter(release => !release.draft && !release.prerelease &&
      desktopTag.test(release.tag_name || '') && releasePage(release.html_url || ''))
      .sort((a, b) => compareVersions(b.tag_name.match(desktopTag)[1], a.tag_name.match(desktopTag)[1]))
    const latest = candidates.find(release => {
      const version = release.tag_name.match(desktopTag)[1]
      return Array.isArray(release.assets) && [`BirdShot-${version}-Setup-x64.exe`, `BirdShot-${version}-Setup-x64.manifest.json`]
        .every(name => release.assets.some(asset => asset.name === name && downloadAsset(asset.browser_download_url, name)))
    })
    if (!latest) return { ...base, status: 'none', message: '当前没有已发布的Windows桌面更新（模型包不算App更新）' }
    const version = latest.tag_name.match(desktopTag)[1]
    if (compareVersions(version, currentVersion) <= 0) return { ...base, status: 'upToDate', message: '当前已是最新稳定桌面版' }
    const asset = latest.assets.find(asset => asset.name === `BirdShot-${version}-Setup-x64.exe`)
    const record = latest.assets.find(asset => asset.name === `BirdShot-${version}-Setup-x64.manifest.json`)
    const manifest = await boundedJson(record.browser_download_url, fetcher, true)
    if (manifest.version !== version || manifest.platform !== 'win32-x64' || manifest.channel !== 'stable' ||
        manifest.filename !== asset.name || !/^[a-f0-9]{64}$/.test(manifest.sha256 || '') ||
        !Number.isSafeInteger(manifest.bytes) || manifest.bytes !== asset.size ||
        !Number.isInteger(manifest.dataSchemaVersion) || manifest.dataSchemaVersion < 1 ||
        !Number.isInteger(manifest.minSupportedDataSchema) || manifest.minSupportedDataSchema < 1 ||
        manifest.minSupportedDataSchema > manifest.dataSchemaVersion) {
      throw new Error('官方桌面更新清单不完整或与安装器不一致')
    }
    if (asset.digest && asset.digest !== 'sha256:' + manifest.sha256) throw new Error('官方资产摘要与清单不一致')
    if (manifest.minSupportedDataSchema > 1) return { ...base, status: 'incompatible', newVersion: version,
      releaseUrl: latest.html_url, message: '新版本不直接支持当前库格式；请先阅读官方迁移说明' }
    return { ...base, status: 'available', newVersion: version, releaseUrl: latest.html_url,
      message: '打开官方页面下载并自行运行安装器；不会自动执行更新', notes: String(latest.body || '').slice(0, 12000),
      sha256: manifest.sha256, bytes: manifest.bytes, unsigned: manifest.codeSigned === false }
  } catch (error) {
    return { ...base, status: 'error', message: ['TimeoutError', 'AbortError'].includes(error.name) ?
      '检查更新连接超时；离线不影响本机筛片' : '检查更新失败：' + error.message }
  }
}
