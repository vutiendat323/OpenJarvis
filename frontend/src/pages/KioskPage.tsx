import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { X } from 'lucide-react';

import { VisualizerControls } from '@/components/Visualizer/VisualizerControls';
import { isKioskTheme, KIOSK_THEMES } from '@/components/Kiosk/kioskThemes';
import '@/components/Kiosk/kioskThemes.css';
import { FloatingCodexPet } from '@/components/Kiosk/Pet/FloatingCodexPet';
import { SharedBrowserPane } from '@/components/Kiosk/SharedBrowserPane';
import { VoiceWaveform } from '@/components/Kiosk/VoiceWaveform';
import { SpeechGlow } from '@/components/Kiosk/SpeechGlow';
import { ScreensMonitorButton } from '@/components/Kiosk/ScreensMonitorButton';
import { RemoteCameraButton } from '@/components/Kiosk/RemoteCameraButton';
import { StackCaption } from '@/components/Kiosk/StackCaption';
import { currentVoiceTurnRows } from '@/components/Chat/voiceTurnRows';
import { useDraggableResizable } from '@/hooks/useDraggableResizable';
import { useKioskState, type KioskState } from '@/hooks/useKioskState';
import { usePipecatVoiceMode } from '@/hooks/usePipecatVoiceMode';
import { useRemoteCamera } from '@/hooks/useRemoteCamera';
import { useSharedBrowser } from '@/hooks/useSharedBrowser';
import { useUiLanguage, type UiLanguage } from '@/hooks/useUiLanguage';
import { shouldShimmerVoiceStatus, voiceStatusLabel } from '@/hooks/voiceUiText';
import { apiFetch } from '@/lib/api';
import { useAppStore } from '@/lib/store';
import {
  createKioskPresentationLifecycle,
  ensurePresentationSession,
  shouldEnsurePresentationSession,
} from '@/lib/kioskPresentation';
import { VISUALIZER_DEFAULTS } from '@/components/Visualizer/types';
import type { VisualizerSettings, VoiceStatus } from '@/components/Visualizer/types';
import type { LocalVoiceStatus } from '@/hooks/voiceStatus';
import { kioskVoiceCommand, type KioskVoiceOwner } from './kioskVoicePolicy';

const STATUS_COLOR: Record<LocalVoiceStatus, string> = {
  idle: '#dae2f4',
  connecting: '#dae2f4',
  listening: '#b7ffdd',
  speaking: '#dce7ff',
  busy: '#dae2f4',
  processing: '#ffe6a9',
  inference: '#ffe6a9',
  tool: '#ffe6a9',
  error: '#ffd7df',
  ended: '#dae2f4',
};

export const VOICE_STATUS_GLOW_RGB: Record<LocalVoiceStatus, string> = {
  idle: '0, 242, 254',
  connecting: '0, 242, 254',
  listening: '0, 255, 100',
  speaking: '79, 172, 254',
  busy: '255, 180, 80',
  processing: '0, 242, 254',
  inference: '0, 242, 254',
  tool: '0, 242, 254',
  error: '255, 80, 80',
  ended: '79, 172, 254',
};

export const VOICE_STATUS_BASE_INTENSITY: Record<LocalVoiceStatus, number> = {
  idle: 0.2,
  connecting: 0.35,
  listening: 0.4,
  speaking: 0.4,
  busy: 0.35,
  processing: 0.35,
  inference: 0.35,
  tool: 0.35,
  error: 0.4,
  ended: 0.2,
};

export function computeEdgeGlowShadow(status: LocalVoiceStatus, glowValue: number): string {
  if (glowValue <= 0) return 'none';
  const rgb = VOICE_STATUS_GLOW_RGB[status] ?? '0, 242, 254';
  const base = VOICE_STATUS_BASE_INTENSITY[status] ?? 0.35;
  const intensity = (glowValue / 20) * base;
  const r = glowValue;

  // Soft, subtle ambient diffusion without harsh border lines
  const a1 = Math.min(1, 0.22 * intensity);
  const a2 = Math.min(1, 0.12 * intensity);
  const a3 = Math.min(1, 0.05 * intensity);

  return [
    `inset 0 0 ${Math.max(4, Math.round(r * 0.6))}px rgba(${rgb}, ${a1.toFixed(3)})`,
    `inset 0 0 ${Math.round(r * 1.8)}px ${Math.round(r * 0.1)}px rgba(${rgb}, ${a2.toFixed(3)})`,
    `inset 0 0 ${Math.round(r * 3.6)}px ${Math.round(r * 0.25)}px rgba(${rgb}, ${a3.toFixed(3)})`,
  ].join(', ');
}

