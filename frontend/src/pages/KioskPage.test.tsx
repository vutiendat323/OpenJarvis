// @vitest-environment jsdom

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { KioskState } from '@/hooks/useKioskState';

import { KioskPage, computeEdgeGlowShadow } from './KioskPage';
import type { LocalVoiceStatus } from '@/hooks/voiceStatus';
import { voiceStatusLabel } from '@/hooks/voiceUiText';

const storageMap = new Map<string, string>();
let mockUiLanguage = 'vi';
let mockKioskState: KioskState = 'prompting';

let mockVoiceState = {
  enabled: true,
  status: 'idle' as LocalVoiceStatus,
  activityDetail: null as string | null,
  transcript: '',
  error: null as string | null,
  assistantCaptionText: '',
  getFrequencyData: () => new Uint8Array(),
  getAudioLevel: () => 0,
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
  mockKioskState = 'prompting';
  mockUiLanguage = 'vi';
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
    transcript: '',
    error: null,
    assistantCaptionText: '',
    getFrequencyData: () => new Uint8Array(),
    getAudioLevel: () => 0,
    start: vi.fn().mockResolvedValue(undefined),
    end: vi.fn().mockResolvedValue(undefined),
  };

  mockCreateConversation.mockClear();
  mockLifecycle.markActive.mockClear();
  mockLifecycle.endVoiceThenReset.mockClear();
});

afterEach(cleanup);

it.each([
  ['listening', 'pastel-grid'],
  ['speaking', 'midnight-blue'],
  ['speaking', 'purple-glow'],
] as const)('connects %s audio to the saved %s background without a synthetic silence pulse', (status, theme) => {
  let volume = 1;
  let callbacks: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callbacks.push(callback);
    return callbacks.length;
  });
  vi.stubGlobal('cancelAnimationFrame', () => {});
  try {
    mockVoiceState.status = status;
    mockVoiceState.getAudioLevel = () => volume;
    storageMap.set('openjarvis_kiosk_visualizer_settings', JSON.stringify({ theme }));
    const { getByTestId, getByLabelText } = render(<MemoryRouter><KioskPage /></MemoryRouter>);
    act(() => {
      const pending = callbacks;
      callbacks = [];
      pending.forEach((callback) => callback(40));
    });
    expect(getByLabelText('Voice assistant').getAttribute('data-kiosk-theme')).toBe(theme);
    expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBeGreaterThan(0);
    if (theme === 'purple-glow') {
      const pane = getByLabelText('Voice assistant');
      const glow = getByTestId('kiosk-center-glow');
      const alpha = Number(glow.style.opacity);
      const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      const luminance = (channels: number[]) => channels.map((value) => {
        const v = value / 255;
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      }).reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
      for (const foreground of ['--kiosk-caption-muted', '--kiosk-caption-secondary']) {
        for (const property of ['--speech-primary', '--speech-secondary']) {
          const tint = rgb(glow.style.getPropertyValue(property));
          const blended = rgb('#713cce').map((value, i) => value * (1 - alpha) + tint[i] * alpha);
          expect((luminance(rgb(pane.style.getPropertyValue(foreground))) + 0.05) / (luminance(blended) + 0.05)).toBeGreaterThanOrEqual(4.5);
        }
      }
    }
    volume = 0;
    act(() => {
      const pending = callbacks;
      callbacks = [];
      pending.forEach((callback) => callback(80));
    });
    expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBe(0);
  } finally {
    cleanup();
    vi.unstubAllGlobals();
  }
});

vi.mock('@/hooks/useKioskState', () => ({
  useKioskState: () => ({ state: mockKioskState, micEnabled: mockKioskState === 'active', respond: vi.fn() }),
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
  ensurePresentationSession: vi.fn().mockResolvedValue('display-123'),
  shouldEnsurePresentationSession: vi.fn(() => false),
}));
vi.mock('@/components/Visualizer/VisualizerControls', () => ({ VisualizerControls: () => null }));
vi.mock('@/components/Kiosk/Pet/FloatingCodexPet', () => ({
  FloatingCodexPet: (props: { scale?: number }) => (
    <div data-testid="mock-floating-codex-pet" data-scale={props.scale} />
  ),
}));
vi.mock('@/components/Kiosk/SharedBrowserPane', () => ({
  SharedBrowserPane: () => <section data-testid="shared-browser-pane" />,
}));

