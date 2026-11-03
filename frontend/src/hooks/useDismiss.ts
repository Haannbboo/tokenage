import { useEffect, useRef, type RefObject } from 'react'

// Open instances, most recently opened last: Escape closes only the topmost.
const stack: object[] = []

/** Close an open popover on a pointer press outside `ref`, or on Escape. */
export function useDismiss(ref: RefObject<HTMLElement | null>, open: boolean, onClose: (event: Event) => void) {
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose })
  useEffect(() => {
    if (!open) return
    const token = {}
    stack.push(token)
    const onPointer = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onCloseRef.current(e) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && stack[stack.length - 1] === token) onCloseRef.current(e) }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      stack.splice(stack.indexOf(token), 1)
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [ref, open])
}
