import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { X, PanelLeftClose, PanelLeftOpen } from 'lucide-react';

import { AudioVisualizer } from '@/components/Visualizer/AudioVisualizer';
import { VisualizerControls } from '@/components/Visualizer/VisualizerControls';
import { FloatingCodexPet } from '@/components/Kiosk/Pet/FloatingCodexPet';
import { SharedBrowserPane } from '@/components/Kiosk/SharedBrowserPane';
import { VoiceWaveform } from '@/components/Kiosk/VoiceWaveform';
import { currentVoiceTurnRows } from '@/components/Chat/voiceTurnRows';
import { useDraggableResizable } from '@/hooks/useDraggableResizable';
import { useKioskState, type KioskState } from '@/hooks/useKioskState';
import { usePipecatVoiceMode } from '@/hooks/usePipecatVoiceMode';
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

const GLOW: Record<LocalVoiceStatus, string> = {
  idle: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(0,242,254,0.04) 0%, transparent 70%)',
  connecting: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(0,242,254,0.08) 0%, transparent 70%)',
  listening: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(0,255,100,0.08) 0%, transparent 70%)',
  speaking: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(79,172,254,0.10) 0%, transparent 70%)',
  busy: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(255,180,80,0.08) 0%, transparent 70%)',
  processing: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(0,242,254,0.08) 0%, transparent 70%)',
  inference: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(0,242,254,0.08) 0%, transparent 70%)',
  tool: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(0,242,254,0.08) 0%, transparent 70%)',
  error: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(255,80,80,0.10) 0%, transparent 70%)',
  ended: 'radial-gradient(ellipse 56% 56% at 50% 50%, rgba(0,242,254,0.04) 0%, transparent 70%)',
};

