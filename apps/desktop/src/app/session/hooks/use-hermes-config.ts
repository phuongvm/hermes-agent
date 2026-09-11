import { type MutableRefObject, useCallback, useRef, useState } from 'react'

import { setTerminalFontFamilyFromConfig } from '@/app/right-sidebar/terminal/terminal-font'
import { getApiRequestConnection, getApiRequestProfile, getHermesConfig, getHermesConfigDefaults } from '@/hermes'
import { BUILTIN_PERSONALITIES, normalizePersonalityValue, personalityNamesFromConfig } from '@/lib/chat-runtime'
import { normalize } from '@/lib/text'
import { setDisplayTimestampsFromConfig } from '@/store/display-timestamps'
import { $activeGatewayProfile, getProfileFetchGeneration, isGatewayOpen, normalizeProfileKey } from '@/store/profile'
import {
  getComposerSelectionGeneration,
  getCurrentModelSource,
  setAvailablePersonalities,
  setCurrentFastMode,
  setCurrentPersonality,
  setCurrentReasoningEffort,
  setCurrentServiceTier,
  setDefaultReasoningEffort,
  setIntroPersonality
} from '@/store/session'
import {
  applyAutoSpeakFromConfig,
  applyThinkingSoundFromConfig,
  applyVoiceStopPhraseFromConfig
} from '@/store/voice-prefs'

const DEFAULT_VOICE_SECONDS = 120
const FAST_TIERS = new Set(['fast', 'priority', 'on'])

function recordingLimit(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : DEFAULT_VOICE_SECONDS
}

/** config.yaml hands back whatever the user wrote — `reasoning_effort: false`
 *  (or `off`/`no`, which YAML also parses to boolean false) means thinking
 *  disabled, and a bare boolean must not throw on `.trim()`. */
function normalizeConfigEffort(value: unknown): string {
  if (value === false) {
    return 'none'
  }

  if (typeof value !== 'string') {
    return ''
  }

  const effort = normalize(value)

  return effort === 'false' || effort === 'disabled' ? 'none' : effort
}

interface HermesConfigOptions {
  activeSessionIdRef: MutableRefObject<string | null>
}

interface InFlightConfigFetch {
  scopeKey: string
  epoch: number
  promise: Promise<[any, any]>
}

export function useHermesConfig({ activeSessionIdRef }: HermesConfigOptions) {
  const [voiceMaxRecordingSeconds, setVoiceMaxRecordingSeconds] = useState(DEFAULT_VOICE_SECONDS)
  const [sttEnabled, setSttEnabled] = useState(true)
  const profileRefreshEpochRef = useRef(0)
  const inFlightFetchRef = useRef<InFlightConfigFetch | null>(null)

  const getScopeKey = useCallback(() => {
    const connection = (getApiRequestConnection() ?? '').trim()
    const profile = normalizeProfileKey(getApiRequestProfile() ?? $activeGatewayProfile.get())
    const generation = getProfileFetchGeneration()
    return {
      connection,
      profile,
      generation,
      key: `${connection}\0${profile}\0${generation}`
    }
  }, [])

  const refreshHermesConfig = useCallback(
    async (force = false, shouldPublish: () => boolean = () => true) => {
      if (!isGatewayOpen()) {
        return
      }

      if (force) {
        profileRefreshEpochRef.current += 1
      }

      const profileRefreshEpoch = profileRefreshEpochRef.current
      const selectionGeneration = getComposerSelectionGeneration()
      const { key: scopeKey } = getScopeKey()

      let fetchPromise: Promise<[any, any]>
      const currentInFlight = inFlightFetchRef.current

      if (
        currentInFlight &&
        currentInFlight.scopeKey === scopeKey &&
        (!force || currentInFlight.epoch === profileRefreshEpoch)
      ) {
        fetchPromise = currentInFlight.promise
      } else {
        fetchPromise = Promise.all([getHermesConfig(), getHermesConfigDefaults().catch(() => ({}))])
        inFlightFetchRef.current = {
          scopeKey,
          epoch: profileRefreshEpoch,
          promise: fetchPromise
        }
      }

      try {
        const [config, defaults] = await fetchPromise

        if (!isGatewayOpen()) {
          return
        }

        const canPublish = () =>
          profileRefreshEpochRef.current === profileRefreshEpoch &&
          getScopeKey().key === scopeKey &&
          shouldPublish()

        if (!canPublish()) {
          return
        }

        const personality = normalizePersonalityValue(
          typeof config.display?.personality === 'string' ? config.display.personality : ''
        )

        if (!canPublish()) {
          return
        }

        setIntroPersonality(personality)
        // Active sessions keep their per-session value; standalone falls back to config.
        setCurrentPersonality(prev => (activeSessionIdRef.current ? prev || personality : personality))
        setAvailablePersonalities([
          ...new Set([
            'none',
            ...BUILTIN_PERSONALITIES,
            ...personalityNamesFromConfig(defaults),
            ...personalityNamesFromConfig(config)
          ])
        ])

        const reasoning = normalizeConfigEffort(config.agent?.reasoning_effort)
        const tier = (config.agent?.service_tier ?? '').trim()

        // Publish the profile default regardless of whether the composer is
        // reseeded below: picker rows and preset application resolve "the
        // default" from here, so a manual model pick must not leave them
        // rendering/applying Hermes' built-in medium over the user's config.
        if (!canPublish()) {
          return
        }

        setDefaultReasoningEffort(reasoning)

        const shouldSeedComposer =
          !activeSessionIdRef.current &&
          getComposerSelectionGeneration() === selectionGeneration &&
          (force || getCurrentModelSource() !== 'manual')

        if (shouldSeedComposer) {
          if (!canPublish()) {
            return
          }

          setCurrentReasoningEffort(reasoning)
          setCurrentFastMode(FAST_TIERS.has(tier.toLowerCase()))
        }

        if (!canPublish()) {
          return
        }

        setCurrentServiceTier(prev => (activeSessionIdRef.current ? prev : tier))

        if (!canPublish()) {
          return
        }

        setVoiceMaxRecordingSeconds(recordingLimit(config.voice?.max_recording_seconds))
        setSttEnabled(config.stt?.enabled !== false)

        if (!canPublish()) {
          return
        }

        setDisplayTimestampsFromConfig(config.display?.timestamps)
        setTerminalFontFamilyFromConfig(config.terminal?.font_family)

        if (!canPublish()) {
          return
        }

        applyAutoSpeakFromConfig(config)
        applyVoiceStopPhraseFromConfig(config)
        applyThinkingSoundFromConfig(config)
      } catch {
        // Config is nice-to-have; chat still works without it.
      } finally {
        if (inFlightFetchRef.current?.promise === fetchPromise) {
          inFlightFetchRef.current = null
        }
      }
    },
    [activeSessionIdRef, getScopeKey]
  )

  return { refreshHermesConfig, sttEnabled, voiceMaxRecordingSeconds }
}
