// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SpeechGlow } from './SpeechGlow';

let frames: Map<number, FrameRequestCallback>;
let reducedMotion: boolean;
let motionChanged: () => void;

function frame(time: number) {
  const pending = [...frames.values()];
  frames.clear();
  for (const callback of pending) callback(time);
}

beforeEach(() => {
  frames = new Map();
  reducedMotion = false;
  let id = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++id, callback);
    return id;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  vi.stubGlobal('matchMedia', () => ({
    get matches() { return reducedMotion; },
    addEventListener: (_event: string, callback: () => void) => { motionChanged = callback; },
    removeEventListener: () => {},
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('speech background glow', () => {
  it.each(['listening', 'speaking'] as const)('follows real volume and settles in silence during %s', (status) => {
    let volume = 0.02;
    const { getByTestId } = render(<SpeechGlow status={status} getAudioLevel={() => volume} lightBackground={false} />);
    const glow = getByTestId('kiosk-center-glow');
    frame(40);
    const quietOpacity = Number(glow.style.opacity);
    expect(quietOpacity).toBeGreaterThan(0);
    volume = 0.1;
    frame(80);
    expect(Number(glow.style.opacity)).toBeGreaterThan(quietOpacity);
    expect(Number(glow.style.opacity)).toBeLessThanOrEqual(0.3);
    expect(glow.style.transform).not.toBe('scale(1)');
    volume = 0;
    frame(120);
    expect(Number(glow.style.opacity)).toBe(0);
    expect(glow.style.transform).toBe('scale(1)');
  });

  it('uses different speech tints and fades when the turn ends', () => {
    const { getByTestId, rerender } = render(<SpeechGlow status="listening" getAudioLevel={() => 0.1} lightBackground={false} />);
    const glow = getByTestId('kiosk-center-glow');
    frame(40);
    const customerTint = glow.style.getPropertyValue('--speech-primary');
    rerender(<SpeechGlow status="speaking" getAudioLevel={() => 0.1} lightBackground={false} />);
    frame(80);
    expect(glow.style.getPropertyValue('--speech-primary')).not.toBe(customerTint);
    rerender(<SpeechGlow status="processing" getAudioLevel={() => 0.1} lightBackground={false} />);
    expect(Number(glow.style.opacity)).toBe(0);
    expect(frames.size).toBe(0);
  });

  it('keeps light backgrounds subtle even with a loud input', () => {
    const { getByTestId } = render(<SpeechGlow status="speaking" getAudioLevel={() => 1} lightBackground />);
    frame(40);
    expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBeGreaterThan(0);
    expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBeLessThanOrEqual(0.2);
  });

  it.each(['#713cce', '#1c326e'])('keeps small caption text readable at peak glow over %s', (background) => {
    const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const luminance = (channels: number[]) => channels.map((value) => {
      const v = value / 255;
      return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
    const getAudioLevel = () => 1;
    const { getByTestId, rerender } = render(<SpeechGlow status="listening" getAudioLevel={getAudioLevel} lightBackground={false} />);
    for (const status of ['listening', 'speaking'] as const) {
      rerender(<SpeechGlow status={status} getAudioLevel={getAudioLevel} lightBackground={false} />);
      frame(status === 'listening' ? 40 : 80);
      const glow = getByTestId('kiosk-center-glow');
      const alpha = Number(glow.style.opacity);
      for (const property of ['--speech-primary', '--speech-secondary']) {
        const tint = rgb(glow.style.getPropertyValue(property));
        const blended = rgb(background).map((value, i) => value * (1 - alpha) + tint[i] * alpha);
        expect((luminance(rgb('#dae2f4')) + 0.05) / (luminance(blended) + 0.05)).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it('pauses in a hidden tab and resumes with fresh audio when visible', () => {
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    try {
      const { getByTestId } = render(<SpeechGlow status="listening" getAudioLevel={() => 0.1} lightBackground={false} />);
      frame(40);
      expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBeGreaterThan(0);
      hidden.mockReturnValue(true);
      document.dispatchEvent(new Event('visibilitychange'));
      expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBe(0);
      expect(frames.size).toBe(0);
      hidden.mockReturnValue(false);
      document.dispatchEvent(new Event('visibilitychange'));
      frame(80);
      expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBeGreaterThan(0);
    } finally {
      hidden.mockRestore();
    }
  });

  it('stops motion when the preference changes and resumes when allowed', () => {
    const { getByTestId, unmount } = render(<SpeechGlow status="speaking" getAudioLevel={() => 0.1} lightBackground={false} />);
    frame(40);
    expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBeGreaterThan(0);
    reducedMotion = true;
    motionChanged();
    expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBe(0);
    expect(frames.size).toBe(0);
    reducedMotion = false;
    motionChanged();
    frame(80);
    expect(Number(getByTestId('kiosk-center-glow').style.opacity)).toBeGreaterThan(0);
    unmount();
    expect(frames.size).toBe(0);
  });
});
