import { describe, expect, it } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  VoiceWaveform,
  computeWaveformHeights,
} from './VoiceWaveform';

describe('computeWaveformHeights', () => {
  it('returns default idle heights with middle bar taller than sides when inactive', () => {
    const heights = computeWaveformHeights(undefined, false);
    expect(heights).toEqual([7, 16, 7]);
    expect(heights[1]).toBeGreaterThan(heights[0]);
    expect(heights[1]).toBeGreaterThan(heights[2]);
  });

  it('ensures the center bar is always strictly taller than side bars when active', () => {
    // Test with all zeros
    const zeroData = new Uint8Array(128).fill(0);
    const zeroHeights = computeWaveformHeights(zeroData, true, [7, 16, 7]);
    expect(zeroHeights[1]).toBeGreaterThan(zeroHeights[0]);
    expect(zeroHeights[1]).toBeGreaterThan(zeroHeights[2]);

    // Test with maximum audio values
    const maxData = new Uint8Array(128).fill(255);
    const maxHeights = computeWaveformHeights(maxData, true, [7, 16, 7]);
    expect(maxHeights[1]).toBeGreaterThan(maxHeights[0]);
    expect(maxHeights[1]).toBeGreaterThan(maxHeights[2]);

    // Test with biased high frequencies
    const highData = new Uint8Array(128);
    for (let i = 25; i < 55; i++) highData[i] = 255;
    const highHeights = computeWaveformHeights(highData, true, [7, 16, 7]);
    expect(highHeights[1]).toBeGreaterThan(highHeights[0]);
    expect(highHeights[1]).toBeGreaterThan(highHeights[2]);
  });
});

describe('VoiceWaveform Component', () => {
  it('renders squircle container with 15px border radius, 3 waveform bars and mic button', () => {
    const html = renderToStaticMarkup(
      React.createElement(VoiceWaveform, { speaking: false })
    );

    expect(html).toContain('data-testid="voice-dock-pill"');
    expect(html).toContain('border-radius:15px');
    expect(html).toContain('data-testid="voice-dock-waveform"');
    expect(html).toContain('data-testid="voice-dock-waveform-bar-0"');
    expect(html).toContain('data-testid="voice-dock-waveform-bar-1"');
    expect(html).toContain('data-testid="voice-dock-waveform-bar-2"');

    // In idle state, middle bar (16px) must be taller than side bars (7px)
    expect(html).toContain('height:7px');
    expect(html).toContain('height:16px');

    expect(html).toContain('data-testid="active-mic-btn"');
    expect(html).toContain('border-radius:13px');
    expect(html).toContain('aria-label="Active microphone"');
  });

  it('sets appropriate aria-label when active vs idle', () => {
    const idleHtml = renderToStaticMarkup(
      React.createElement(VoiceWaveform, { speaking: false })
    );
    expect(idleHtml).toContain('aria-label="Microphone ready"');

    const speakingHtml = renderToStaticMarkup(
      React.createElement(VoiceWaveform, { speaking: true })
    );
    expect(speakingHtml).toContain('aria-label="Microphone active and listening"');

    const listeningHtml = renderToStaticMarkup(
      React.createElement(VoiceWaveform, { voiceStatus: 'listening' })
    );
    expect(listeningHtml).toContain('aria-label="Microphone active and listening"');
  });
});
