import { useEffect, useRef, type ReactNode } from 'react'

export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`notice ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>
}

export function Drawer({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { dialog?.close(); document.body.style.overflow = previous }
  }, [])
  return <dialog ref={ref} className="drawer" aria-labelledby="drawer-title" onCancel={close}>
    <header className="drawer-top"><span className="eyebrow">条目详情</span><button autoFocus onClick={close} aria-label="关闭详情">关闭 ×</button></header>
    <h2 id="drawer-title">{title}</h2>{children}
  </dialog>
}


const iconPaths = {
  feed: <><path d="M4 4h16v16H4zM8 8h8M8 12h8M8 16h5" /></>,
  sources: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="4" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="m9 3-1 3-3 1v3l-2 2 2 2v3l3 1 1 3h6l1-3 3-1v-3l2-2-2-2V7l-3-1-1-3z" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2" /></>,
  moon: <path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z" />,
}

export function Icon({ name }: { name: keyof typeof iconPaths }) {
  return <svg className="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{iconPaths[name]}</svg>
}


export function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 0.8 ? 'high' : score >= 0.5 ? 'mid' : 'low'
  return <span className={`badge score-${tone}`} aria-label={`相关度 ${Math.round(score * 100)} 分`}>{Math.round(score * 100)} 分</span>
}
