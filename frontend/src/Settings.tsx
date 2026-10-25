import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { getToken, saveToken } from './api'
import { Notice } from './shared'

export default function Settings() {
  const [params, setParams] = useSearchParams()
  const [saved, setSaved] = useState(false)
  const client = useQueryClient()
  return <>
    <header className="page-heading"><div><p className="eyebrow">MAKE YOURSELF AT HOME</p><h1>设置<span className="accent">.</span></h1><p className="muted">连接你的私人信息台。</p></div></header>
    {params.get('auth') === 'required' && <Notice error>服务器要求有效的访问令牌，请在下方设置后重试。</Notice>}
    <section className="settings-panel"><h2>访问令牌</h2><p className="muted">与服务器配置的 server.token 一致。仅保存在当前浏览器，自动附加到 API 请求；未启用鉴权时可留空。</p>
      <form onSubmit={event => {
        event.preventDefault(); const data = new FormData(event.currentTarget)
        saveToken(String(data.get('token') || '').trim()); client.clear(); setSaved(true); setParams({})
      }}><label>Bearer token<input name="token" type="password" autoComplete="off" defaultValue={getToken()} placeholder="输入访问令牌" /></label><button className="primary" type="submit">保存令牌</button></form>
      {saved && <Notice>令牌已保存，可返回信息流继续浏览。</Notice>}
      <p className="small muted">请勿在不受信任的设备保存令牌。跨设备或对外访问请通过 HTTPS 反向代理。</p>
    </section>
  </>
}
