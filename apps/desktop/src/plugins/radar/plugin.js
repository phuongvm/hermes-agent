/**
 * Fleet & Session Radar - Hermes Desktop plugin.
 *
 * One read-only board of who is talking to this Hermes right now: A2A peers, Buzz, ACP clients and
 * remote users. Data comes from one backend route (board.json under /api/plugins/radar/); the only write
 * is the Security Shield decision (security/action), which records a block / dismiss in a policy file.
 *
 * Contributions (registerMany):
 *   page  - ROUTES_AREA        /radar centre board: filter bar, four paged swimlanes, Security Shield, mute switch
 *   nav   - SIDEBAR_NAV_AREA   Radar entry, asTile so opening it keeps the active chat tab
 *   feed  - PANES_AREA         closable right pane "Radar feed" (registered only while radar.feedOpen is true)
 *   chip  - STATUSBAR_AREAS.right  count chip with a conic-gradient scan while anything runs
 * Plus a proactive toast watcher (host.notify) for inbound A2A tasks and auth rejection bursts,
 * silenced by a persistent mute switch kept in ctx.storage.
 *
 * Preferences in ctx.storage: radar.muteAlerts, radar.pageSize (5|10|20|50, default 5), radar.feedOpen.
 *
 * Packaging: single uncompiled ESM file; imports only the plugin SDK, react and the JSX runtime.
 * Colours come from the app's --ui-* theme tokens only.
 */

import {
  Codicon, host, PANES_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS, useQuery
} from '@hermes/plugin-sdk'
import React from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const POLL_MS = 5000
const WATCH_MS = 5000
const MUTE_KEY = 'radar.muteAlerts'
const PAGE_SIZE_KEY = 'radar.pageSize'
const FEED_KEY = 'radar.feedOpen'
const FEED_PANE_ID = 'radar:feed' // the contribution-scoped pane id: <plugin id>:<contribution id>
const PAGE_SIZES = Object.freeze([5, 10, 20, 50])
const DEFAULT_PAGE_SIZE = 5
const ALERT_BURST = 5 // rejections inside the backend's 5 minute window that raise a toast
const BOARD_PATH = '/board.json'
const SECURITY_PATH = '/security/action'
const RADAR_ROUTE = '/radar'
const LANE_ORDER = ['a2a', 'buzz', 'acp', 'remote']
const LANE_TITLES = { a2a: 'A2A Peers', buzz: 'Buzz', acp: 'ACP', remote: 'Remote Users' }
const NO_FILTERS = Object.freeze({ q: '', entity: 'all', status: 'all', dateWindow: 'all' })
const STATUS_OPTIONS = [['all', 'All status'], ['running', 'Running now'], ['active', 'Active (running + warm)'], ['idle', 'Idle']]
const DATE_OPTIONS = [['all', 'All time'], ['1h', 'Last hour'], ['24h', 'Last 24 hours'], ['7d', 'Last 7 days'], ['30d', 'Last 30 days']]
const DATE_WINDOW_S = { '1h': 3600, '24h': 86400, '7d': 604800, '30d': 2592000 }
const KIND_LABELS = { known_member_expired_cookie: 'Known member (expired cookie)', unauthenticated_probe: 'Unauthenticated probe' }

