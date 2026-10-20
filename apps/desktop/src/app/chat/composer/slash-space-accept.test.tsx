// @vitest-environment jsdom
import { AssistantRuntimeProvider, useExternalStoreRuntime } from '@assistant-ui/react'
import type { ThreadMessageLike } from '@assistant-ui/react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
import { I18nProvider } from '@/i18n'
import { queryClient } from '@/lib/query-client'
import { mainComposerScope } from '@/store/composer'

import { composerPlainText, placeCaretAtOffset, renderComposerContents, RICH_INPUT_SLOT } from './rich-editor'
import type { ChatBarState } from './types'

import { ChatBar } from './index'

// THE INVARIANT: at the slash command-name stage, Space is the delimiter
// before arguments. The popover's highlight is only the list's default first
// row, so Space must not be consumed (no preventDefault) and must not swap the
// typed token for a completion the user never chose — unless the user arrowed
// to a row deliberately, in which case Space commits exactly that row.
//
// Driven through the REAL ChatBar editor: typed text goes through `input` +
// keyup → refreshTrigger, the popover is fed by a stub gateway, and keys are
// real keydown events on the contentEditable. jsdom does not insert text on an
// un-prevented keydown, so "a literal space is typed" is proven as: the
// keydown is NOT defaultPrevented AND the typed token is still plain text with
// no chip.
const SKILLS = ['/zeta-alpha', '/zeta-beta', '/zeta-gamma']

const state: ChatBarState = {
  model: { canSwitch: false, model: '', provider: '' },
  tools: { enabled: false, label: '' },
  voice: { enabled: false, active: false }
}

const request = vi.fn(async (method: string, params?: { text?: string }) => {
  if (method === 'commands.catalog') {
    return { pairs: SKILLS.map(command => [command, `${command.slice(1)} skill`]) }
  }

  if (method === 'complete.slash') {
    const typed = (params?.text ?? '').toLowerCase()

    return {
      items: SKILLS.filter(command => command.startsWith(typed)).map(command => ({
        text: command,
        display: command,
        meta: `${command.slice(1)} skill`,
        kind: 'skill'
      }))
    }
  }

  return {}
})

const gateway = { request } as unknown as HermesGateway

