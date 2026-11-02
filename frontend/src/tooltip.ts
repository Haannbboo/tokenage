/** Position a tooltip around its trigger, keeping its box inside the viewport. */
export function tooltipPosition(trigger: DOMRect, tooltip: Pick<DOMRect, 'width' | 'height'>, width: number, height: number) {
  const gap = 8
  const margin = 4
  const left = Math.max(margin, Math.min(trigger.left + trigger.width / 2 - tooltip.width / 2, width - tooltip.width - margin))
  const aboveRoom = Math.max(0, trigger.top - gap - margin)
  const belowRoom = Math.max(0, height - trigger.bottom - gap - margin)
  const below = tooltip.height > aboveRoom && belowRoom > aboveRoom
  const maxHeight = below ? belowRoom : aboveRoom
  const top = below ? trigger.bottom + gap : trigger.top - gap - Math.min(tooltip.height, maxHeight)
  const arrow = Math.max(8, Math.min(trigger.left + trigger.width / 2 - left, tooltip.width - 8))
  return { left, top, below, arrow, maxHeight }
}

/** Promote the existing tooltip to the browser's top layer while hovered. */
export function installTooltips() {
  // Without the Popover API, the plain CSS hover tooltip still works.
  if (!('showPopover' in HTMLElement.prototype)) return () => {}

  let trigger: HTMLElement | null = null
  let tooltip: HTMLElement | null = null
  let observer: ResizeObserver | null = null

  function position() {
    if (!trigger || !tooltip || !trigger.isConnected || !tooltip.matches(':popover-open')) return
    const naturalHeight = Math.max(tooltip.offsetHeight, tooltip.scrollHeight + tooltip.offsetHeight - tooltip.clientHeight)
    const { left, top, below, arrow, maxHeight } = tooltipPosition(trigger.getBoundingClientRect(), { width: tooltip.offsetWidth, height: naturalHeight }, innerWidth, innerHeight)
    tooltip.style.boxSizing = 'border-box'
    tooltip.style.maxHeight = `${maxHeight}px`
    tooltip.style.overflowY = naturalHeight > maxHeight ? 'auto' : 'visible'
    tooltip.style.setProperty('--tooltip-left', `${left}px`)
    tooltip.style.setProperty('--tooltip-top', `${top}px`)
    tooltip.style.setProperty('--tooltip-arrow', `${arrow}px`)
    tooltip.classList.toggle('tooltip-below', below)
  }

  function close() {
    observer?.disconnect()
    observer = null
    if (tooltip) {
      tooltip.style.removeProperty('max-height')
      tooltip.style.removeProperty('overflow-y')
      tooltip.style.removeProperty('box-sizing')
      if (tooltip.matches(':popover-open')) tooltip.hidePopover()
    }
    trigger = null
    tooltip = null
  }

  function show(target: HTMLElement | null) {
    if (!target || target === trigger) return
    const content = target.querySelector<HTMLElement>(':scope > .tooltip-text')
    if (!content) return
    close()
    trigger = target
    tooltip = content
    content.setAttribute('popover', 'manual')
    content.showPopover()
    position()
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(position)
      observer.observe(content)
      for (const child of content.children) observer.observe(child)
    }
  }

  function onMouseOver(event: MouseEvent) {
    show(event.target instanceof Element ? event.target.closest<HTMLElement>('.has-tooltip') : null)
  }

  function onFocusOrClick(event: Event) {
    const target = event.target instanceof Element ? event.target.closest<HTMLElement>('.session-tools-more') : null
    if (target) show(target)
    else if (event.type === 'click' && trigger?.classList.contains('session-tools-more')) close()
  }

  function onFocusOut(event: FocusEvent) {
    if (trigger?.classList.contains('session-tools-more') &&
        !(event.relatedTarget instanceof Node && trigger.contains(event.relatedTarget))) close()
  }

  function onWheel(event: WheelEvent) {
    if (!trigger || !tooltip || !(event.target instanceof Node) || !trigger.contains(event.target)) return
    if (tooltip.scrollHeight <= tooltip.clientHeight) return
    tooltip.scrollTop += event.deltaY
    event.preventDefault()
  }

  function onMouseOut(event: MouseEvent) {
    if (trigger && event.relatedTarget instanceof Node && trigger.contains(event.relatedTarget)) return
    if (trigger && event.target instanceof Node && trigger.contains(event.target)) close()
  }

  document.addEventListener('mouseover', onMouseOver)
  document.addEventListener('mouseout', onMouseOut)
  document.addEventListener('focusin', onFocusOrClick)
  document.addEventListener('focusout', onFocusOut)
  document.addEventListener('click', onFocusOrClick, true)
  document.addEventListener('wheel', onWheel, { passive: false })
  window.addEventListener('scroll', position, true)
  window.addEventListener('resize', position)
  return () => {
    close()
    document.removeEventListener('mouseover', onMouseOver)
    document.removeEventListener('mouseout', onMouseOut)
    document.removeEventListener('focusin', onFocusOrClick)
    document.removeEventListener('focusout', onFocusOut)
    document.removeEventListener('click', onFocusOrClick, true)
    document.removeEventListener('wheel', onWheel)
    window.removeEventListener('scroll', position, true)
    window.removeEventListener('resize', position)
  }
}