const RADAR_CSS = [
  '@property --radar-arc { syntax: "<angle>"; inherits: false; initial-value: 0deg; }',
  '@keyframes radararc { to { --radar-arc: 360deg; } }',
  '@keyframes radarglow { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }',
  '.radar-chip { position: relative; display: flex; align-items: center; align-self: center; gap: 5px; height: 16px; margin: 0 2px; padding: 0 6px; box-sizing: border-box; line-height: 1; border: 0; background: none; cursor: pointer; font-size: 11px; border-radius: 4px; color: var(--ui-text-tertiary); }',
  '.radar-chip:hover { color: var(--ui-text-primary); }',
  '.radar-chip b { font-weight: 600; color: var(--ui-accent); }',
  '.radar-chip.live { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ui-success) 28%, transparent); }',
  '.radar-chip.live .codicon { color: var(--ui-success); animation: radarglow 2.2s ease-in-out infinite; }',
  '.radar-chip.live::after { content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1px; pointer-events: none;' +
    ' background: conic-gradient(from var(--radar-arc), transparent 0 68%, var(--ui-success) 88%, transparent 100%);' +
    ' -webkit-mask: linear-gradient(rgb(0 0 0 / 0.78) 0 0) content-box, linear-gradient(black 0 0); -webkit-mask-composite: xor;' +
    ' mask: linear-gradient(rgb(0 0 0 / 0.78) 0 0) content-box exclude, linear-gradient(black 0 0); animation: radararc 2.2s linear infinite; }',
  '.radar-card.live::after, .radar-card.warm::after { content: ""; position: absolute; inset: -1px; border-radius: inherit; padding: 1px; pointer-events: none;' +
    ' -webkit-mask: linear-gradient(black 0 0) content-box, linear-gradient(black 0 0); -webkit-mask-composite: xor; mask: linear-gradient(black 0 0) content-box exclude, linear-gradient(black 0 0); }',
  '.radar-card.live::after { background: conic-gradient(from var(--radar-arc), transparent 0 68%, var(--ui-success) 88%, transparent 100%); animation: radararc 2.2s linear infinite; }',
  '.radar-card.warm::after { background: conic-gradient(from var(--radar-arc), transparent 0 68%, var(--ui-warning, var(--ui-accent)) 88%, transparent 100%); animation: radararc 6s linear infinite; }',
  '.radar-page { display: flex; flex-direction: column; gap: 12px; height: 100%; padding: 16px; overflow: auto; color: var(--ui-text-primary); }',
  '.radar-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }',
  '.radar-head h1 { margin: 0; font-size: 15px; font-weight: 600; }',
  '.radar-sub { font-size: 11px; color: var(--ui-text-tertiary); }',
  '.radar-grow { flex: 1; }',
  '.radar-btn { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--ui-stroke-secondary); background: var(--ui-bg-elevated); color: var(--ui-text-secondary); border-radius: 6px; padding: 3px 8px; font-size: 11px; cursor: pointer; }',
  '.radar-btn:hover { color: var(--ui-text-primary); }',
  '.radar-btn:disabled { opacity: 0.45; cursor: default; }',
  '.radar-btn[aria-pressed="true"] { color: var(--ui-accent); border-color: var(--ui-accent); }',
  '.radar-btn.danger { color: var(--ui-danger); border-color: var(--ui-danger); }',
  '.radar-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }',
  '.radar-input, .radar-select { border: 1px solid var(--ui-stroke-secondary); background: var(--ui-bg-elevated); color: var(--ui-text-primary); border-radius: 6px; padding: 3px 8px; font-size: 11px; min-width: 0; }',
  '.radar-input { flex: 1 1 220px; max-width: 360px; }',
  '.radar-input:focus, .radar-select:focus { outline: none; border-color: var(--ui-accent); }',
  '.radar-lanes { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; align-items: start; }',
  '.radar-lane { display: flex; flex-direction: column; gap: 8px; min-width: 0; }',
  '.radar-lane-head { display: flex; align-items: center; justify-content: space-between; font-size: 12px; font-weight: 600; color: var(--ui-text-secondary); }',
  '.radar-count { font-size: 11px; font-weight: 400; color: var(--ui-text-tertiary); }',
  '.radar-pager { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 11px; color: var(--ui-text-tertiary); }',
  '.radar-card { position: relative; display: flex; flex-direction: column; gap: 4px; min-width: 0; padding: 8px 10px; border: 1px solid var(--ui-stroke-secondary); border-radius: 8px; background: var(--ui-bg-elevated); cursor: pointer; text-align: left; }',
  '.radar-card.idle { opacity: 0.7; }',
  '.radar-card:hover { border-color: var(--ui-accent); }',
  '.radar-card-title { font-size: 12px; color: var(--ui-text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
  '.radar-card-preview { font-size: 11px; color: var(--ui-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
  '.radar-card-meta { display: flex; flex-wrap: wrap; gap: 6px; font-size: 10.5px; color: var(--ui-text-tertiary); }',
  '.radar-badge { border: 1px solid var(--ui-stroke-secondary); border-radius: 999px; padding: 0 6px; color: var(--ui-text-secondary); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
  '.radar-tool { border: 1px solid var(--ui-success); border-radius: 4px; padding: 0 5px; color: var(--ui-success); }',
  '.radar-empty { font-size: 11px; color: var(--ui-text-tertiary); padding: 8px 2px; }',
  '.radar-alert { border: 1px solid var(--ui-danger); border-radius: 8px; padding: 6px 10px; font-size: 11.5px; color: var(--ui-danger); }',
  '.radar-shield { display: flex; flex-direction: column; gap: 8px; }',
  '.radar-shield-head { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 600; color: var(--ui-text-secondary); }',
  '.radar-shield-card { display: flex; flex-direction: column; gap: 6px; border: 1px solid var(--ui-warning, var(--ui-accent)); border-radius: 8px; padding: 8px 10px; background: var(--ui-bg-elevated); }',
  '.radar-shield-card.high { border-color: var(--ui-danger); }',
  '.radar-shield-card.blocked { border-color: var(--ui-stroke-secondary); opacity: 0.8; }',
  '.radar-shield-line { font-size: 11.5px; color: var(--ui-text-primary); overflow-wrap: anywhere; }',
  '.radar-shield-meta { display: flex; flex-wrap: wrap; gap: 6px; font-size: 10.5px; color: var(--ui-text-tertiary); }',
  '.radar-shield-actions { display: flex; flex-wrap: wrap; gap: 6px; }',
  '.radar-shield details { font-size: 11px; color: var(--ui-text-secondary); }',
  '.radar-shield summary { cursor: pointer; color: var(--ui-text-tertiary); }',
  '.radar-shield-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 3px 0; }',
  '.radar-feed { display: flex; flex-direction: column; gap: 10px; padding: 10px; height: 100%; overflow: auto; color: var(--ui-text-primary); }',
  '.radar-feed-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 12px; font-weight: 600; color: var(--ui-text-secondary); }',
  '.radar-feed h2 { margin: 0 0 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--ui-text-tertiary); }',
  '.radar-feed-row { font-size: 11.5px; padding: 3px 0; border-bottom: 1px solid var(--ui-stroke-secondary); overflow-wrap: anywhere; }',
  '@media (max-width: 1100px) { .radar-lanes { grid-template-columns: repeat(2, minmax(0, 1fr)); } }',
  '@media (prefers-reduced-motion: reduce) { .radar-chip.live .codicon { animation: none; } .radar-chip.live::after, .radar-card.live::after, .radar-card.warm::after { animation: none; background: var(--ui-success); opacity: 0.55; } .radar-card.warm::after { background: var(--ui-warning, var(--ui-accent)); } }'
].join(' ')

// -------------------------------------------------------------------------------------------------
// Pure helpers (exported through __test)
// -------------------------------------------------------------------------------------------------

function formatIdle(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0))
  if (s < 60) return s + 's'
  if (s < 3600) return Math.floor(s / 60) + 'm'
  if (s < 86400) return Math.floor(s / 3600) + 'h'
  return Math.floor(s / 86400) + 'd'
}

/** Header numbers for the chip and the page. `live` drives the radar scan. */
function summarizeBoard(board) {
  const counts = (board && board.counts) || {}
  const running = Number(counts.running) || 0
  const warm = Number(counts.warm) || 0
  return {
    ok: !!(board && board.ok),
    total: Number(counts.total) || 0,
    running,
    warm,
    active: running + warm,
    live: running > 0
  }
}

