import { useEffect, useState } from 'react'
const key = 'finguard.preferences.v1'
export type Preferences = { largeText: boolean; compact: boolean; reduceMotion: boolean }
const defaults: Preferences = { largeText: false, compact: false, reduceMotion: false }
export function readPreferences(): Preferences {
    try {
        const data = JSON.parse(localStorage.getItem(key) ?? '{}')
        return {
            largeText: data?.largeText === true,
            compact: data?.compact === true,
            reduceMotion: data?.reduceMotion === true,
        }
    } catch {
        return defaults
    }
}
export function usePreferences() {
    const [value, setValue] = useState(readPreferences)
    useEffect(() => {
        const sync = () => setValue(readPreferences())
        window.addEventListener('finguard-preferences', sync)
        window.addEventListener('storage', sync)
        return () => {
            window.removeEventListener('finguard-preferences', sync)
            window.removeEventListener('storage', sync)
        }
    }, [])
    useEffect(() => {
        document.documentElement.dataset.largeText = String(value.largeText)
        document.documentElement.dataset.compact = String(value.compact)
        document.documentElement.dataset.reduceMotion = String(value.reduceMotion)
    }, [value])
    const save = (next: Preferences) => {
        localStorage.setItem(key, JSON.stringify(next))
        setValue(next)
        window.dispatchEvent(new Event('finguard-preferences'))
    }
    return { value, save, defaults }
}