describe('KioskPage', () => {
  it.each([
    ['purple-glow', 'listening'], ['purple-glow', 'error'],
    ['midnight-blue', 'listening'], ['midnight-blue', 'error'],
  ] as const)('keeps %s %s status readable with the app light palette', (theme, status) => {
    mockKioskState = 'active';
    mockUiLanguage = 'en';
    mockVoiceState.status = status;
    localStorage.setItem('openjarvis_kiosk_visualizer_settings', JSON.stringify({ theme }));
    const { getByText } = render(<MemoryRouter><KioskPage /></MemoryRouter>);
    const color = getByText(voiceStatusLabel('en', status)).parentElement!.style.color;
    const lightAppColors: Record<string, string> = {
      'var(--color-accent)': '#0891b2',
      'var(--color-success)': '#16a34a',
      'var(--color-error)': '#dc2626',
    };
    const foreground = lightAppColors[color] ?? color;
    // The bright regions of these backgrounds must still support small text.
    const background = theme === 'purple-glow' ? '#713cce' : '#1c326e';
    const luminance = (value: string) => {
      const channels = value.startsWith('#')
        ? [1, 3, 5].map((index) => parseInt(value.slice(index, index + 2), 16))
        : value.match(/\d+/g)!.slice(0, 3).map(Number);
      const linear = channels.map((channel) => {
        const v = channel / 255;
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      });
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    };
    const levels = [luminance(foreground), luminance(background)].sort((a, b) => a - b);
    expect((levels[1] + 0.05) / (levels[0] + 0.05)).toBeGreaterThanOrEqual(4.5);
  });

  it('retains mascot preferences but retires saved sphere settings', () => {
    localStorage.setItem('openjarvis_kiosk_visualizer_settings', JSON.stringify({
      style: '3d', theme: 'gold', gain: 4, size: 230,
      showPet: true, petScale: 3.5,
    }));
    const { container, getByLabelText, getByTestId } = render(
      <MemoryRouter><KioskPage /></MemoryRouter>,
    );

    expect(container.querySelector('canvas')).toBeNull();
    expect(getByLabelText('Voice assistant').getAttribute('data-kiosk-theme')).toBe('midnight-blue');
    expect(getByTestId('mock-floating-codex-pet').getAttribute('data-scale')).toBe('3.5');
  });

  it.each(['playful', 'soft-gradient', 'purple-glow', 'midnight-blue', 'pastel-grid'])(
    'restores the saved %s background after reload', (theme) => {
      localStorage.setItem('openjarvis_kiosk_visualizer_settings', JSON.stringify({ theme }));
      const { getByLabelText } = render(<MemoryRouter><KioskPage /></MemoryRouter>);
      expect(getByLabelText('Voice assistant').getAttribute('data-kiosk-theme')).toBe(theme);
    },
  );

  it.each(['idle', 'approaching', 'prompting', 'cleanup'] as const)(
    'keeps the shared screen hidden and the robot full width in %s',
    (state) => {
      mockKioskState = state;
      const { getByTestId, getByLabelText, queryByTestId } = render(
        <MemoryRouter><KioskPage /></MemoryRouter>,
      );

      expect(getByTestId('customer-display-pane-container').hidden).toBe(true);
      expect(getByLabelText('Voice assistant').style.width).toBe('');
      expect(queryByTestId('kiosk-pane-resizer')).toBeNull();
      expect(getByTestId('screens-monitor-button').getAttribute('aria-pressed')).toBe('false');
      expect(getByTestId('active-mic-btn').hasAttribute('disabled')).toBe(true);
      fireEvent.click(getByTestId('active-mic-btn'));
      expect(mockVoiceState.start).not.toHaveBeenCalled();
      fireEvent.click(getByTestId('screens-monitor-button'));
      expect(getByTestId('customer-display-pane-container').hidden).toBe(true);
    },
  );

  it.each(['split', 'floating'])(
    'shows the menu after acceptance and resets screen visibility for the next guest in %s mode',
    (mode) => {
      localStorage.setItem('openjarvis_kiosk_browser_mode', mode);
      const view = <MemoryRouter><KioskPage /></MemoryRouter>;
      const { getByTestId, rerender } = render(view);
      const screenId = mode === 'split' ? 'customer-display-pane-container' : 'floating-browser-container';

      expect(getByTestId(screenId).hidden).toBe(true);
      mockKioskState = 'active';
      rerender(<MemoryRouter><KioskPage /></MemoryRouter>);
      expect(getByTestId(screenId).hidden).toBe(false);
      expect(getByTestId('active-mic-btn').hasAttribute('disabled')).toBe(false);
      fireEvent.click(getByTestId('screens-monitor-button'));
      expect(getByTestId(screenId).hidden).toBe(true);

      mockKioskState = 'cleanup';
      rerender(<MemoryRouter><KioskPage /></MemoryRouter>);
      expect(getByTestId(screenId).hidden).toBe(true);
      mockKioskState = 'active';
      rerender(<MemoryRouter><KioskPage /></MemoryRouter>);
      expect(getByTestId(screenId).hidden).toBe(false);
    },
  );

  it('places the remote camera toggle between the microphone and screen monitor', () => {
    const markup = renderToStaticMarkup(<MemoryRouter><KioskPage /></MemoryRouter>);
    const mic = markup.indexOf('data-testid="active-mic-btn"');
    const camera = markup.indexOf('data-testid="remote-camera-button"');
    const monitor = markup.indexOf('data-testid="screens-monitor-button"');
    expect(mic).toBeGreaterThan(-1);
    expect(camera).toBeGreaterThan(mic);
    expect(monitor).toBeGreaterThan(camera);
    expect(markup).toContain('aria-pressed="false"');
  });

  it.each(['vi', 'en'] as const)('does not block the kiosk with a consent popup in %s', (language) => {
    mockUiLanguage = language;
    const markup = renderToStaticMarkup(<MemoryRouter><KioskPage /></MemoryRouter>);
    expect(markup).not.toContain('data-testid="kiosk-prompting-overlay"');
    expect(markup).not.toContain('Bắt đầu gọi món');
    expect(markup).not.toContain('Start ordering');
  });

  it('shows live transcription when the microphone starts a session before kiosk policy is active', () => {
    mockVoiceState.status = 'listening';
    mockVoiceState.transcript = 'Tôi muốn gọi món';

    const markup = renderToStaticMarkup(<MemoryRouter><KioskPage /></MemoryRouter>);

    expect(markup).toContain('Tôi muốn gọi món');
    expect(markup).not.toContain('data-testid="kiosk-prompting-overlay"');
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
    expect(markup).toContain('data-scale="10"');
  });

  it('automatically resizes the mascot from fullscreen to split and back', () => {
    mockKioskState = 'idle';
    const view = () => <MemoryRouter><KioskPage /></MemoryRouter>;
    const { getByTestId, rerender } = render(view());
    expect(getByTestId('mock-floating-codex-pet').getAttribute('data-scale')).toBe('10');
    mockKioskState = 'active';
    rerender(view());
    expect(getByTestId('mock-floating-codex-pet').getAttribute('data-scale')).toBe('5');
    mockKioskState = 'idle';
    rerender(view());
    expect(getByTestId('mock-floating-codex-pet').getAttribute('data-scale')).toBe('10');
  });

  it('keeps background artwork square as the voice pane changes aspect ratio', () => {
    let bounds = { width: 1920, height: 1080 };
    let resize = () => {};
    const rectangle = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(() => new DOMRect(0, 0, bounds.width, bounds.height));
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { resize = callback; }
      observe() {}
      disconnect() {}
    });
    const { getByLabelText, unmount } = render(<MemoryRouter><KioskPage /></MemoryRouter>);
    try {
      const pane = getByLabelText('Voice assistant');
      expect(pane.style.getPropertyValue('--kiosk-background-size')).toBe('1920px');
      for (const [width, height, expected] of [
        [350, 950, '950px'],
        [950, 350, '950px'],
        [1440, 900, '1440px'],
      ] as const) {
        act(() => { bounds = { width, height }; resize(); });
        expect(pane.style.getPropertyValue('--kiosk-background-size')).toBe(expected);
      }
    } finally {
      unmount();
      rectangle.mockRestore();
      vi.unstubAllGlobals();
    }
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
    expect(markup).not.toContain('Collapse voice pane');
    expect(markup).not.toContain('Share Screen');
    expect(markup).not.toContain('hero-talk-btn');
    expect(markup).not.toContain('dock-mic-btn');
  });

  it('renders 7:3 split layout with fullscreen customer display on the left and seamless voice assistant on the right', () => {
    mockKioskState = 'active';
    const markup = renderToStaticMarkup(<MemoryRouter><KioskPage /></MemoryRouter>);
    expect(markup).toContain('data-testid="customer-display-pane-container"');
    expect(markup).toContain('width:70%');
    expect(markup).not.toContain('border-radius:16px');
    // Divider is removed for a seamless floating feel
    expect(markup).not.toContain('role="separator"');

    expect(markup).toContain('data-testid="kiosk-pane-resizer"');
    expect(markup).toContain('cursor-col-resize');

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