/** The lanes in canonical order, each guaranteed to exist (an old backend answer still renders four columns). */
function orderedLanes(board) {
  const byKey = {}
  for (const lane of (board && board.lanes) || []) if (lane && lane.key) byKey[lane.key] = lane
  return LANE_ORDER.map(key => {
    const lane = byKey[key] || {}
    return { key, title: lane.title || LANE_TITLES[key], running: Number(lane.running) || 0, cards: lane.cards || [] }
  })
}

/** Route a session card opens in Desktop (the session route is "/<session id>"); null for ledger-only peers. */
function sessionPath(card) {
  if (!card || !card.id || card.synthetic) return null
  return '/' + encodeURIComponent(card.id)
}

function cardIdleSeconds(card, nowSec) {
  if (typeof card.idle_s === 'number' && isFinite(card.idle_s)) return card.idle_s
  const last = Number(card.last_activity_at)
  return isFinite(last) && last > 0 ? Math.max(0, nowSec - last) : Infinity
}

function isFiltering(filters) {
  const f = filters || NO_FILTERS
  return !!(String(f.q || '').trim() || (f.entity && f.entity !== 'all') || (f.status && f.status !== 'all') || (f.dateWindow && f.dateWindow !== 'all'))
}

/** Cards that satisfy every active predicate: q (text), entity, status and date window. Order is preserved. */
function filterCards(cards, filters, nowSec) {
  const f = filters || NO_FILTERS
  const now = typeof nowSec === 'number' ? nowSec : Date.now() / 1000
  const q = String(f.q || '').trim().toLowerCase()
  const windowS = DATE_WINDOW_S[f.dateWindow]
  return (cards || []).filter(card => {
    if (!card) return false
    if (q) {
      const haystack = [card.title, card.last_message_preview, card.actor, card.actor_badge, card.entity, card.chat_id, card.cwd, card.model, card.id]
        .filter(v => v !== null && v !== undefined).join('\n').toLowerCase()
      if (!haystack.includes(q)) return false
    }
    if (f.entity && f.entity !== 'all' && card.entity !== f.entity && card.actor !== f.entity) return false
    if (f.status === 'running' && card.status !== 'running') return false
    if (f.status === 'active' && card.status !== 'running' && card.status !== 'warm') return false
    if (f.status === 'idle' && card.status !== 'idle') return false
    if (windowS && !(cardIdleSeconds(card, now) <= windowS)) return false
    return true
  })
}

/** Sorted distinct entity names (the card's entity, else its actor) for the Entity dropdown. */
function collectEntities(cards) {
  const names = new Set()
  for (const card of cards || []) {
    const name = card && (card.entity || card.actor)
    if (name) names.add(String(name))
  }
  return Array.from(names).sort()
}

function normalizePageSize(value) {
  const n = Number(value)
  return PAGE_SIZES.includes(n) ? n : DEFAULT_PAGE_SIZE
}

/** One page of *items*; the page number is clamped into range and an unsupported page size becomes 5. */
function paginateItems(items, page, pageSize) {
  const list = items || []
  const size = normalizePageSize(pageSize)
  const totalPages = Math.max(1, Math.ceil(list.length / size))
  const wanted = Math.floor(Number(page))
  const current = Math.min(totalPages, isFinite(wanted) && wanted >= 1 ? wanted : 1)
  return { items: list.slice((current - 1) * size, current * size), page: current, totalPages, totalItems: list.length }
}

/** Everything the /radar board renders: four lanes, each filtered then paged, plus the entity list. */
function buildBoardView(board, filters, pages, pageSize, nowSec) {
  const base = orderedLanes(board)
  const lanes = base.map(lane => {
    const filtered = filterCards(lane.cards, filters, nowSec)
    const paged = paginateItems(filtered, (pages || {})[lane.key], pageSize)
    return {
      key: lane.key,
      title: lane.title,
      running: lane.running,
      total: lane.cards.length,
      filtered: filtered.length,
      page: paged.page,
      totalPages: paged.totalPages,
      totalItems: paged.totalItems,
      cards: paged.items
    }
  })
  return { lanes, entities: collectEntities(base.flatMap(lane => lane.cards)), filtering: isFiltering(filters) }
}

/** The JSON body of one security/action call. Block carries a human reason; dismissals address the alert id. */
function securityBody(action, target) {
  const t = target || {}
  if (action === 'block') {
    return { action, ip: t.ip, reason: (Number(t.count) || 0) + ' rejected requests: ' + (t.reason || '?') + ' ' + (t.path || '') }
  }
  if (action === 'unblock') return { action, ip: t.ip }
  return { action, alert_id: t.id }
}

/** Post one decision, then refresh the board. A rejected call throws and leaves the board alone. */
async function performSecurityAction(api, action, target, refresh) {
  const result = await api.securityAction(securityBody(action, target))
  if (typeof refresh === 'function') await refresh()
  return result
}

