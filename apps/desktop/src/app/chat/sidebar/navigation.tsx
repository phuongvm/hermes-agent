import { useId, useState } from 'react'
import { useLocation } from 'react-router'

import type { AppView } from '@/app/routes'
import { SidebarPanelLabel } from '@/app/shell/sidebar-label'
import type { SidebarNavItem } from '@/app/types'
import { ContextMenu, ContextMenuContent, ContextMenuTrigger } from '@/components/ui/context-menu'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { KbdGroup } from '@/components/ui/kbd'
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem
} from '@/components/ui/sidebar'
import { TipKeybindLabel } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { recordAction } from '@/store/desktop-metrics'
import { $newChatProfile } from '@/store/profile'
import { openRouteTile } from '@/store/route-tiles'

import { type NewSessionSplitHandler, startNewSessionDrag } from '../new-session-drag'

import { CONTEXT_SPLIT_KIT, SplitSubmenu } from './split-submenu'

interface SidebarNavigationProps {
  navItems: SidebarNavItem[]
  currentView: AppView
  onNavigate: (item: SidebarNavItem) => void
  onNewSessionSplit: NewSessionSplitHandler
  newSessionKbd: string[]
  newSessionKbdFlash: boolean
}

export function SidebarNavigation({
  navItems,
  currentView,
  onNavigate,
  onNewSessionSplit,
  newSessionKbd,
  newSessionKbdFlash
}: SidebarNavigationProps) {
  const { t } = useI18n()
  const s = t.sidebar
  const { pathname } = useLocation()

  return (
    <SidebarMenu className="gap-px">
      {navItems.map(item => {
        const isInteractive = Boolean(item.action) || Boolean(item.route)

        const active =
          (item.id === 'capabilities' && currentView === 'capabilities') ||
          (item.id === 'messaging' && currentView === 'messaging') ||
          (item.id === 'artifacts' && currentView === 'artifacts') ||
          (item.id === 'cron' && currentView === 'cron') ||
          (item.id === 'settings' && currentView === 'settings') ||
          (item.id === 'webhooks' && currentView === 'webhooks') ||
          // Contributed rows light up at their own route.
          (currentView === 'extension' && Boolean(item.route) && pathname === item.route)

        const isNewSession = item.id === 'new-session'

        const button = (
          <SidebarMenuButton
            aria-disabled={!isInteractive}
            className={cn(
              // no-drag: these rows sit directly under the titlebar's
              // [-webkit-app-region:drag] strips (app-shell.tsx), with only
              // 6px of clearance. Drag regions win hit-testing over DOM
              // (pointer-events can't override), and on Linux/WSLg the
              // resolved region has been observed to swallow clicks on the
              // top rows. Same carve-out as USER_BUBBLE_BASE_CLASS in
              // thread.tsx.
              'flex h-7 w-full justify-start gap-2 rounded-md border border-transparent px-2 text-left text-[0.8125rem] font-medium text-(--ui-text-secondary) transition-colors duration-100 ease-out [-webkit-app-region:no-drag] hover:bg-(--ui-control-hover-background) hover:text-foreground hover:transition-none',
              active &&
                'border-(--ui-stroke-tertiary) bg-(--ui-control-active-background) text-foreground shadow-none hover:border-(--ui-stroke-tertiary)!',
              !isInteractive && 'cursor-default hover:border-transparent hover:bg-transparent hover:text-inherit'
            )}
            // A tip anchored to the label points at the end of the
            // word; the row is what it's actually about.
            data-tip-region=""
            onClick={() => {
              // A plain new session lands in whatever profile the live
              // gateway is on (= the active switcher context). null →
              // no swap. The switcher header is the single place to
              // change which profile that is.
              if (isNewSession) {
                $newChatProfile.set(null)
              }

              if (item.keybindActionId) {
                recordAction(item.keybindActionId, 'click')
              }

              onNavigate(item)
            }}
            onPointerDown={event => {
              // The "New session" row is a drag source too: drag it onto
              // a chat zone's tab strip / edge / center to create the
              // session exactly there (stack / split). The pointer drag
              // session owns the gesture — a sub-threshold release falls
              // through to the onClick above (ordinary new session), and
              // an engaged drag suppresses that click so it never
              // double-creates. The create callback sets $newChatProfile
              // itself (the suppressed click can't), so a dragged new
              // session lands in the same profile a click would.
              if (!isNewSession) {
                return
              }

              startNewSessionDrag(placement => {
                $newChatProfile.set(null)
                onNewSessionSplit(placement.dir, { anchor: placement.anchor, before: placement.before })
              }, event)
            }}
            tooltip={
              item.keybindActionId
                ? {
                    children: <TipKeybindLabel actionId={item.keybindActionId} text={s.nav[item.id] ?? item.label} />
                  }
                : (s.nav[item.id] ?? item.label)
            }
            type="button"
          >
            <item.icon className="size-4 shrink-0 text-[color-mix(in_srgb,currentColor_72%,transparent)]" />
            {/* Shrink-to-fit, not flex-1: the label carries the row's
          `data-tour` handle, and anything anchored to it should
          land at the end of the WORD, not out at the sidebar's
          edge. Still truncates — `min-w-0` lets it shrink past
          its content when the rail is narrow — and the trailing
          chip's `ml-auto` was already doing the pushing that
          `flex-1` looked like it was for.
          Its own `sidebar-nav-` namespace: the overlay nav owns
          `nav-<id>`, and both are on screen with Settings open. */}
            <span className="min-w-0 truncate" data-tip-arrow-only="" data-tour={`sidebar-nav-${item.id}`}>
              {s.nav[item.id] ?? item.label}
            </span>
            {isNewSession && (
              <KbdGroup
                className={cn('ml-auto opacity-55', newSessionKbdFlash && 'opacity-100!')}
                keys={newSessionKbd}
                size="sm"
              />
            )}
          </SidebarMenuButton>
        )

        // New session + route-backed pages can open in a split —
        // right-click for the directional "Open in split" submenu.
        return (
          <SidebarMenuItem key={item.id}>
            {isNewSession || item.route ? (
              <ContextMenu>
                <ContextMenuTrigger asChild>{button}</ContextMenuTrigger>
                <ContextMenuContent aria-label={s.nav[item.id] ?? item.label}>
                  <SplitSubmenu
                    kit={CONTEXT_SPLIT_KIT}
                    label={s.row.openInSplit}
                    onSplit={dir => {
                      if (isNewSession) {
                        onNewSessionSplit(dir)
                      } else if (item.route) {
                        openRouteTile(item.route, dir)
                      }
                    }}
                  />
                </ContextMenuContent>
              </ContextMenu>
            ) : (
              button
            )}
          </SidebarMenuItem>
        )
      })}
    </SidebarMenu>
  )
}

export function SidebarTools(props: SidebarNavigationProps) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const contentId = useId()

  return (
    <SidebarGroup className="min-h-0 max-h-[40%] shrink-0 p-0 pb-1">
      <div className="group/section flex shrink-0 items-center justify-between pb-1 pt-1.5">
        <button
          aria-controls={contentId}
          aria-expanded={open}
          className="group/section-label flex w-fit min-w-0 items-center gap-1 bg-transparent text-left leading-none"
          onClick={() => setOpen(value => !value)}
          type="button"
        >
          <SidebarPanelLabel>{t.connectorsPage.tools.title}</SidebarPanelLabel>
          <DisclosureCaret
            className="text-(--ui-text-tertiary) opacity-0 transition group-hover/section-label:opacity-100"
            open={open}
          />
        </button>
      </div>
      <SidebarGroupContent
        className="scrollbar-fade min-h-0 overflow-x-hidden overflow-y-auto pb-1.75"
        hidden={!open}
        id={contentId}
      >
        {open && <SidebarNavigation {...props} />}
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
