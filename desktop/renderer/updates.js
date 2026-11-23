// SPDX-License-Identifier: AGPL-3.0-only
const section = document.getElementById('update-settings')
const button = document.getElementById('check-updates')
const status = document.getElementById('update-status')
const link = document.getElementById('update-page')
button.addEventListener('click', async () => {
  if (!window.birdshotDesktop?.checkForUpdates) { status.textContent = '当前运行环境不提供桌面更新检查'; return }
  button.disabled = true
  status.textContent = '正在查询官方桌面发行…'
  link.hidden = true
  try {
    const result = await window.birdshotDesktop.checkForUpdates()
    status.textContent = `当前 ${result.currentVersion} · ` + result.message +
      (result.newVersion ? `（${result.newVersion}）` : '') +
      (result.unsigned ? '；该安装器未签名，SHA仅证明文件完整性。' : '')
    status.setAttribute('role', result.status === 'error' ? 'alert' : 'status')
    if (result.releaseUrl) {
      link.href = result.releaseUrl
      link.textContent = '打开官方发行页'
      link.hidden = false
    }
  } catch (error) { status.textContent = '更新检查失败：' + error.message; status.setAttribute('role', 'alert') }
  finally { button.disabled = false }
})
if (!section) throw new Error('Desktop update controls are missing')
