import { useEffect, useRef } from 'react'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'textarea:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

const getFocusable = (root) =>
  root ? Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR)) : []

export function useFocusTrap(active) {
  const containerRef = useRef(null)
  const previousFocusRef = useRef(null)

  useEffect(() => {
    if (!active) return undefined

    previousFocusRef.current = document.activeElement
    const root = containerRef.current
    if (root && !root.contains(document.activeElement)) {
      root.focus()
    }

    const handleKeyDown = (e) => {
      if (e.key !== 'Tab' || !containerRef.current) return
      const focusable = getFocusable(containerRef.current)
      if (focusable.length === 0) {
        e.preventDefault()
        containerRef.current.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const activeEl = document.activeElement

      if (e.shiftKey && (activeEl === first || !containerRef.current.contains(activeEl))) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && activeEl === last) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocusRef.current?.focus?.()
    }
  }, [active])

  return containerRef
}
