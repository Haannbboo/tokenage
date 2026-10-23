import { createContext, createElement, useContext, useState, type ReactNode } from 'react'
import { zh } from './zh.ts'

export type Lang = 'en' | 'zh'

/** The locale the UI is written in; numbers and dates follow it, not the browser. */
export const LANG_LOCALES: Record<Lang, string> = { en: 'en-US', zh: 'zh-CN' }

const LANG_KEY = 'tokenage-lang'

let currentLang: Lang = (() => {
  const saved = localStorage.getItem(LANG_KEY)
  if (saved === 'zh' || saved === 'en') return saved
  return 'en'
})()

const LangContext = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({
  lang: currentLang,
  setLang: () => {},
})

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(currentLang)
  const setLang = (l: Lang) => {
    currentLang = l
    localStorage.setItem(LANG_KEY, l)
    setLangState(l)
  }
  return createElement(LangContext.Provider, { value: { lang, setLang } }, children)
}

export function useLang() {
  return useContext(LangContext)
}

export function getLang(): Lang {
  return currentLang
}

export function t(english: string): string {
  if (currentLang === 'en') return english
  return zh[english] ?? english
}
