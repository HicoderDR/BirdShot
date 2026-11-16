// SPDX-License-Identifier: AGPL-3.0-only
export function trustedSender(event, contents, pageUrl) {
  return event.sender === contents && event.senderFrame === contents.mainFrame &&
    event.senderFrame?.url === pageUrl
}

export function externalReference(value) {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) return false
    if (['zh.wikipedia.org', 'en.wikipedia.org'].includes(url.hostname)) {
      return url.pathname.startsWith('/wiki/') || url.pathname === '/w/index.php' &&
        url.searchParams.has('search') && [...url.searchParams.keys()].every(key => key === 'search')
    }
    if (['www.inaturalist.org', 'inaturalist.org'].includes(url.hostname)) return /^\/photos\/\d+$/.test(url.pathname)
    if (url.hostname === 'commons.wikimedia.org') return url.pathname.startsWith('/wiki/')
    return url.hostname === 'creativecommons.org' && /^\/(?:licenses|publicdomain)\//.test(url.pathname)
  } catch {
    return false
  }
}

export function networkSettings(value) {
  if (!value || !['local', 'lan', 'all'].includes(value.mode) || !Number.isInteger(value.port) ||
      value.port < 1024 || value.port > 65535) throw new Error('端口须为1024–65535，访问方式无效')
  if (value.lanIp !== undefined && value.lanIp !== '' &&
      !/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value.lanIp)) throw new Error('请输入本机IPv4地址')
  if (value.lanIp && value.lanIp.split('.').some(part => Number(part) > 255)) throw new Error('IPv4地址无效')
  return { mode: value.mode, port: value.port, ...(value.lanIp ? { lanIp: value.lanIp } : {}) }
}
