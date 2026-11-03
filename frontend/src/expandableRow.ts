import type { KeyboardEvent } from 'react'

/** Props for a table row that expands on click, Enter or Space. */
export function expandableRowProps(expanded: boolean, toggle: () => void) {
  return {
    className: `expandable-row${expanded ? ' expanded' : ''}`,
    tabIndex: 0,
    'aria-expanded': expanded,
    onClick: toggle,
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      // Keys on inner controls (copy, tool badges) are theirs.
      if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggle() }
    },
  }
}