/** Put *text* on the clipboard through the host's OS door (or the browser one); false when neither exists. */
async function copyText(ctx, text) {
  try {
    if (ctx && ctx.os && typeof ctx.os.writeClipboard === 'function') return (await ctx.os.writeClipboard(text)) === true
    if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (e) { /* clipboard blocked: the caller reports it */ }
  return false
}

/**
 * Which events deserve a toast. `seen` is the watcher's memory: { baselined, events:Set, alerts:Set }.
 * The first call only records a baseline, so opening Desktop never replays history as toasts.
 * A dismissed or blocked burst is already handled by the operator and never toasts.
 */
function diffAlerts(board, seen) {
  const toasts = []
  const events = (board && board.a2a_events) || []
  const alerts = (board && board.auth_alerts) || []
  const baseline = !seen.baselined
  for (const ev of events) {
    if (!ev || !ev.id || seen.events.has(ev.id)) continue
    seen.events.add(ev.id)
    if (!baseline && ev.direction === 'inbound') {
      toasts.push({
        id: 'radar-a2a-' + ev.id,
        kind: 'info',
        title: 'A2A: task from ' + (ev.peer || 'a peer'),
        message: ev.summary || ev.task_id || 'New inbound task'
      })
    }
  }
  const hot = new Set()
  for (const alert of alerts) {
    if (!alert || !alert.id) continue
    const burst = !alert.dismissed && !alert.blocked && (Number(alert.recent_count) || 0) >= ALERT_BURST
    if (burst) hot.add(alert.id)
    if (burst && !seen.alerts.has(alert.id)) {
      seen.alerts.add(alert.id)
      if (!baseline) {
        toasts.push({
          id: 'radar-auth-' + alert.id,
          kind: 'warning',
          title: 'Auth: repeated rejections',
          message: alert.recent_count + ' rejected requests from ' + alert.ip + ' (' + alert.reason + ') on ' + alert.path
        })
      }
    }
  }
  for (const id of Array.from(seen.alerts)) if (!hot.has(id)) seen.alerts.delete(id) // a quiet IP can alert again later
  seen.baselined = true
  return toasts
}

// -------------------------------------------------------------------------------------------------
// Plugin plumbing
// -------------------------------------------------------------------------------------------------

function createApi(ctx) {
  return {
    board: () => ctx.rest(BOARD_PATH),
    securityAction: body => ctx.rest(SECURITY_PATH, { method: 'POST', body })
  }
}

function navigateTo(ctx, path) {
  if (!path) return null
  if (ctx && ctx.host && typeof ctx.host.navigate === 'function') return ctx.host.navigate(path)
  if (host && typeof host.navigate === 'function') return host.navigate(path)
  return null
}

/** Persistent mute switch. Reads ctx.storage on every get, so a toggle in one window silences the watcher in all. */
function createMuteStore(ctx) {
  const listeners = new Set()
  let memory = false
  const read = () => {
    try {
      if (ctx && ctx.storage && typeof ctx.storage.get === 'function') return !!ctx.storage.get(MUTE_KEY, false)
    } catch (e) { /* storage blocked: fall back to this session's value */ }
    return memory
  }
  return {
    get: read,
    set(value) {
      memory = !!value
      try {
        if (ctx && ctx.storage && typeof ctx.storage.set === 'function') ctx.storage.set(MUTE_KEY, memory)
      } catch (e) { /* storage full or blocked: the switch still applies this session */ }
      listeners.forEach(fn => fn(memory))
    },
    toggle() { this.set(!read()) },
    subscribe(fn) {
      listeners.add(fn)
      return () => { listeners.delete(fn) }
    }
  }
}

/** A persisted preference with change notification; *clean* maps any stored junk onto a legal value. */
function createPrefStore(ctx, key, fallback, clean) {
  const listeners = new Set()
  let memory = fallback
  const read = () => {
    try {
      if (ctx && ctx.storage && typeof ctx.storage.get === 'function') return clean(ctx.storage.get(key, fallback))
    } catch (e) { /* storage blocked: fall back to this session's value */ }
    return memory
  }
  return {
    get: read,
    set(value) {
      memory = clean(value)
      try {
        if (ctx && ctx.storage && typeof ctx.storage.set === 'function') ctx.storage.set(key, memory)
      } catch (e) { /* storage full or blocked: the preference still applies this session */ }
      listeners.forEach(fn => fn(memory))
    },
    subscribe(fn) {
      listeners.add(fn)
      return () => { listeners.delete(fn) }
    }
  }
}

function createPageSizeStore(ctx) {
  return createPrefStore(ctx, PAGE_SIZE_KEY, DEFAULT_PAGE_SIZE, normalizePageSize)
}

const feedStores = new WeakMap()

/** Whether the Radar feed pane is wanted (default open). One shared store per plugin context. */
function createFeedStore(ctx) {
  if (ctx && typeof ctx === 'object' && feedStores.has(ctx)) return feedStores.get(ctx)
  const store = createPrefStore(ctx, FEED_KEY, true, value => value !== false)
  if (ctx && typeof ctx === 'object') feedStores.set(ctx, store)
  return store
}

/** Open the feed (registering its pane when closed), then un-dismiss and reveal it for the user. */
function showFeed(ctx, feedStore) {
  feedStore.set(true)
  const target = (ctx && ctx.host) || host
  if (target && typeof target.undismissPane === 'function') target.undismissPane(FEED_PANE_ID)
  if (target && typeof target.revealPane === 'function') target.revealPane(FEED_PANE_ID)
}

function isFeedOnScreen(ctx) {
  try {
    const target = (ctx && ctx.host) || host
    if (target && typeof target.paneVisibility === 'function') {
      const atom = target.paneVisibility(FEED_PANE_ID)
      if (atom && typeof atom.get === 'function') return !!atom.get()
    }
  } catch (e) { /* older desktop: judge by the stored preference alone */ }
  return true
}

/**
 * Follow the host's on-screen visibility of the feed pane so closing it from the native tab strip (its own X) is
 * recorded too: once the pane has been seen on screen, a drop to hidden calls *onHidden*; seeing it on screen again
 * calls *onShown*. Older desktops without paneVisibility get a no-op. Returns the disposer.
 */
function watchFeedVisibility(ctx, onHidden, onShown) {
  try {
    const target = (ctx && ctx.host) || host
    if (!target || typeof target.paneVisibility !== 'function') return () => {}
    const atom = target.paneVisibility(FEED_PANE_ID)
    if (!atom) return () => {}
    let seenVisible = !!(typeof atom.get === 'function' && atom.get())
    const onChange = visible => {
      if (visible) { seenVisible = true; onShown() } else if (seenVisible) { seenVisible = false; onHidden() }
    }
    const stop = typeof atom.listen === 'function' ? atom.listen(onChange)
      : typeof atom.subscribe === 'function' ? atom.subscribe(onChange) : null
    return typeof stop === 'function' ? stop : () => {}
  } catch (e) { return () => {} }
}

/** The /radar "Radar feed" button: close an open pane, otherwise (re)open and reveal it. */
function toggleFeed(ctx, feedStore) {
  if (feedStore.get() && isFeedOnScreen(ctx)) feedStore.set(false)
  else showFeed(ctx, feedStore)
}

function useMuted(mute) {
  const [muted, setMuted] = React.useState(mute.get())
  React.useEffect(() => mute.subscribe(setMuted), [mute])
  return muted
}

function useStoreValue(store) {
  const [value, setValue] = React.useState(store.get())
  React.useEffect(() => store.subscribe(setValue), [store])
  return value
}

function useBoard(api) {
  const query = useQuery({ queryKey: ['radar', 'board'], queryFn: () => api.board(), refetchInterval: POLL_MS })
  return { board: query.data || null, error: query.error || null, loading: !!query.isLoading, refetch: query.refetch }
}

/** Proactive toasts: poll the board, notify on new inbound A2A tasks and auth bursts unless muted. */
function watchRadarEvents(ctx, api, mute) {
  const notify = (ctx && ctx.host && ctx.host.notify) || (host && host.notify)
  if (typeof notify !== 'function' || typeof ctx.setInterval !== 'function') return null
  const seen = { baselined: false, events: new Set(), alerts: new Set() }
  const tick = async () => {
    let board
    try {
      board = await api.board()
    } catch (e) {
      return
    }
    const toasts = diffAlerts(board, seen) // always advance the memory, even while muted
    if (mute.get()) return
    for (const toast of toasts) {
      notify({
        id: toast.id,
        kind: toast.kind,
        title: toast.title,
        message: toast.message,
        action: { label: 'Open Radar', onClick: () => navigateTo(ctx, RADAR_ROUTE) }
      })
    }
  }
  void tick()
  return ctx.setInterval(() => { void tick() }, WATCH_MS)
}

// -------------------------------------------------------------------------------------------------
// Components
// -------------------------------------------------------------------------------------------------

function Styles() {
  return jsx('style', { 'data-radar-styles': 'true', children: RADAR_CSS })
}

function RadarChip({ api, ctx }) {
  const { board } = useBoard(api)
  const summary = summarizeBoard(board)
  if (!summary.ok) return null
  const title = 'Radar: ' + summary.running + ' running, ' + summary.warm + ' warm, ' + summary.total + ' tracked'
  const props = {
    type: 'button',
    className: summary.live ? 'radar-chip live' : 'radar-chip',
    title,
    'data-radar-chip': 'true',
    onClick: () => navigateTo(ctx, RADAR_ROUTE)
  }
  if (summary.live) props['data-radar'] = 'running'
  props.children = [
    jsx(Styles, {}, 'css'),
    jsx(Codicon, { name: 'radio-tower' }, 'icon'),
    jsx('b', { children: String(summary.total) }, 'count')
  ]
  return jsxs('button', props)
}

function RadarCard({ card, onOpen }) {
  const status = card.status === 'running' ? 'live' : card.status === 'warm' ? 'warm' : 'idle'
  const target = sessionPath(card)
  const open = () => { if (target) onOpen(target) }
  const meta = [
    jsx('span', { className: 'radar-badge', children: card.actor_badge || card.actor || card.lane }, 'badge'),
    card.active_tool ? jsx('span', { className: 'radar-tool', children: card.active_tool }, 'tool') : null,
    card.model ? jsx('span', { children: card.model }, 'model') : null,
    jsx('span', { children: (card.message_count || 0) + ' msg \u00b7 ' + (card.tool_call_count || 0) + ' tools' }, 'counts'),
    jsx('span', { children: formatIdle(card.idle_s) }, 'idle')
  ]
  return jsxs('div', {
    className: 'radar-card ' + status,
    role: 'button',
    tabIndex: target ? 0 : -1,
    'data-radar-card': card.id,
    'data-status': card.status,
    title: (card.title || '') + (card.cwd ? '\n' + card.cwd : ''),
    onClick: open,
    onKeyDown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } },
    children: [
      jsx('div', { className: 'radar-card-title', children: card.title || card.id }, 'title'),
      card.last_message_preview ? jsx('div', { className: 'radar-card-preview', children: card.last_message_preview }, 'preview') : null,
      jsx('div', { className: 'radar-card-meta', children: meta }, 'meta')
    ]
  })
}

