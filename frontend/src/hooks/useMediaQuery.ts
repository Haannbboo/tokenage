import { useCallback, useSyncExternalStore } from 'react'

/** Same breakpoint as the phone layout in the CSS. */
export const PHONE_QUERY = '(max-width: 639px)'

export function useMediaQuery(query: string) {
  const subscribe = useCallback((notify: () => void) => {
    if (typeof window.matchMedia !== 'function') return () => {}
    const list = window.matchMedia(query)
    list.addEventListener('change', notify)
    return () => list.removeEventListener('change', notify)
  }, [query])
  return useSyncExternalStore(subscribe, () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches)
}
