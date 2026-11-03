import { useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'

export function CopyButton({
  text,
  className = 'btn-ghost',
  style,
  idleLabel,
  copiedLabel,
  onCopied,
  timeoutMs = 1500,
}: {
  text: string
  className?: string
  style?: CSSProperties
  idleLabel: ReactNode
  copiedLabel: ReactNode
  onCopied?: () => void
  timeoutMs?: number
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    void navigator.clipboard.writeText(text)
      .then(() => {
        setCopied(true)
        onCopied?.()
        setTimeout(() => setCopied(false), timeoutMs)
      })
  }

  return (
    <button
      className={`${className}${copied ? ' btn-copy-clicked' : ''}`}
      onClick={handleCopy}
      style={style}
    >
      {copied ? copiedLabel : idleLabel}
    </button>
  )
}

export function copyTextToClipboard(text: string, onCopy?: (msg: string) => void) {
  void navigator.clipboard.writeText(text).then(() => {
    onCopy?.('✓ Copied to clipboard')
  })
}

export function ClickToCopy({ text, children, onCopy }: { text: string, children: React.ReactNode, onCopy?: (msg: string) => void }) {
  const handleCopy = (e: React.SyntheticEvent) => {
    // Inside an expandable row: copying must not toggle it.
    e.stopPropagation();
    copyTextToClipboard(text, onCopy);
  };

  // A span with button semantics keeps the inline text look.
  return (
    <span
      className="click-to-copy"
      role="button"
      tabIndex={0}
      onClick={handleCopy}
      onKeyDown={(e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        handleCopy(e);
      }}
      title="Click to copy"
    >
      {children}
    </span>
  );
}
