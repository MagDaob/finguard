import { useEffect, useState } from 'react'

declare global {
    interface Window {
        pywebview?: { api?: { session: () => Promise<{ token: string }> } }
    }
}

export function useDesktop() {
    const [available, setAvailable] = useState(!!window.pywebview?.api?.session)
    useEffect(() => {
        const ready = () => setAvailable(!!window.pywebview?.api?.session)
        window.addEventListener('pywebviewready', ready)
        ready()
        return () => window.removeEventListener('pywebviewready', ready)
    }, [])
    return available
}

export async function desktopSession() {
    const bridge = window.pywebview?.api
    if (!bridge) throw new Error('本机桌面会话尚未就绪。')
    const result = await bridge.session()
    if (typeof result?.token !== 'string' || !result.token) throw new Error('无法建立本机会话。')
    return result.token
}
