import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApiError } from './api'
import Feed from './Feed'
import Sources from './Sources'
import Settings from './Settings'
import Pipelines from './Pipelines'
import Reports, { ReportDetail } from './Reports'
import Runs from './Runs'
import Personas from './Personas'
import { Icon } from './shared'
import '@fontsource-variable/noto-sans-sc'
import './style.css'

const queryClient = new QueryClient({ defaultOptions: { queries: {
  staleTime: 10000,
  retry: (count, error) => count < 1 && (!(error instanceof ApiError) || error.status >= 500),
} } })

function App() {
  const [dark, setDark] = useState(() => {
    const choice = localStorage.getItem('tentacles.theme')
    return choice ? choice === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches
  })
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('tentacles.theme', dark ? 'dark' : 'light') }, [dark])
  return <div className="app-shell">
    <a className="skip-link" href="#main">跳到内容</a>
    <aside className="sidebar"><NavLink to="/feed" className="brand"><svg className="brand-mark" aria-hidden="true" viewBox="0 0 40 40" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="20" cy="16" r="8" /><path d="M12 17C4 18 4 29 10 29M14 22C9 28 13 36 17 31M20 24v10M26 22c5 6 1 14-3 9M28 17c8 1 8 12 2 12" /><circle cx="17" cy="15" r="1" fill="currentColor" /><circle cx="23" cy="15" r="1" fill="currentColor" /></svg><span>tentacles<small>个人信息台</small></span></NavLink><p className="nav-label">工作空间</p>
      <nav aria-label="主导航"><NavLink to="/feed"><Icon name="feed" /> 信息流</NavLink><NavLink to="/sources"><Icon name="sources" /> 信息源</NavLink><NavLink to="/pipelines"><Icon name="feed" /> 处理流程</NavLink><NavLink to="/reports"><Icon name="feed" /> 报告</NavLink><NavLink to="/runs"><Icon name="sources" /> 运行记录</NavLink><NavLink to="/persona"><Icon name="settings" /> Persona</NavLink><NavLink to="/settings"><Icon name="settings" /> 设置</NavLink></nav>
      <div className="sidebar-bottom"><button onClick={() => setDark(!dark)} aria-label={dark ? '切换浅色模式' : '切换深色模式'}><Icon name={dark ? 'sun' : 'moon'} /> <span>{dark ? '浅色模式' : '深色模式'}</span></button><p>更少噪音，更多洞见。<br /><span>YOUR PERSONAL RADAR</span></p></div>
    </aside>
    <main id="main"><Routes><Route path="/feed" element={<Feed />} /><Route path="/sources" element={<Sources />} /><Route path="/pipelines" element={<Pipelines />} /><Route path="/reports" element={<Reports />} /><Route path="/reports/:id" element={<ReportDetail />} /><Route path="/runs" element={<Runs />} /><Route path="/persona" element={<Personas />} /><Route path="/settings" element={<Settings />} /><Route path="*" element={<Navigate to="/feed" replace />} /></Routes><footer className="page-footer">TENTACLES <span>为你的注意力，留一点余地。</span></footer></main>
  </div>
}

createRoot(document.getElementById('root')!).render(<StrictMode><QueryClientProvider client={queryClient}><BrowserRouter><App /></BrowserRouter></QueryClientProvider></StrictMode>)