function Harness() {
  const runtime = useExternalStoreRuntime({
    convertMessage: (message: ThreadMessageLike) => message,
    isRunning: false,
    messages: [] as ThreadMessageLike[],
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <MemoryRouter>
        <I18nProvider configClient={null} initialLocale="en">
          <ChatBar
            busy={false}
            disabled={false}
            gateway={gateway}
            onCancel={vi.fn()}
            onSubmit={vi.fn(async () => true)}
            state={state}
          />
        </I18nProvider>
      </MemoryRouter>
    </AssistantRuntimeProvider>
  )
}

afterEach(() => {
  cleanup()
  mainComposerScope.clear()
  queryClient.clear()
  request.mockClear()
})

function openComposer() {
  const { container } = render(<Harness />)
  const editor = container.querySelector<HTMLElement>(`[data-slot="${RICH_INPUT_SLOT}"]`)!

  // jsdom implements neither isContentEditable nor contenteditable focus.
  Object.defineProperty(editor, 'isContentEditable', { configurable: true, value: true })
  editor.tabIndex = 0
  editor.focus()

  return editor
}

/** Type `text` as the whole draft: DOM edit, then input + keyup as a keystroke. */
async function type(editor: HTMLElement, text: string) {
  await act(async () => {
    renderComposerContents(editor, text)
    placeCaretAtOffset(editor, text.length)
    fireEvent.input(editor)
    fireEvent.keyUp(editor, { key: text.slice(-1) })
  })
}

function rows() {
  return Array.from(screen.queryByRole('listbox')?.querySelectorAll<HTMLButtonElement>('button') ?? [])
}

function highlightedRow() {
  return screen.queryByRole('listbox')?.querySelector<HTMLElement>('button[data-highlighted]') ?? null
}

/** Wait until the popover lists exactly `expected` (by row text). */
async function waitForRows(expected: string[]) {
  await waitFor(() => {
    expect(rows().map(row => row.textContent ?? '')).toEqual(expected.map(text => expect.stringContaining(text)))
  })
}

/** Fire a keydown; returns true when the handler called preventDefault(). */
function press(editor: HTMLElement, key: string, code = key) {
  let notPrevented = true

  act(() => {
    notPrevented = fireEvent.keyDown(editor, { key, code })
  })

  return !notPrevented
}

function chip(editor: HTMLElement) {
  return editor.querySelector<HTMLElement>('[data-ref-text]')
}

describe('Space at the slash command-name stage is a literal delimiter by default', () => {
  it('does not consume Space after a fully typed command name', async () => {
    const editor = openComposer()

    await type(editor, '/zeta-alpha')
    await waitForRows(['/zeta-alpha'])

    expect(press(editor, ' ', 'Space')).toBe(false)
    expect(chip(editor)).toBeNull()
    expect(composerPlainText(editor)).toBe('/zeta-alpha')
  })

  it('does not complete a partial command name to the un-chosen first row', async () => {
    const editor = openComposer()

    await type(editor, '/zeta-a')
    await waitForRows(['/zeta-alpha'])

    expect(press(editor, ' ', 'Space')).toBe(false)
    expect(chip(editor)).toBeNull()
    expect(composerPlainText(editor)).toBe('/zeta-a')
  })

  it('does not consume Space after an inline skill reference in prose', async () => {
    const editor = openComposer()

    await type(editor, 'look at /zeta-alpha')
    await waitForRows(['/zeta-alpha'])

    expect(press(editor, ' ', 'Space')).toBe(false)
    expect(chip(editor)).toBeNull()
    expect(composerPlainText(editor)).toBe('look at /zeta-alpha')
  })

  it('does not treat a mouse hover as an explicit pick', async () => {
    const editor = openComposer()

    await type(editor, '/zeta-')
    await waitForRows(SKILLS)

    act(() => {
      fireEvent.mouseEnter(rows()[1]!)
    })

    await waitFor(() => expect(highlightedRow()?.textContent).toContain('/zeta-beta'))

    expect(press(editor, ' ', 'Space')).toBe(false)
    expect(chip(editor)).toBeNull()
    expect(composerPlainText(editor)).toBe('/zeta-')
  })
})

describe('an explicit arrow selection accepts on Space, Enter or Tab', () => {
  it('commits the arrowed row on Space', async () => {
    const editor = openComposer()

    await type(editor, '/zeta-')
    await waitForRows(SKILLS)

    expect(press(editor, 'ArrowDown')).toBe(true)
    await waitFor(() => expect(highlightedRow()?.textContent).toContain('/zeta-beta'))

    expect(press(editor, ' ', 'Space')).toBe(true)
    expect(chip(editor)?.dataset.refText).toBe('/zeta-beta')
  })

  it('commits the arrowed row on Enter', async () => {
    const editor = openComposer()

    await type(editor, '/zeta-')
    await waitForRows(SKILLS)

    expect(press(editor, 'ArrowDown')).toBe(true)
    await waitFor(() => expect(highlightedRow()?.textContent).toContain('/zeta-beta'))

    expect(press(editor, 'Enter')).toBe(true)
    expect(chip(editor)?.dataset.refText).toBe('/zeta-beta')
  })

  it('accepts the default highlight on Tab without arrowing', async () => {
    const editor = openComposer()

    await type(editor, '/zeta-')
    await waitForRows(SKILLS)

    expect(press(editor, 'Tab')).toBe(true)
    expect(chip(editor)?.dataset.refText).toBe('/zeta-alpha')
  })

  it('drops the explicit selection once typing changes the query', async () => {
    const editor = openComposer()

    await type(editor, '/zeta-')
    await waitForRows(SKILLS)

    expect(press(editor, 'ArrowDown')).toBe(true)
    await waitFor(() => expect(highlightedRow()?.textContent).toContain('/zeta-beta'))

    await type(editor, '/zeta-b')
    await waitForRows(['/zeta-beta'])

    expect(press(editor, ' ', 'Space')).toBe(false)
    expect(chip(editor)).toBeNull()
    expect(composerPlainText(editor)).toBe('/zeta-b')
  })
})
