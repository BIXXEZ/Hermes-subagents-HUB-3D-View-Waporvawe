/**
 * office_hermes — живой vaporwave-офис Hermes в правой зоне рабочего стола.
 *
 * Панель показывает ОДИН текущий (сфокусированный) чат: имя чата на доске,
 * реальных субагентов за столами и их логи. Данные отдаёт локальный сервер
 * (office_live.py, http://127.0.0.1:8797), который читает state.db Hermes
 * только на чтение.
 *
 * Три входа: панель у правого края рабочей области, чип в статусбаре и
 * команда ⌘K «office_hermes» — оба зовут host.revealPane.
 */

import { host, PALETTE_AREA, useQuery, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useRef, useState } from 'react'

const ID = 'office-hermes'
const PANE_ID = ID + ':pane'
const BASE = 'http://127.0.0.1:8797'

function reveal() {
  // undismiss: панель могла быть закрыта ✕ и запомнена как Close — тогда
  // revealPane на пустом месте ничего не делает.
  if (typeof host.undismissPane === 'function') host.undismissPane(PANE_ID)
  if (typeof host.revealPane === 'function') host.revealPane(PANE_ID)
}

function useOfficeState() {
  return useQuery({
    queryKey: ['office-hermes', 'state'],
    refetchInterval: 5000,
    retry: false,
    queryFn: async () => {
      const ctl = new AbortController()
      const t = setTimeout(() => ctl.abort(), 1500)
      try {
        const r = await fetch(BASE + '/state', { signal: ctl.signal, cache: 'no-store' })
        if (!r.ok) throw new Error('bad status ' + r.status)
        return await r.json()
      } finally {
        clearTimeout(t)
      }
    }
  })
}

function usePaneVisible() {
  // paneVisibility есть не во всех сборках десктопа
  const [, force] = useState(0)
  useEffect(() => {
    if (typeof host.paneVisibility !== 'function') return undefined
    const atom = host.paneVisibility(PANE_ID)
    const unsub = typeof atom.subscribe === 'function' ? atom.subscribe(() => force(n => n + 1)) : undefined
    return () => { if (unsub) unsub() }
  }, [])
  if (typeof host.paneVisibility !== 'function') return true
  try { return !!host.paneVisibility(PANE_ID).get() } catch { return true }
}

function OfficePane() {
  const frame = useRef(null)
  const boot = useRef(null)
  const visible = usePaneVisible()
  const tries = useRef(0)
  const storedSid = useValue(host.state.focusedStoredSessionId)
  const busy = useValue(host.state.busy)
  const [loaded, setLoaded] = useState(false)
  const { data, isError } = useOfficeState()

  const post = useCallback((msg) => {
    const el = frame.current
    if (el && el.contentWindow) el.contentWindow.postMessage(msg, '*')
  }, [])

  const push = useCallback(() => {
    post({ type: 'sid', value: storedSid || '' })
    post({ type: 'busy', value: !!busy })
  }, [post, storedSid, busy])

  useEffect(() => {
    if (loaded) push()
  }, [loaded, push])

  // Панель приезжает свёрнутой вертикальной полоской у правого края. Офис —
  // фоновый экран, он должен быть открыт: раскрываем себя, пока не станем
  // видимыми (не больше 5 попыток, чтобы не спорить с ручным закрытием).
  useEffect(() => {
    if (visible || tries.current >= 5) return undefined
    tries.current += 1
    const t = setTimeout(reveal, 900 * tries.current)
    return () => clearTimeout(t)
  }, [visible])

  if (isError || (data && data.error)) {
    return jsxs('div', {
      className: 'flex h-full flex-col gap-2 p-4 text-sm text-(--ui-text-secondary)',
      children: [
        jsx('div', { className: 'font-medium text-foreground', children: 'office_hermes — нет связи с фидом' }),
        jsx('div', {
          className: 'text-(--ui-text-tertiary)',
          children: 'Запустите сервер офиса (читает state.db только на чтение):'
        }),
        jsx('code', {
          className: 'rounded bg-(--ui-surface-2) px-2 py-1 text-[0.75rem] text-(--ui-text-secondary)',
          children: 'python "%LOCALAPPDATA%\\hermes\\office-live\\office_live.py"'
        }),
        jsx('div', { className: 'text-(--ui-text-quaternary)', children: 'Панель подхватит связь сама.' })
      ]
    })
  }

  // версия на каждый mount: правки страницы подхватываются без перезагрузки приложения
  if (boot.current === null) {
    boot.current = BASE + '/?live=1&v=' + Date.now() + (storedSid ? '&sid=' + encodeURIComponent(storedSid) : '')
  }
  const src = boot.current

  // Пока сцена грузится (большой GLB), панель показывала бы пустоту.
  const overlay = loaded
    ? null
    : jsx('div', {
        className: 'pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-[0.75rem] text-(--ui-text-tertiary)',
        children: [
          jsx('div', { children: 'office_hermes — загружаю офис…' }),
          jsx('div', { className: 'text-(--ui-text-quaternary)', children: 'сцена ~7 МБ, обычно 15–25 с' })
        ]
      })

  return jsxs('div', {
    className: 'relative h-full w-full',
    children: [jsx('iframe', {
    ref: frame,
    src,
      title: 'Hermes office',
      onLoad: () => {
        setLoaded(true)
        push()
      },
        style: { width: '100%', height: '100%', border: '0', display: 'block', background: 'var(--ui-surface-1)' }
    }), overlay]
  })
}

function OfficeChip() {
  const { data, isError } = useOfficeState()
  const workers = (data && Array.isArray(data.workers)) ? data.workers.length : 0
  const busy = !!(data && data.chat && data.chat.busy)
  const dot = isError ? '○' : busy ? '◉' : '●'
  const title = isError
    ? 'office_hermes: фид не запущен'
    : `office_hermes: ${(data.chat && data.chat.title) || 'без названия'} · субагентов: ${workers} — открыть панель`

  return jsx('button', {
    type: 'button',
    title,
    className: 'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
    onClick: reveal,
    children: `office_hermes ${dot}${workers || ''}`
  })
}

export default {
  id: ID,
  name: 'office_hermes',
  register(ctx) {
    // Панель приходит свёрнутой вертикальной полоской у правого края (так же,
    // как Routines). Раскрываем её один раз при загрузке плагина — офис нужен
    // открытым, это фоновый экран. Дальше состояние помнит сам пользователь.
    setTimeout(reveal, 900)
    setTimeout(reveal, 3500)

    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'office_hermes',
      // placement 'main' + dock right = своя панель у правого края рабочей
      // области. placement 'right' — это скрываемый правый сайдбар: там
      // панель не видно, пока не включишь сайдбар.
      data: {
        placement: 'main',
        dock: { pane: 'workspace', pos: 'right', enforce: true },
        width: '560px',
        tabTitleText: () => 'office_hermes'
      },
      render: () => jsx(OfficePane, {})
    })

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 120,
      render: () => jsx(OfficeChip, {})
    })

    ctx.register({
      id: 'open-command',
      area: PALETTE_AREA,
      data: {
        id: 'office.open',
        label: 'office_hermes: открыть офис',
        keywords: ['office', 'hermes', 'офис', 'агенты', 'vaporwave'],
        run: reveal
      }
    })
  }
}
