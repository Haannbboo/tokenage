import { useEffect, type RefObject } from 'react'

/** Close an open popover on a pointer press outside `ref`, or on Escape. */
export function useDismiss(ref: RefObject<HTMLElement | null>, open: boolean, onClose: (event: Event) => void) {
  useEffect(() => {
    if (!open) return
    const onPointer = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(e) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(e) }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [ref, open, onClose])
}