function LanePager({ lane, onPage }) {
  return jsxs('div', {
    className: 'radar-pager',
    'data-radar-pager': lane.key,
    children: [
      jsx('button', {
        type: 'button',
        className: 'radar-btn',
        'data-radar-prev': 'true',
        disabled: lane.page <= 1,
        onClick: () => onPage(lane.key, lane.page - 1),
        children: '\u2039 Prev'
      }, 'prev'),
      jsx('span', { children: 'Page ' + lane.page + ' / ' + lane.totalPages + ' (' + lane.totalItems + ')' }, 'at'),
      jsx('button', {
        type: 'button',
        className: 'radar-btn',
        'data-radar-next': 'true',
        disabled: lane.page >= lane.totalPages,
        onClick: () => onPage(lane.key, lane.page + 1),
        children: 'Next \u203a'
      }, 'next')
    ]
  })
}

function RadarLane({ lane, filtering, onOpen, onPage }) {
  const total = typeof lane.total === 'number' ? lane.total : lane.cards.length
  const filtered = typeof lane.filtered === 'number' ? lane.filtered : lane.cards.length
  const emptyText = total === 0 ? 'No sessions' : 'No matching sessions'
  const body = lane.cards.length
    ? lane.cards.map(card => jsx(RadarCard, { card, onOpen }, card.id))
    : jsx('div', { className: 'radar-empty', children: emptyText })
  const count = filtering ? filtered + ' of ' + total : lane.running + ' running \u00b7 ' + total
  return jsxs('section', {
    className: 'radar-lane',
    'data-radar-lane': lane.key,
    children: [
      jsxs('div', {
        className: 'radar-lane-head',
        children: [
          jsx('span', { children: lane.title }, 't'),
          jsx('span', { className: 'radar-count', children: count }, 'c')
        ]
      }, 'head'),
      body,
      lane.totalPages > 1 ? jsx(LanePager, { lane, onPage }, 'pager') : null
    ]
  })
}