const PANEL_STATUS: Record<LocalVoiceStatus, VoiceStatus> = {
  idle: 'idle', connecting: 'thinking', listening: 'listening', speaking: 'speaking',
  busy: 'thinking', error: 'idle', ended: 'idle',
  processing: 'thinking', inference: 'thinking', tool: 'thinking',
};

export function KioskPage() {
  const kioskDefaults = { ...VISUALIZER_DEFAULTS, petScale: 10 };
  const [settings, setSettings] = useState<VisualizerSettings>(() => {
    try {
      const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
      if (storage) {
        const raw =
          storage.getItem('openjarvis_kiosk_visualizer_settings') ||
          storage.getItem('openjarvis_visualizer_settings');
        if (raw) {
          const saved = JSON.parse(raw);
          return {
            style: 'screen',
            theme: isKioskTheme(saved.theme) ? saved.theme : kioskDefaults.theme,
            showCaptions: typeof saved.showCaptions === 'boolean' ? saved.showCaptions : kioskDefaults.showCaptions,
            showPet: typeof saved.showPet === 'boolean' ? saved.showPet : kioskDefaults.showPet,
            petScale: saved.petScale === VISUALIZER_DEFAULTS.petScale
              ? kioskDefaults.petScale : saved.petScale ?? kioskDefaults.petScale,
          };
        }
      }
    } catch {}
    return kioskDefaults;
  });

  const handleSettingsChange = useCallback((next: VisualizerSettings) => {
    const screenSettings: VisualizerSettings = { ...next, style: 'screen' };
    setSettings(screenSettings);
    try {
      const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
      if (storage) {
        storage.setItem('openjarvis_kiosk_visualizer_settings', JSON.stringify(screenSettings));
        storage.setItem('openjarvis_visualizer_settings', JSON.stringify(screenSettings));
      }
    } catch {}
  }, []);

  const addMessage = useAppStore((state) => state.addMessage);
  const threadIdRef = useRef<string>('');
  const remoteCamera = useRemoteCamera();
  const voice = usePipecatVoiceMode({
    onTurn: (message) => {
      if (threadIdRef.current) addMessage(threadIdRef.current, message);
    },
  });

  const edgeGlowShadow = useMemo(
    () => computeEdgeGlowShadow(voice.status, 20),
    [voice.status]
  );
  const { state: kioskState, micEnabled } = useKioskState();
  const browser = useSharedBrowser();
  const [screenRequested, setScreenRequested] = useState(true);
  const screenVisible = kioskState === 'active' && screenRequested;
  useEffect(() => {
    if (kioskState !== 'active') setScreenRequested(true);
  }, [kioskState]);
  const [voiceCollapsed, setVoiceCollapsed] = useState(false);
  const [browserMode, setBrowserMode] = useState<'split' | 'floating'>(() => {
    try {
      const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
      const saved = storage?.getItem('openjarvis_kiosk_browser_mode');
      if (saved === 'split' || saved === 'floating') return saved;
    } catch {}
    return 'split';
  });

  const handleSetBrowserMode = useCallback((nextMode: 'split' | 'floating') => {
    setBrowserMode(nextMode);
    try {
      const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
      storage?.setItem('openjarvis_kiosk_browser_mode', nextMode);
    } catch {}
  }, []);

  const [splitRatio, setSplitRatio] = useState<number>(() => {
    try {
      const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
      const saved = storage?.getItem('openjarvis_kiosk_split_ratio');
      if (saved) {
        const val = Number.parseFloat(saved);
        if (!Number.isNaN(val) && val >= 25 && val <= 85) return val;
      }
    } catch {}
    return 70;
  });


  const {
    position: floatingPos,
    size: floatingSize,
    isDragging: isFloatingDragging,
    isResizing: isFloatingResizing,
    activeHandle: floatingActiveHandle,
    isMaximized: isFloatingMaximized,
    toggleMaximize: toggleFloatingMaximize,
    zoom: zoomFloating,
    cardHandlers: floatingCardHandlers,
    getResizeHandleProps: getFloatingResizeHandleProps,
  } = useDraggableResizable({
    initialPlacement: 'center',
    aspectRatio: 16 / 10,
    minWidth: 360,
    enableWheelZoom: true,
  });

  const [isResizingSplit, setIsResizingSplit] = useState(false);
  const splitContainerRef = useRef<HTMLDivElement>(null);

  const handleSplitResizeStart = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizingSplit(true);

    const onPointerMove = (moveEvent: PointerEvent) => {
      const container = splitContainerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      if (rect.width <= 0) return;
      const rawX = moveEvent.clientX - rect.left;
      const pct = (rawX / rect.width) * 100;
      const clamped = Math.min(85, Math.max(25, Math.round(pct * 10) / 10));
      setSplitRatio(clamped);
      try {
        const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
        storage?.setItem('openjarvis_kiosk_split_ratio', clamped.toString());
      } catch {}
    };

    const onPointerUp = () => {
      setIsResizingSplit(false);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerUp);
    };

    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);
  }, []);


  const { language: uiLanguage, setLanguage: setUiLanguage } = useUiLanguage();
  const navigate = useNavigate();
  const createConversation = useAppStore((state) => state.createConversation);
  const selectedModel = useAppStore((state) => state.selectedModel);
  const modelsLoading = useAppStore((state) => state.modelsLoading);
  const setVoiceSessionActive = useAppStore((state) => state.setVoiceSessionActive);
  const voiceOwnerRef = useRef<KioskVoiceOwner | null>(null);
  const policyGrantConsumedRef = useRef(false);
  const presentationLifecycleRef = useRef<ReturnType<typeof createKioskPresentationLifecycle> | null>(null);
  if (presentationLifecycleRef.current === null) {
    presentationLifecycleRef.current = createKioskPresentationLifecycle();
  }
  const presentationLifecycle = presentationLifecycleRef.current;
  const resetPresentation = presentationLifecycle.requestReset;
  const isVoiceActive = !['idle', 'ended', 'error', 'busy'].includes(voice.status);

  useEffect(() => {
    setVoiceSessionActive(isVoiceActive);
    return () => setVoiceSessionActive(false);
  }, [isVoiceActive, setVoiceSessionActive]);

  const startPolicyVoice = useCallback(() => {
    if (modelsLoading || !selectedModel) return;
    const threadId = threadIdRef.current || createConversation(selectedModel);
    threadIdRef.current = threadId;
    voiceOwnerRef.current = 'policy';
    presentationLifecycle.markActive(threadId);
    void voice.start(threadId, selectedModel).catch(() => {});
  }, [createConversation, modelsLoading, presentationLifecycle, selectedModel, voice.start]);

  const endVoice = useCallback(() => {
    voiceOwnerRef.current = null;
    return presentationLifecycle.endVoiceThenReset(voice.end);
  }, [presentationLifecycle, voice.end]);

  const ensurePresentation = useCallback(() => {
    void ensurePresentationSession(window.location.origin, uiLanguage).then((sessionId) => {
      presentationLifecycle.setSessionId(sessionId);
    }).catch(() => {});
  }, [presentationLifecycle, uiLanguage]);

  const handleUiLanguageChange = useCallback((nextLanguage: UiLanguage) => {
    setUiLanguage(nextLanguage);
    void apiFetch('/api/kiosk/language', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language: nextLanguage }),
    }).catch(() => {});
  }, [setUiLanguage]);

  useEffect(() => {
    void apiFetch('/api/kiosk/language', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language: uiLanguage }),
    }).catch(() => {});
  }, [uiLanguage]);

  useEffect(() => {
    ensurePresentation();
  }, [ensurePresentation]);

  useEffect(() => {
    if (shouldEnsurePresentationSession(kioskState)) ensurePresentation();
  }, [ensurePresentation, kioskState]);

  useEffect(() => {
    const decision = kioskVoiceCommand({
      micEnabled,
      voiceEnabled: voice.enabled && !modelsLoading && selectedModel !== null,
      owner: voiceOwnerRef.current,
      grantConsumed: policyGrantConsumedRef.current,
    });
    policyGrantConsumedRef.current = decision.grantConsumed;

    if (decision.command === 'end') {
      void endVoice().catch(() => {});
    } else if (decision.command === 'start') {
      threadIdRef.current = '';
      startPolicyVoice();
    }
  }, [endVoice, micEnabled, modelsLoading, selectedModel, startPolicyVoice, voice.enabled]);

  useEffect(() => () => {
    if (voiceOwnerRef.current !== null) {
      void endVoice().catch(() => {});
    } else {
      resetPresentation();
    }
  }, [endVoice, resetPresentation]);

  const rows = currentVoiceTurnRows({ ...voice, assistantText: voice.assistantCaptionText });
  const speechRows = rows.filter((r) => r.role !== 'error');
  const activeSpeechRow = voice.status === 'speaking' || voice.assistantCaptionText
    ? (speechRows.find((r) => r.role === 'assistant') ?? speechRows[speechRows.length - 1])
    : speechRows[speechRows.length - 1];
  const voiceStatusShimmers = shouldShimmerVoiceStatus(voice.status);
  const [voicePane, setVoicePane] = useState<HTMLElement | null>(null);
  const [voicePaneSize, setVoicePaneSize] = useState<{ width: number; height: number }>();
  useLayoutEffect(() => {
    if (!voicePane) return;
    const measure = () => {
      const { width, height } = voicePane.getBoundingClientRect();
      if (width > 0 && height > 0) {
        setVoicePaneSize((prev) => prev?.width === width && prev.height === height
          ? prev : { width, height });
      }
    };
    measure();
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    observer?.observe(voicePane);
    window.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, [voicePane]);
  const splitPet = screenVisible && browserMode === 'split';
  const petBounds = voicePaneSize ?? {
    width: typeof window === 'undefined' ? 1920 : window.innerWidth * (splitPet ? (100 - splitRatio) / 100 : 1),
    height: typeof window === 'undefined' ? 1080 : window.innerHeight,
  };
  const voicePetScale = Math.max(0.5, Math.min(
    10, settings.petScale * (splitPet ? 0.5 : 1), splitPet ? 5 : 10,
    (petBounds.width - 32) / 55, (petBounds.height - 160) / 55,
  ));
  const voicePetPosition = useMemo(() => ({
    x: Math.max(16, Math.round((petBounds.width - 55 * voicePetScale) / 2)),
    y: Math.max(16, Math.round((petBounds.height - 55 * voicePetScale) / 2)),
  }), [petBounds.width, petBounds.height, voicePetScale]);

  const theme = KIOSK_THEMES[settings.theme] ?? KIOSK_THEMES['midnight-blue'];
  const lightBackground = theme.tone === 'light';
  const statusColor = lightBackground
    ? (voice.status === 'error' ? '#760c2c' : '#243b70')
    : STATUS_COLOR[voice.status];
  const voiceAssistantPane = (
    <aside
      ref={setVoicePane}
      aria-label="Voice assistant"
      data-kiosk-theme={settings.theme}
      data-pet-boundary
      className={
        !screenVisible || browserMode === 'floating'
          ? 'relative isolate h-full min-w-0 w-full shrink-0 overflow-hidden bg-[#071410]'
          : `relative isolate min-w-0 shrink-0 overflow-hidden bg-[#071410] ${
              voiceCollapsed ? 'h-14 md:h-full md:w-14' : 'w-full h-[35%] min-h-48 md:h-full md:w-[var(--voice-pane-width)]'
            }`
      }
      style={{
        '--pet-bubble-max-width': `${Math.max(24, Math.min(260, petBounds.width - 40))}px`,
        '--pet-bubble-max-height': `${Math.max(40, petBounds.height - 64)}px`,
        backgroundImage: theme.background,
        '--kiosk-background-size': `${Math.max(petBounds.width, petBounds.height)}px`,
        '--kiosk-foreground': lightBackground ? '#17243b' : '#f0f3fc',
        '--kiosk-muted': lightBackground ? '#243047' : '#dae2f4',
        '--kiosk-caption-muted': lightBackground ? '#243047' : '#dae2f4',
        '--kiosk-caption-secondary': lightBackground ? '#243047' : '#dae2f4',
        ...(screenVisible && browserMode === 'split' && !voiceCollapsed
          ? {
              '--voice-pane-width': `${100 - splitRatio}%`,
              flex: `0 0 ${100 - splitRatio}%`,
            }
          : {}),
      } as React.CSSProperties}
    >
      <div className={`relative h-full ${voiceCollapsed ? 'invisible' : ''}`}>
        <SpeechGlow status={voice.status} getAudioLevel={voice.getAudioLevel} lightBackground={lightBackground} />

        <VisualizerControls
          settings={settings}
          onSettingsChange={handleSettingsChange}
          status={PANEL_STATUS[voice.status]}
          uiLanguage={uiLanguage}
          onUiLanguageChange={handleUiLanguageChange}
        />

        {browserMode === 'floating' && (
          <div
            aria-hidden
            data-testid="kiosk-edge-glow"
            className="pointer-events-none absolute inset-0 z-30 transition-all duration-700"
            style={{ boxShadow: edgeGlowShadow }}
          />
        )}

        {settings.showPet && !voiceCollapsed && (
          <FloatingCodexPet
            initialPosition={voicePetPosition}
            scale={voicePetScale}
            bounds={petBounds}
            positioning="absolute"
            storageKey={
              browserMode === 'split'
                ? 'openjarvis_kiosk_pet_pos_split'
                : 'openjarvis_kiosk_pet_pos_center'
            }
            enableWandering={browserMode === 'floating'}
            voiceStatus={voice.status}
            activityDetail={voice.activityDetail}
            assistantCaptionText={voice.assistantCaptionText}
          />
        )}



        <button
          onClick={() => navigate('/')}
          title="Exit kiosk"
          className="absolute top-4 right-4 z-30 w-9 h-9 flex items-center justify-center cursor-pointer transition-all hover:scale-110 opacity-70 hover:opacity-100"
          style={{
            background: 'none',
            border: 'none',
            boxShadow: 'none',
            color: 'var(--kiosk-muted)',
          }}
        >
          <X size={20} />
        </button>

        <div className="absolute inset-x-0 bottom-0 z-40 flex flex-col items-center gap-3 px-4 pb-4 pointer-events-none">
          {(kioskState === 'active' || isVoiceActive) && (
            <>
              {settings.showCaptions && (
                <div className="w-full max-w-2xl flex flex-col items-center justify-end text-center min-h-[64px] mb-1">
                  {rows.filter((r) => r.role === 'error').map((err) => (
                    <p
                      key="error"
                      className="text-xs text-red-400 bg-red-950/60 border border-red-500/30 px-3 py-1 rounded-full mb-2"
                    >
                      {err.text}
                    </p>
                  ))}
                  {activeSpeechRow && (
                    <StackCaption
                      key={activeSpeechRow.role}
                      text={activeSpeechRow.text}
                      role={activeSpeechRow.role}
                      isFading={activeSpeechRow.role === 'assistant' && voice.assistantCaptionFading}
                    />
                  )}
                </div>
              )}
              <div
                className="text-[13px] font-medium transition-all duration-500"
                style={{ color: statusColor }}
              >
                <span
                  className={voiceStatusShimmers ? 'text-shimmer' : undefined}
                  style={voiceStatusShimmers ? {
                    '--shimmer-base': statusColor,
                    '--shimmer-highlight': 'var(--kiosk-foreground)',
                  } as React.CSSProperties : undefined}
                >
                  {voiceStatusLabel(uiLanguage, voice.status, voice.activityDetail)}
                </span>
              </div>
            </>
          )}
          <div className="pointer-events-auto flex items-center gap-2">
            <VoiceWaveform
              speaking={voice.status === 'speaking' && !voiceCollapsed}
              listening={voice.status === 'listening' && !voiceCollapsed}
              voiceStatus={voiceCollapsed ? 'idle' : voice.status}
              getFrequencyData={voice.getFrequencyData}
              onMicClick={isVoiceActive ? endVoice : kioskState === 'active' ? startPolicyVoice : undefined}
            />
            <RemoteCameraButton
              enabled={remoteCamera.enabled}
              active={remoteCamera.active}
              error={remoteCamera.error}
              language={uiLanguage}
              onToggle={remoteCamera.toggle}
            />
            <ScreensMonitorButton
              visible={screenVisible}
              disabled={kioskState !== 'active'}
              language={uiLanguage}
              onToggle={() => setScreenRequested((visible) => !visible)}
            />
          </div>
        </div>
      </div>
    </aside>
  );

  return (
    <div
      data-testid="kiosk-shared-layout"
      className="relative flex h-full min-h-0 flex-1 flex-col overflow-hidden md:flex-row"
      style={{ background: 'var(--color-bg)', color: 'var(--color-text)' }}
    >
      {/* Global overlay during split drag to guarantee pointer tracking and correct cursor */}
      {isResizingSplit && (
        <div className="fixed inset-0 z-50 cursor-col-resize select-none pointer-events-auto" />
      )}

      {browserMode === 'split' ? (
        <div
          ref={splitContainerRef}
          className="relative isolate flex h-full w-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-[#08100f] md:flex-row"
        >
          {/* Customer display and its browser controls share the outer frame. */}
          <div
            id="kiosk-shared-screen"
            hidden={!screenVisible}
            data-testid="customer-display-pane-container"
            className="flex w-full min-h-0 min-w-0 flex-col overflow-hidden md:w-[var(--customer-pane-width)]"
            style={{
              display: screenVisible ? undefined : 'none',
              '--customer-pane-width': voiceCollapsed ? 'calc(100% - 3.5rem)' : `${splitRatio}%`,
              flex: voiceCollapsed ? '1 1 auto' : `0 0 ${splitRatio}%`,
            } as React.CSSProperties}
          >
            <SharedBrowserPane
              browser={browser}
              isFloating={false}
              onToggleFloating={() => handleSetBrowserMode('floating')}
            />
          </div>

          {/* Invisible interactive resize handle at the seam — no UI changes, pure UX */}
          {screenVisible && !voiceCollapsed && (
            <div
              data-testid="kiosk-pane-resizer"
              aria-label="Resize split panes"
              onPointerDown={handleSplitResizeStart}
              onDoubleClick={() => {
                setSplitRatio(70);
                try {
                  const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
                  storage?.setItem('openjarvis_kiosk_split_ratio', '70');
                } catch {}
              }}
              className="absolute top-0 bottom-0 z-40 hidden md:block w-4 -translate-x-1/2 cursor-col-resize select-none touch-none bg-transparent"
              style={{ left: `${splitRatio}%` }}
            />
          )}

          {/* Voice status, captions, mascot, and microphone stay in this pane. */}
          {voiceAssistantPane}
          <div
            aria-hidden
            data-testid="kiosk-edge-glow"
            className="pointer-events-none absolute inset-0 z-30 transition-all duration-700"
            style={{ boxShadow: edgeGlowShadow }}
          />
        </div>
      ) : (
        <>
          {/* Global overlay during drag or resize to guarantee pointer tracking and correct cursor */}
          {screenVisible && (isFloatingDragging || isFloatingResizing) && (
            <div
              className={`fixed inset-0 z-50 select-none ${
                isFloatingDragging
                  ? 'cursor-grabbing'
                  : floatingActiveHandle === 'n' || floatingActiveHandle === 's'
                    ? 'cursor-ns-resize'
                    : floatingActiveHandle === 'w' || floatingActiveHandle === 'e'
                      ? 'cursor-ew-resize'
                      : floatingActiveHandle === 'nw' || floatingActiveHandle === 'se'
                        ? 'cursor-nwse-resize'
                        : 'cursor-nesw-resize'
              }`}
            />
          )}

          {/* Floating Mode: Voice Assistant takes full background */}
          {voiceAssistantPane}

          {/* Floating Card: Customer Display */}
          <div
            id="kiosk-shared-screen"
            hidden={!screenVisible}
            data-testid="floating-browser-container"
            data-maximized={isFloatingMaximized ? 'true' : 'false'}
            className={`fixed z-30 select-none touch-none transition-[box-shadow] ${
              isFloatingMaximized
                ? 'inset-0 rounded-none'
                : 'rounded-2xl'
            } ${
              isFloatingDragging
                ? 'shadow-[0_24px_60px_rgba(0,0,0,0.85)] ring-1 ring-white/25'
                : isFloatingResizing
                  ? 'shadow-[0_20px_50px_rgba(0,0,0,0.8)] ring-1 ring-cyan-400/50'
                  : 'shadow-[0_12px_36px_rgba(0,0,0,0.65)] hover:ring-1 hover:ring-white/20'
            }`}
            style={{
              display: screenVisible ? undefined : 'none',
              ...(isFloatingMaximized
                ? { left: 0, top: 0, width: '100vw', height: '100vh' }
                : {
                    left: `${floatingPos.x}px`,
                    top: `${floatingPos.y}px`,
                    width: `${floatingSize.width}px`,
                    height: `${floatingSize.height}px`,
                  }),
            }}
          >
            {/* Inner content box with overflow-hidden and rounded corners */}
            <div
              className={`relative flex h-full w-full flex-col overflow-hidden bg-[#1d1f22] ${
                isFloatingMaximized
                  ? 'rounded-none border-0'
                  : 'rounded-2xl border border-white/15'
              }`}
            >
              <SharedBrowserPane
                browser={browser}
                isFloating={true}
                isMaximized={isFloatingMaximized}
                onToggleFloating={() => handleSetBrowserMode('split')}
                onToggleMaximize={toggleFloatingMaximize}
                dragHandleProps={floatingCardHandlers}
              />
            </div>

            {/* 8 Resize Handles on the outer container (never clipped!) */}
            {!isFloatingMaximized && (
              <>
                {/* 4 Edge Handles */}
                <div {...getFloatingResizeHandleProps('n')} title="Resize Top" className="absolute -top-2 inset-x-6 h-4 cursor-ns-resize touch-none z-40" />
                <div {...getFloatingResizeHandleProps('s')} title="Resize Bottom" className="absolute -bottom-2 inset-x-6 h-4 cursor-ns-resize touch-none z-40" />
                <div {...getFloatingResizeHandleProps('w')} title="Resize Left" className="absolute -left-2 inset-y-6 w-4 cursor-ew-resize touch-none z-40" />
                <div {...getFloatingResizeHandleProps('e')} title="Resize Right" className="absolute -right-2 inset-y-6 w-4 cursor-ew-resize touch-none z-40" />

                {/* 4 Corner Handles with Visual Accents */}
                <div {...getFloatingResizeHandleProps('nw')} title="Resize Top-Left" className="group/nw absolute -top-2.5 -left-2.5 w-7 h-7 cursor-nwse-resize touch-none z-50 flex items-start justify-start p-1">
                  <div className="w-3 h-3 border-t-2 border-l-2 border-cyan-400 rounded-tl-sm group-hover/nw:scale-125 transition-transform" />
                </div>
                <div {...getFloatingResizeHandleProps('ne')} title="Resize Top-Right" className="group/ne absolute -top-2.5 -right-2.5 w-7 h-7 cursor-nesw-resize touch-none z-50 flex items-start justify-end p-1">
                  <div className="w-3 h-3 border-t-2 border-r-2 border-cyan-400 rounded-tr-sm group-hover/ne:scale-125 transition-transform" />
                </div>
                <div {...getFloatingResizeHandleProps('sw')} title="Resize Bottom-Left" className="group/sw absolute -bottom-2.5 -left-2.5 w-7 h-7 cursor-nesw-resize touch-none z-50 flex items-end justify-start p-1">
                  <div className="w-3 h-3 border-b-2 border-l-2 border-cyan-400 rounded-bl-sm group-hover/sw:scale-125 transition-transform" />
                </div>
                <div {...getFloatingResizeHandleProps('se')} title="Resize Bottom-Right" className="group/se absolute -bottom-2.5 -right-2.5 w-7 h-7 cursor-nwse-resize touch-none z-50 flex items-end justify-end p-1">
                  <div className="w-3 h-3 border-b-2 border-r-2 border-cyan-400 rounded-br-sm group-hover/se:scale-125 transition-transform" />
                </div>
              </>
            )}
          </div>
        </>
      )}

    </div>
  );
}