const STATUS_COLOR: Record<LocalVoiceStatus, string> = {
  idle: 'var(--color-text-secondary)',
  connecting: 'var(--color-accent)',
  listening: 'var(--color-success)',
  speaking: 'var(--color-accent)',
  busy: 'var(--color-warning)',
  processing: 'var(--color-accent)',
  inference: 'var(--color-accent)',
  tool: 'var(--color-accent)',
  error: 'var(--color-error)',
  ended: 'var(--color-text-secondary)',
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
  const [settings, setSettings] = useState<VisualizerSettings>(() => {
    try {
      const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
      if (storage) {
        const raw =
          storage.getItem('openjarvis_kiosk_visualizer_settings') ||
          storage.getItem('openjarvis_visualizer_settings');
        if (raw) return { ...VISUALIZER_DEFAULTS, ...JSON.parse(raw) };
      }
    } catch {}
    return VISUALIZER_DEFAULTS;
  });

  const handleSettingsChange = useCallback((next: VisualizerSettings) => {
    setSettings(next);
    try {
      const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
      if (storage) {
        storage.setItem('openjarvis_kiosk_visualizer_settings', JSON.stringify(next));
        storage.setItem('openjarvis_visualizer_settings', JSON.stringify(next));
      }
    } catch {}
  }, []);

  const addMessage = useAppStore((state) => state.addMessage);
  const threadIdRef = useRef<string>('');
  const voice = usePipecatVoiceMode({
    onTurn: (message) => {
      if (threadIdRef.current) addMessage(threadIdRef.current, message);
    },
  });

  const edgeGlowShadow = useMemo(
    () => computeEdgeGlowShadow(voice.status, settings.glow),
    [voice.status, settings.glow]
  );
  const { state: kioskState, micEnabled, respond } = useKioskState();
  const browser = useSharedBrowser();
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

  const handleZoom = useCallback((direction: number) => {
    if (browserMode === 'floating') {
      zoomFloating(direction * 40);
    } else {
      setSplitRatio((prev) => {
        const next = Math.min(85, Math.max(25, Math.round((prev + direction * 5) * 10) / 10));
        try {
          const storage = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage;
          storage?.setItem('openjarvis_kiosk_split_ratio', next.toString());
        } catch {}
        return next;
      });
    }
  }, [browserMode, zoomFloating]);


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
  const voiceStatusShimmers = shouldShimmerVoiceStatus(voice.status);
  const voicePetPosition = useMemo(() => {
    if (typeof window === 'undefined') return undefined;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const horizontalSplit = browserMode === 'split' && viewportWidth >= 768;
    const verticalSplit = browserMode === 'split' && viewportWidth < 768;
    const petSize = Math.round(110 * (settings.petScale / 2));
    const paneStartX = horizontalSplit ? viewportWidth * splitRatio / 100 : 0;
    const paneWidth = horizontalSplit ? viewportWidth - paneStartX : viewportWidth;
    const paneStartY = verticalSplit ? viewportHeight * 0.65 : 0;
    const paneHeight = verticalSplit ? viewportHeight * 0.35 : viewportHeight;
    return {
      x: Math.round(paneStartX + (paneWidth - petSize) / 2),
      y: Math.round(paneStartY + (paneHeight - petSize) / 2),
    };
  }, [browserMode, settings.petScale, splitRatio]);

  const voiceAssistantPane = (
    <aside
      aria-label="Voice assistant"
      className={
        browserMode === 'floating'
          ? 'relative h-full w-full shrink-0 overflow-hidden bg-[#071410]'
          : `relative shrink-0 overflow-hidden border-t border-white/10 bg-[#071410] md:border-l md:border-t-0 ${
              voiceCollapsed ? 'h-14 md:h-full md:w-14' : 'h-[35%] min-h-48 md:h-full'
            }`
      }
      style={{
        background: 'linear-gradient(155deg, #0b1e19 0%, #071410 58%, #06110f 100%)',
        ...(browserMode === 'split' && !voiceCollapsed
          ? {
              width: `${100 - splitRatio}%`,
              flex: `0 0 ${100 - splitRatio}%`,
            }
          : {}),
      }}
    >
      <button
        aria-label={voiceCollapsed ? 'Expand voice pane' : 'Collapse voice pane'}
        onClick={() => setVoiceCollapsed(!voiceCollapsed)}
        className="absolute top-4 left-4 z-40 cursor-pointer text-[var(--color-text-secondary)] hover:text-[var(--color-text)] transition-colors"
      >
        {voiceCollapsed ? <PanelLeftOpen size={20} /> : <PanelLeftClose size={20} />}
      </button>
      <div className={`relative h-full ${voiceCollapsed ? 'invisible' : ''}`}>

        {/* Center ambient glow - intensity dynamically tuned by settings.glow */}
        <div
          aria-hidden
          data-testid="kiosk-center-glow"
          className="absolute inset-0 pointer-events-none transition-all duration-1000"
          style={{
            background: GLOW[voice.status],
            opacity: settings.glow > 0 ? Math.min(2.0, settings.glow / 20) : 0,
            zIndex: 0,
          }}
        />

        {settings.style === '3d' && !voiceCollapsed && <AudioVisualizer getFrequencyData={voice.getFrequencyData} settings={settings} />}
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

        {settings.showPet && (
          <FloatingCodexPet
            initialPosition={voicePetPosition}
            scale={settings.petScale}
            storageKey="openjarvis_kiosk_pet_pos_center"
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
            color: 'var(--color-text-secondary)',
          }}
        >
          <X size={20} />
        </button>

        <div className="absolute inset-x-0 bottom-0 z-20 flex flex-col items-center gap-3 px-4 pb-4 pointer-events-none">
          {kioskState === 'active' && (
            <>
              {settings.showCaptions && (
                <div className="w-full max-w-2xl flex flex-col items-center gap-2 rounded-2xl bg-black/20 px-4 py-2 text-center backdrop-blur-sm">
                  {rows.map((row) => (
                    <p
                      key={row.role}
                      className="text-sm leading-snug text-white/85"
                    >
                      {row.text}
                    </p>
                  ))}
                </div>
              )}
              <div
                className="text-[13px] font-medium transition-all duration-500"
                style={{ color: STATUS_COLOR[voice.status] }}
              >
                <span
                  className={voiceStatusShimmers ? 'text-shimmer' : undefined}
                  style={voiceStatusShimmers ? {
                    '--shimmer-base': STATUS_COLOR[voice.status],
                    '--shimmer-highlight': 'var(--color-text)',
                  } as React.CSSProperties : undefined}
                >
                  {voiceStatusLabel(uiLanguage, voice.status, voice.activityDetail)}
                </span>
              </div>
            </>
          )}
          <div className="pointer-events-auto">
            <VoiceWaveform
              speaking={voice.status === 'speaking' && !voiceCollapsed}
              voiceStatus={voiceCollapsed ? 'idle' : voice.status}
              getFrequencyData={voice.getFrequencyData}
              onMicClick={isVoiceActive ? endVoice : startPolicyVoice}
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
      {browserMode === 'split' ? (
        <div className="flex h-full w-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden p-2 md:p-2.5">
          {/* One shared frame keeps the customer display and voice assistant together. */}
          <div
            className="relative isolate flex h-full w-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[16px] border border-white/10 bg-[#08100f] shadow-2xl shadow-black/45 md:flex-row"
            style={{ borderRadius: '16px' }}
          >
            {/* Customer display and its browser controls share the outer frame. */}
            <div
              data-testid="customer-display-pane-container"
              className="flex min-h-0 min-w-0 flex-col overflow-hidden"
              style={{
                width: voiceCollapsed ? 'calc(100% - 3.5rem)' : `${splitRatio}%`,
                flex: voiceCollapsed ? '1 1 auto' : `0 0 ${splitRatio}%`,
              }}
            >
              <SharedBrowserPane
                browser={browser}
                isFloating={false}
                onToggleFloating={() => handleSetBrowserMode('floating')}
                onZoom={handleZoom}
              />
            </div>

            {/* Voice status, captions, mascot, and microphone stay in this pane. */}
            {voiceAssistantPane}
            <div
              aria-hidden
              data-testid="kiosk-edge-glow"
              className="pointer-events-none absolute inset-0 z-30 rounded-[inherit] transition-all duration-700"
              style={{ boxShadow: edgeGlowShadow }}
            />
          </div>
        </div>
      ) : (
        <>
          {/* Global overlay during drag or resize to guarantee pointer tracking and correct cursor */}
          {(isFloatingDragging || isFloatingResizing) && (
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
            style={
              isFloatingMaximized
                ? { left: 0, top: 0, width: '100vw', height: '100vh' }
                : {
                    left: `${floatingPos.x}px`,
                    top: `${floatingPos.y}px`,
                    width: `${floatingSize.width}px`,
                    height: `${floatingSize.height}px`,
                  }
            }
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
                onZoom={handleZoom}
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

      {/* Global Prompting Modal: Centered over the entire Kiosk across both panes */}
      {kioskState === 'prompting' && !isVoiceActive && (
        <div
          data-testid="kiosk-prompting-overlay"
          className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md"
        >
          <div
            className="mx-4 max-w-sm rounded-2xl p-8 text-center shadow-2xl"
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
          >
            <div className="mb-4 text-4xl">🤖</div>
            <h2 className="mb-2 text-xl font-semibold" style={{ color: 'var(--color-text)' }}>
              {uiLanguage === 'vi' ? 'Sẵn sàng trò chuyện?' : 'Ready to chat?'}
            </h2>
            <p className="mb-6 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
              {uiLanguage === 'vi'
                ? 'Cho phép trợ lý bật microphone để nhận yêu cầu của bạn.'
                : 'Allow the assistant to enable the microphone to hear your requests.'}
            </p>
            <div className="flex justify-center gap-3">
              <button
                onClick={() => void respond(true)}
                className="cursor-pointer rounded-xl bg-[var(--color-accent)] px-6 py-2.5 text-sm font-semibold text-white transition-transform hover:scale-105"
              >
                {uiLanguage === 'vi' ? 'Bắt đầu trò chuyện' : 'Start chatting'}
              </button>
              <button
                onClick={() => void respond(false)}
                className="cursor-pointer rounded-xl px-6 py-2.5 text-sm font-semibold transition-transform hover:scale-105"
                style={{
                  background: 'var(--color-bg-secondary)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text)',
                }}
              >
                {uiLanguage === 'vi' ? 'Không phải bây giờ' : 'Not now'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