/** A block is an application-level ingress deny at the Dashboard, never a firewall or WAF rule. */
const BLOCK_TOOLTIP = 'Ingress deny at the Dashboard. Not a firewall rule.'

/** `auto` / `manual` chip plus the entry's expiry (auto blocks expire; manual ones are permanent). */
function sourceChip(entry) {
  const source = entry.source === 'auto' ? 'auto' : 'manual'
  const expiry = Number(entry.expires_at) > 0 ? ' \u00b7 expires ' + new Date(Number(entry.expires_at) * 1000).toISOString().slice(0, 16).replace('T', ' ') + 'Z' : ''
  return jsx('span', { className: 'radar-badge', 'data-radar-source': source, children: source + expiry }, 'source')
}

function actionButton(act, label, onClick, extra, title) {
  return jsx('button', { type: 'button', className: 'radar-btn' + (extra ? ' ' + extra : ''), 'data-radar-act': act, onClick, title, children: label }, act)
}

function ShieldAlert({ alert, onAction, onCopy }) {
  const canAct = !!alert.firewall_cmd // the backend only emits a firewall command for a validated IP address
  const cls = 'radar-shield-card' + (alert.severity === 'high' ? ' high' : '') + (alert.blocked ? ' blocked' : '')
  const meta = [
    jsx('span', { className: 'radar-badge', children: alert.severity || 'medium' }, 'sev'),
    jsx('span', { children: KIND_LABELS[alert.kind] || alert.kind || 'Rejections' }, 'kind'),
    alert.rate_per_min ? jsx('span', { children: alert.rate_per_min + '/min' }, 'rate') : null,
    alert.blocked ? jsx('span', { className: 'radar-tool', title: BLOCK_TOOLTIP, 'data-radar-blocked-badge': 'true', children: 'Blocked' }, 'blocked') : null,
    alert.blocked ? sourceChip(alert) : null
  ]
  const buttons = [
    canAct && !alert.blocked ? actionButton('block', 'Block IP (app-level)', () => onAction('block', alert), 'danger', BLOCK_TOOLTIP) : null,
    canAct && alert.blocked ? actionButton('unblock', 'Unblock', () => onAction('unblock', alert)) : null,
    actionButton('dismiss', 'Dismiss', () => onAction('dismiss', { id: alert.id })),
    alert.firewall_cmd ? actionButton('copy-firewall', 'Copy Firewall Rule', () => onCopy(alert.firewall_cmd)) : null,
    alert.waf_expr ? actionButton('copy-waf', 'Copy WAF Rule', () => onCopy(alert.waf_expr)) : null
  ]
  return jsxs('div', {
    className: cls,
    'data-radar-alert': alert.id,
    children: [
      jsx('div', { className: 'radar-shield-line', children: alert.count + ' rejected requests from ' + alert.ip + ' (' + alert.reason + ') on ' + alert.path }, 'line'),
      jsx('div', { className: 'radar-shield-meta', children: meta }, 'meta'),
      jsx('div', { className: 'radar-shield-actions', children: buttons }, 'acts')
    ]
  })
}

/** Auth rejection bursts as actionable cards, plus the blocked-IP and dismissed folds. Nothing to show -> null. */
function SecurityShield({ alerts, blocked, onAction, onCopy }) {
  const live = (alerts || []).filter(a => a && !a.dismissed)
  const dismissed = (alerts || []).filter(a => a && a.dismissed)
  const blockedIps = blocked || []
  if (!live.length && !dismissed.length && !blockedIps.length) return null
  return jsxs('div', {
    className: 'radar-shield',
    'data-radar-shield': 'true',
    children: [
      jsxs('div', {
        className: 'radar-shield-head',
        children: [jsx(Codicon, { name: 'shield' }, 'i'), jsx('span', { children: 'Security Shield' }, 't'), jsx('span', { className: 'radar-count', children: live.length + ' active' }, 'n')]
      }, 'head'),
      ...live.map(alert => jsx(ShieldAlert, { alert, onAction, onCopy }, alert.id)),
      blockedIps.length ? jsxs('details', {
        'data-radar-blocked': 'true',
        children: [
          jsx('summary', { children: 'Blocked IPs (' + blockedIps.length + ')' }, 's'),
          ...blockedIps.map(b => jsxs('div', {
            className: 'radar-shield-row',
            children: [
              jsx('span', { title: BLOCK_TOOLTIP, children: b.ip + (b.reason ? ' \u00b7 ' + b.reason : '') }, 'ip'),
              sourceChip(b),
              actionButton('unblock', 'Unblock', () => onAction('unblock', { ip: b.ip }))
            ]
          }, b.ip))
        ]
      }, 'blocked') : null,
      dismissed.length ? jsxs('details', {
        'data-radar-dismissed': 'true',
        children: [
          jsx('summary', { children: 'Dismissed (' + dismissed.length + ')' }, 's'),
          ...dismissed.map(a => jsxs('div', {
            className: 'radar-shield-row',
            'data-radar-dismissed-row': a.id,
            children: [
              jsx('span', { children: a.count + ' from ' + a.ip + ' on ' + a.path }, 'd'),
              actionButton('undismiss', 'Restore', () => onAction('undismiss', { id: a.id }))
            ]
          }, a.id))
        ]
      }, 'dismissed') : null
    ]
  })
}

