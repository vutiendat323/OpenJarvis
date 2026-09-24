import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { KioskPage, computeEdgeGlowShadow } from './KioskPage';
import type { LocalVoiceStatus } from '@/hooks/voiceStatus';

const storageMap = new Map<string, string>();
let mockUiLanguage = 'vi';

let mockVoiceState = {
  enabled: true,
  status: 'idle' as LocalVoiceStatus,
  activityDetail: null as string | null,
  assistantCaptionText: '',
  getFrequencyData: () => new Uint8Array(),
  start: vi.fn().mockResolvedValue(undefined),
  end: vi.fn().mockResolvedValue(undefined),
};


const mockCreateConversation = vi.fn(() => 'thread-123');
const mockAddMessage = vi.fn();
const mockSetVoiceSessionActive = vi.fn();

const mockLifecycle = {
  requestReset: vi.fn(),
  setSessionId: vi.fn(),
  endVoiceThenReset: vi.fn().mockResolvedValue(undefined),
  markActive: vi.fn(),
};


beforeEach(() => {
  storageMap.clear();
  globalThis.localStorage = {
    getItem: (k: string) => storageMap.get(k) ?? null,
    setItem: (k: string, v: string) => { storageMap.set(k, String(v)); },
    removeItem: (k: string) => { storageMap.delete(k); },
    clear: () => { storageMap.clear(); },
    key: (i: number) => Array.from(storageMap.keys())[i] ?? null,
    length: storageMap.size,
  } as unknown as Storage;


  mockVoiceState = {
    enabled: true,
    status: 'idle',
    activityDetail: null,
    assistantCaptionText: '',
    getFrequencyData: () => new Uint8Array(),
    start: vi.fn().mockResolvedValue(undefined),
    end: vi.fn().mockResolvedValue(undefined),
  };

  mockCreateConversation.mockClear();
  mockLifecycle.markActive.mockClear();
  mockLifecycle.endVoiceThenReset.mockClear();
});

vi.mock('@/hooks/useKioskState', () => ({
  useKioskState: () => ({ state: 'prompting', micEnabled: false, respond: vi.fn() }),
}));
vi.mock('@/hooks/usePipecatVoiceMode', () => ({
  usePipecatVoiceMode: () => mockVoiceState,
}));
vi.mock('@/hooks/useSharedBrowser', () => ({
  useSharedBrowser: () => ({ status: 'connected', url: 'about:blank', send: vi.fn() }),
}));
vi.mock('@/hooks/useUiLanguage', () => ({
  useUiLanguage: () => ({ language: mockUiLanguage, setLanguage: (l: string) => { mockUiLanguage = l; } }),
}));
vi.mock('@/lib/store', () => ({
  useAppStore: (selector: (state: Record<string, unknown>) => unknown) => selector({
    addMessage: mockAddMessage,
    createConversation: mockCreateConversation,
    selectedModel: 'gpt-5.6-luna',
    modelsLoading: false,
    setVoiceSessionActive: mockSetVoiceSessionActive,
  }),
}));
vi.mock('@/lib/kioskPresentation', () => ({
  createKioskPresentationLifecycle: () => mockLifecycle,
  ensurePresentationSession: vi.fn(),
  shouldEnsurePresentationSession: vi.fn(() => false),
}));
vi.mock('@/components/Visualizer/AudioVisualizer', () => ({
  AudioVisualizer: () => <div data-testid="mock-audio-visualizer" />,
}));
vi.mock('@/components/Visualizer/VisualizerControls', () => ({ VisualizerControls: () => null }));
vi.mock('@/components/Kiosk/Pet/FloatingCodexPet', () => ({
  FloatingCodexPet: (props: { scale?: number }) => (
    <div data-testid="mock-floating-codex-pet" data-scale={props.scale} />
  ),
}));
vi.mock('@/components/Chat/voiceTurnRows', () => ({ currentVoiceTurnRows: () => [] }));

vi.mock('@/components/Kiosk/SharedBrowserPane', () => ({
  SharedBrowserPane: () => <section data-testid="shared-browser-pane" />,
}));

