/** Position a tooltip around its trigger, keeping its box inside the viewport. */
export function tooltipPosition(trigger: DOMRect, tooltip: DOMRect, width: number, height: number) {
  const gap = 8
  const margin = 4
  const left = Math.max(margin, Math.min(trigger.left + trigger.width / 2 - tooltip.width / 2, width - tooltip.width - margin))
  const below = trigger.top - gap - tooltip.height < margin
  const idealTop = below ? trigger.bottom + gap : trigger.top - gap - tooltip.height
  const top = Math.max(margin, Math.min(idealTop, height - tooltip.height - margin))
  const arrow = Math.max(8, Math.min(trigger.left + trigger.width / 2 - left, tooltip.width - 8))
  return { left, top, below, arrow }
}

/** Promote the existing tooltip to the browser's top layer while hovered. */
export function installTooltips() {
  // Without the Popover API, the plain CSS hover tooltip still works.
  if (!('showPopover' in HTMLElement.prototype)) return () => {}

  let trigger: HTMLElement | null = null
  let tooltip: HTMLElement | null = null

  function position() {
    if (!trigger || !tooltip || !trigger.isConnected || !tooltip.matches(':popover-open')) return
    const { left, top, below, arrow } = tooltipPosition(trigger.getBoundingClientRect(), tooltip.getBoundingClientRect(), innerWidth, innerHeight)
    tooltip.style.setProperty('--tooltip-left', `${left}px`)
    tooltip.style.setProperty('--tooltip-top', `${top}px`)
    tooltip.style.setProperty('--tooltip-arrow', `${arrow}px`)
    tooltip.classList.toggle('tooltip-below', below)
  }

  function close() {
    if (tooltip) {
      if (tooltip.matches(':popover-open')) tooltip.hidePopover()
      tooltip.removeAttribute('popover')
      tooltip.classList.remove('tooltip-below')
    }
    trigger = null
    tooltip = null
  }

  function onMouseOver(event: MouseEvent) {
    const target = event.target instanceof Element ? event.target.closest<HTMLElement>('.has-tooltip') : null
    if (!target || target === trigger) return
    const content = target.querySelector<HTMLElement>(':scope > .tooltip-text')
    if (!content) return
    close()
    trigger = target
    tooltip = content
    content.setAttribute('popover', 'manual')
    content.showPopover()
    position()
  }

  function onMouseOut(event: MouseEvent) {
    if (trigger && event.relatedTarget instanceof Node && trigger.contains(event.relatedTarget)) return
    if (trigger && event.target instanceof Node && trigger.contains(event.target)) close()
  }

  document.addEventListener('mouseover', onMouseOver)
  document.addEventListener('mouseout', onMouseOut)
  window.addEventListener('scroll', position, true)
  window.addEventListener('resize', position)
  return () => {
    close()
    document.removeEventListener('mouseover', onMouseOver)
    document.removeEventListener('mouseout', onMouseOut)
    window.removeEventListener('scroll', position, true)
    window.removeEventListener('resize', position)
  }
}