function option(value, label) {
  return jsx('option', { value, children: label }, value)
}

function RadarPage({ api, ctx, mute, pageSizeStore, feedStore }) {
  const { board, error, refetch } = useBoard(api)
  const muted = useMuted(mute)
  const pageSize = useStoreValue(pageSizeStore)
  const feedOpen = useStoreValue(feedStore)
  const [filters, setFilters] = React.useState(NO_FILTERS)
  const [pages, setPages] = React.useState({})
  const [actionError, setActionError] = React.useState('')
  const summary = summarizeBoard(board)
  const view = buildBoardView(board, filters, pages, pageSize, (board && board.generated_at) || Date.now() / 1000)
  const alerts = (board && board.auth_alerts) || []
  const blocked = (board && board.security && board.security.blocked_ips) || []
  const platforms = (board && board.platforms) || {}
  const onOpen = path => navigateTo(ctx, path)
  const setFilter = key => e => { setFilters(prev => ({ ...prev, [key]: e.target.value })); setPages({}) }
  const onPage = (key, page) => setPages(prev => ({ ...prev, [key]: page }))
  const onAction = (action, target) => {
    setActionError('')
    performSecurityAction(api, action, target, async () => { if (typeof refetch === 'function') await refetch() })
      .catch(e => setActionError(String((e && e.message) || e)))
  }
  const onCopy = text => {
    void copyText(ctx, text).then(ok => {
      const notify = (ctx && ctx.host && ctx.host.notify) || (host && host.notify)
      if (typeof notify === 'function') notify({ id: 'radar-copy', kind: ok ? 'info' : 'warning', title: ok ? 'Copied to clipboard' : 'Clipboard unavailable', message: ok ? text : 'Select the rule and copy it by hand.' })
    })
  }
  const status = ['gateway ' + (platforms.gateway_state || '?'), 'a2a ' + (platforms.a2a || '?'), 'buzz ' + (platforms.buzz || '?')].join(' \u00b7 ')
  const entityNames = view.entities.includes(filters.entity) || filters.entity === 'all' ? view.entities : [...view.entities, filters.entity]
  return jsxs('div', {
    className: 'radar-page',
    'data-radar-page': 'true',
    children: [
      jsx(Styles, {}, 'css'),
      jsxs('div', {
        className: 'radar-head',
        children: [
          jsx('h1', { children: 'Fleet & Session Radar' }, 'h'),
          jsx('span', { className: 'radar-sub', children: summary.running + ' running \u00b7 ' + summary.warm + ' warm \u00b7 ' + summary.total + ' tracked' }, 's'),
          jsx('span', { className: 'radar-sub', children: status }, 'p'),
          jsx('span', { className: 'radar-grow' }, 'g'),
          jsxs('button', {
            type: 'button',
            className: 'radar-btn',
            'aria-pressed': feedOpen ? 'true' : 'false',
            'data-radar-feed-toggle': feedOpen ? 'on' : 'off',
            title: feedOpen ? 'Hide the Radar feed pane' : 'Show the Radar feed pane',
            onClick: () => toggleFeed(ctx, feedStore),
            children: [jsx(Codicon, { name: 'layout-sidebar-right' }, 'i'), 'Radar feed']
          }, 'feed'),
          jsxs('button', {
            type: 'button',
            className: 'radar-btn',
            'aria-pressed': muted ? 'true' : 'false',
            'data-radar-mute': muted ? 'on' : 'off',
            title: muted ? 'Toasts are muted' : 'Toast on new inbound A2A tasks and auth rejection bursts',
            onClick: () => mute.toggle(),
            children: [
              jsx(Codicon, { name: muted ? 'bell-slash' : 'bell' }, 'i'),
              muted ? 'Muted' : 'Mute alerts'
            ]
          }, 'mute')
        ]
      }, 'head'),
      jsxs('div', {
        className: 'radar-filters',
        'data-radar-filters': 'true',
        children: [
          jsx('input', {
            type: 'search',
            className: 'radar-input',
            'data-radar-filter': 'q',
            placeholder: 'Search title, message, entity, cwd, model',
            'aria-label': 'Search sessions',
            value: filters.q,
            onChange: setFilter('q')
          }, 'q'),
          jsx('select', {
            className: 'radar-select',
            'data-radar-filter': 'entity',
            'aria-label': 'Entity',
            value: filters.entity,
            onChange: setFilter('entity'),
            children: [option('all', 'All entities'), ...entityNames.map(name => option(name, name))]
          }, 'entity'),
          jsx('select', {
            className: 'radar-select',
            'data-radar-filter': 'status',
            'aria-label': 'Status',
            value: filters.status,
            onChange: setFilter('status'),
            children: STATUS_OPTIONS.map(([value, label]) => option(value, label))
          }, 'status'),
          jsx('select', {
            className: 'radar-select',
            'data-radar-filter': 'date',
            'aria-label': 'Date window',
            value: filters.dateWindow,
            onChange: setFilter('dateWindow'),
            children: DATE_OPTIONS.map(([value, label]) => option(value, label))
          }, 'date'),
          view.filtering ? jsx('button', {
            type: 'button',
            className: 'radar-btn',
            'data-radar-clear': 'true',
            onClick: () => { setFilters(NO_FILTERS); setPages({}) },
            children: 'Clear filters'
          }, 'clear') : null,
          jsx('span', { className: 'radar-grow' }, 'g'),
          jsx('select', {
            className: 'radar-select',
            'data-radar-pagesize': String(pageSize),
            'aria-label': 'Cards per page',
            value: String(pageSize),
            onChange: e => { pageSizeStore.set(e.target.value); setPages({}) },
            children: PAGE_SIZES.map(n => option(String(n), n + ' / page'))
          }, 'size')
        ]
      }, 'filters'),
      error && !board ? jsx('div', { className: 'radar-alert', children: 'Radar backend unavailable. Enable the radar plugin and restart the dashboard.' }, 'err') : null,
      actionError ? jsx('div', { className: 'radar-alert', children: 'Security action failed: ' + actionError }, 'acterr') : null,
      jsx(SecurityShield, { alerts, blocked, onAction, onCopy }, 'shield'),
      jsx('div', { className: 'radar-lanes', children: view.lanes.map(lane => jsx(RadarLane, { lane, filtering: view.filtering, onOpen, onPage }, lane.key)) }, 'lanes')
    ]
  })
}