describe('KioskPage', () => {
  it('renders consent actions in Vietnamese when uiLanguage is vi', () => {
    mockUiLanguage = 'vi';
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <KioskPage />
      </MemoryRouter>,
    );

    expect(markup).toContain('data-testid="kiosk-prompting-overlay"');
    expect(markup).toContain('Sẵn sàng trò chuyện?');
    expect(markup).toContain('Bắt đầu trò chuyện');
    expect(markup).toContain('Không phải bây giờ');
    expect(markup).toContain('Cho phép trợ lý bật microphone để nhận yêu cầu của bạn.');
    expect(markup).not.toContain('Ready to chat?');
    expect(markup).not.toContain('Start chatting');
  });

  it('renders consent actions in English when uiLanguage is en', () => {
    mockUiLanguage = 'en';
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <KioskPage />
      </MemoryRouter>,
    );

    expect(markup).toContain('data-testid="kiosk-prompting-overlay"');
    expect(markup).toContain('Ready to chat?');
    expect(markup).toContain('Start chatting');
    expect(markup).toContain('Not now');
    expect(markup).toContain('Allow the assistant to enable the microphone to hear your requests.');
    expect(markup).not.toContain('Sẵn sàng trò chuyện?');
    expect(markup).not.toContain('Bắt đầu trò chuyện');
  });

  it('hides the consent prompt while a voice session is active', () => {
    mockUiLanguage = 'vi';
    mockVoiceState.status = 'connecting';

    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <KioskPage />
      </MemoryRouter>,
    );

    expect(markup).not.toContain('data-testid="kiosk-prompting-overlay"');
    expect(markup).not.toContain('Sẵn sàng trò chuyện?');
    expect(markup).not.toContain('Bắt đầu trò chuyện');
  });

  it('renders 4-edge border glow and center ambient glow layers', () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <KioskPage />
      </MemoryRouter>,
    );

    expect(markup).toContain('data-testid="kiosk-edge-glow"');
    expect(markup).toContain('data-testid="kiosk-center-glow"');
  });

  it('renders mascot pet with default petScale when showPet is true', () => {
    localStorage.removeItem('openjarvis_kiosk_visualizer_settings');
    localStorage.removeItem('openjarvis_visualizer_settings');

    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <KioskPage />
      </MemoryRouter>,
    );

    expect(markup).toContain('data-testid="mock-floating-codex-pet"');
    expect(markup).toContain('data-scale="2.5"');
  });

  it('hides mascot pet when showPet is false in localStorage', () => {
    localStorage.setItem(
      'openjarvis_kiosk_visualizer_settings',
      JSON.stringify({ showPet: false, petScale: 3.0 }),
    );

    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <KioskPage />
      </MemoryRouter>,
    );

    expect(markup).not.toContain('data-testid="mock-floating-codex-pet"');
    localStorage.removeItem('openjarvis_kiosk_visualizer_settings');
  });

  it('supports legacy openjarvis_visualizer_settings key with custom petScale', () => {
    localStorage.removeItem('openjarvis_kiosk_visualizer_settings');
    localStorage.setItem(
      'openjarvis_visualizer_settings',
      JSON.stringify({ showPet: true, petScale: 3.5 }),
    );

    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <KioskPage />
      </MemoryRouter>,
    );

    expect(markup).toContain('data-testid="mock-floating-codex-pet"');
    expect(markup).toContain('data-scale="3.5"');
    localStorage.removeItem('openjarvis_visualizer_settings');
  });

  it('renders a shared browser beside policy voice without screen capture controls', () => {
    const markup = renderToStaticMarkup(<MemoryRouter><KioskPage /></MemoryRouter>);
    expect(markup).toContain('data-testid="kiosk-shared-layout"');
    expect(markup).toContain('data-testid="shared-browser-pane"');
    expect(markup).toContain('aria-label="Voice assistant"');
    expect(markup).toContain('Collapse voice pane');
    expect(markup).not.toContain('Share Screen');
    expect(markup).not.toContain('hero-talk-btn');
    expect(markup).not.toContain('dock-mic-btn');
  });

  it('renders 7:3 split layout with floating rounded customer display on the left and seamless voice assistant on the right', () => {
    const markup = renderToStaticMarkup(<MemoryRouter><KioskPage /></MemoryRouter>);
    expect(markup).toContain('data-testid="customer-display-pane-container"');
    expect(markup).toContain('width:70%');
    expect(markup).toContain('border-radius:16px');
    // Divider is removed for a seamless floating feel
    expect(markup).not.toContain('role="separator"');

    // Confirm left pane (customer display) appears before right pane (voice assistant)
    const customerDisplayIndex = markup.indexOf('data-testid="customer-display-pane-container"');
    const voiceAssistantIndex = markup.indexOf('aria-label="Voice assistant"');

    expect(customerDisplayIndex).toBeGreaterThan(-1);
    expect(voiceAssistantIndex).toBeGreaterThan(customerDisplayIndex);
  });

  it('renders floating mode with container and all 8 resize handles when browserMode is floating in localStorage', () => {
    localStorage.setItem('openjarvis_kiosk_browser_mode', 'floating');
    const markup = renderToStaticMarkup(<MemoryRouter><KioskPage /></MemoryRouter>);

    expect(markup).toContain('data-testid="floating-browser-container"');
    expect(markup).toContain('title="Resize Top"');
    expect(markup).toContain('title="Resize Bottom"');
    expect(markup).toContain('title="Resize Left"');
    expect(markup).toContain('title="Resize Right"');
    expect(markup).toContain('title="Resize Top-Left"');
    expect(markup).toContain('title="Resize Top-Right"');
    expect(markup).toContain('title="Resize Bottom-Left"');
    expect(markup).toContain('title="Resize Bottom-Right"');
    expect(markup).toContain('data-testid="shared-browser-pane"');
    localStorage.removeItem('openjarvis_kiosk_browser_mode');
  });

  describe('computeEdgeGlowShadow', () => {
    it('returns "none" when glowValue is 0 or negative', () => {
      expect(computeEdgeGlowShadow('speaking', 0)).toBe('none');
      expect(computeEdgeGlowShadow('listening', -5)).toBe('none');
    });

    it('contains the correct RGB color for speaking (blue) and listening (green)', () => {
      const speakingShadow = computeEdgeGlowShadow('speaking', 20);
      expect(speakingShadow).toContain('rgba(79, 172, 254');
      // No harsh solid 1px border line
      expect(speakingShadow).not.toContain('inset 0 0 0 1px');

      const listeningShadow = computeEdgeGlowShadow('listening', 20);
      expect(listeningShadow).toContain('rgba(0, 255, 100');

      const errorShadow = computeEdgeGlowShadow('error', 20);
      expect(errorShadow).toContain('rgba(255, 80, 80');
    });

    it('scales spread and intensity with glow slider value', () => {
      const defaultGlow = computeEdgeGlowShadow('speaking', 20);
      const highGlow = computeEdgeGlowShadow('speaking', 50);

      expect(defaultGlow).toContain('inset 0 0 12px');
      expect(highGlow).toContain('inset 0 0 30px');
    });
  });
});