function FeedSection({ title, rows, empty }) {
  return jsxs('div', {
    children: [
      jsx('h2', { children: title }, 'h'),
      rows.length ? rows : jsx('div', { className: 'radar-empty', children: empty })
    ]
  })
}

function RadarFeed({ api, feedStore }) {
  const { board } = useBoard(api)
  const running = orderedLanes(board).flatMap(l => l.cards).filter(c => c.status === 'running')
  const events = ((board && board.a2a_events) || []).slice(0, 12)
  const alerts = ((board && board.auth_alerts) || []).filter(a => a && !a.dismissed)
  return jsxs('div', {
    className: 'radar-feed',
    'data-radar-feed': 'true',
    children: [
      jsx(Styles, {}, 'css'),
      jsxs('div', {
        className: 'radar-feed-head',
        children: [
          jsx('span', { children: 'Radar feed' }, 't'),
          jsx('button', {
            type: 'button',
            className: 'radar-btn',
            'data-radar-feed-close': 'true',
            'aria-label': 'Close Radar feed',
            title: 'Close Radar feed (reopen it from the Radar page)',
            onClick: () => { if (feedStore) feedStore.set(false) },
            children: '\u2715'
          }, 'close')
        ]
      }, 'head'),
      jsx(FeedSection, {
        title: 'Running now',
        empty: 'Nothing running',
        rows: running.map(c => jsx('div', {
          className: 'radar-feed-row',
          children: (c.actor_badge || c.actor) + ' \u00b7 ' + (c.active_tool || 'turn') + ' \u00b7 ' + (c.title || c.id)
        }, c.id))
      }, 'run'),
      jsx(FeedSection, {
        title: 'A2A exchanges',
        empty: 'No exchanges',
        rows: events.map(e => jsx('div', {
          className: 'radar-feed-row',
          children: e.direction + ' \u00b7 ' + e.peer + ' \u00b7 ' + e.summary
        }, e.id))
      }, 'a2a'),
      jsx(FeedSection, {
        title: 'Auth alerts',
        empty: 'No alerts',
        rows: alerts.map(a => jsx('div', {
          className: 'radar-feed-row',
          children: a.count + ' \u00d7 ' + a.reason + ' from ' + a.ip + ' on ' + a.path
        }, a.id))
      }, 'auth')
    ]
  })
}

// -------------------------------------------------------------------------------------------------
// Plugin definition
// -------------------------------------------------------------------------------------------------

const plugin = {
  id: 'radar',
  name: 'Radar',
  description: 'Fleet & Session Radar: A2A peers, Buzz, ACP and remote-user sessions on one board.',
  register(ctx) {
    const api = createApi(ctx)
    const mute = createMuteStore(ctx)
    const pageSizeStore = createPageSizeStore(ctx)
    const feedStore = createFeedStore(ctx)
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        title: 'Radar',
        data: { path: '/radar' },
        render: () => jsx(RadarPage, { api, ctx, mute, pageSizeStore, feedStore })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { codicon: 'radio-tower', label: 'Radar', path: '/radar', asTile: true }
      },
      {
        id: 'chip',
        area: (STATUSBAR_AREAS && STATUSBAR_AREAS.right) || 'statusBar.right',
        order: 130,
        render: () => jsx(RadarChip, { api, ctx })
      }
    ])
    // The feed pane exists only while the user wants it: closing unregisters it, showFeed registers it again.
    // A close from the native tab strip leaves the (already dismissed) registration alone but is still recorded.
    let unregisterFeed = null
    let stopWatching = null
    let recordingNativeClose = false
    const syncFeed = open => {
      if (open && !unregisterFeed) {
        unregisterFeed = ctx.registerMany([{
          id: 'feed',
          area: PANES_AREA,
          title: 'Radar feed',
          data: { placement: 'right', width: '260px', collapsible: true, defaultCollapsed: true },
          render: () => jsx(RadarFeed, { api, feedStore })
        }])
        stopWatching = watchFeedVisibility(
          ctx,
          () => {
            recordingNativeClose = true
            try { feedStore.set(false) } finally { recordingNativeClose = false }
          },
          () => { if (!feedStore.get()) feedStore.set(true) }
        )
      } else if (!open && unregisterFeed && !recordingNativeClose) {
        if (stopWatching) stopWatching()
        stopWatching = null
        unregisterFeed()
        unregisterFeed = null
      }
    }
    syncFeed(feedStore.get())
    feedStore.subscribe(syncFeed)
    watchRadarEvents(ctx, api, mute)
  }
}

export default plugin
export const __test = Object.freeze({
  formatIdle, summarizeBoard, orderedLanes, sessionPath, diffAlerts, createMuteStore, watchRadarEvents,
  filterCards, collectEntities, paginateItems, buildBoardView, isFiltering, securityBody, performSecurityAction, copyText,
  createApi, createPageSizeStore, createFeedStore, showFeed, toggleFeed,
  RadarChip, RadarPage, RadarFeed, RadarCard, RadarLane, SecurityShield, RADAR_CSS, MUTE_KEY, PAGE_SIZES
})
