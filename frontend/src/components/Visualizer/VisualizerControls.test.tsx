import { describe, expect, it, vi } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { VisualizerControls } from './VisualizerControls';
import { VISUALIZER_DEFAULTS } from './types';

describe('VisualizerControls', () => {
  it('places a labeled voice picker in the kiosk settings panel', () => {
    const markup = renderToStaticMarkup(
      React.createElement(VisualizerControls, {
        settings: VISUALIZER_DEFAULTS,
        onSettingsChange: vi.fn(),
        status: 'idle',
        uiLanguage: 'en',
        onUiLanguageChange: vi.fn(),
        initialCollapsed: false,
      }),
    );
    expect(markup).toContain('aria-label="Voice"');
    expect(markup).toContain('Applies to the next spoken sentence.');
  });

  it('renders collapsed settings toggle button by default', () => {
    const markup = renderToStaticMarkup(
      React.createElement(VisualizerControls, {
        settings: VISUALIZER_DEFAULTS,
        onSettingsChange: vi.fn(),
        status: 'idle',
        uiLanguage: 'vi',
        onUiLanguageChange: vi.fn(),
      })
    );

    expect(markup).toContain('title="Cài đặt kiosk"');
  });

  it('renders uncollapsed panel with Nhân vật toggle and Kích thước slider', () => {
    const markup = renderToStaticMarkup(
      React.createElement(VisualizerControls, {
        settings: {
          ...VISUALIZER_DEFAULTS,
          showPet: true,
          petScale: 2.5,
        },
        onSettingsChange: vi.fn(),
        status: 'idle',
        uiLanguage: 'vi',
        onUiLanguageChange: vi.fn(),
        initialCollapsed: false,
      })
    );

    expect(markup).toContain('Phụ đề');
    expect(markup).toContain('Nhân vật');
    expect(markup).toContain('Kích thước');
    expect(markup).toContain('2.5x');
    expect(markup).toContain('min="1"');
    expect(markup).toContain('max="10"');
    expect(markup).toContain('step="0.1"');
  });

  it('reflects updated petScale in slider output label', () => {
    const markup = renderToStaticMarkup(
      React.createElement(VisualizerControls, {
        settings: {
          ...VISUALIZER_DEFAULTS,
          showPet: false,
          petScale: 3.4,
        },
        onSettingsChange: vi.fn(),
        status: 'listening',
        uiLanguage: 'en',
        onUiLanguageChange: vi.fn(),
        initialCollapsed: false,
      })
    );

    expect(markup).toContain('3.4x');
  });

  it('renders four kiosk settings sections', () => {
    const markup = renderToStaticMarkup(
      React.createElement(VisualizerControls, {
        settings: VISUALIZER_DEFAULTS,
        onSettingsChange: vi.fn(),
        status: 'speaking',
        uiLanguage: 'vi',
        onUiLanguageChange: vi.fn(),
        initialCollapsed: false,
      })
    );

    // Four distinct card sections
    expect(markup).toContain('Ngôn ngữ UI');
    expect(markup).toContain('Cài đặt kiosk');
    expect(markup).toContain('Trợ lý');
    expect(markup).toContain('Phiên khách hàng');

    // Setting rows in Language
    expect(markup).not.toContain('Voice status');
    expect(markup).toContain('Ngôn ngữ UI');

    // Appearance exposes the selectable assistant background.
    expect(markup).not.toContain('Display mode');
    expect(markup).not.toContain('3D Sphere');
    expect(markup).toContain('Nền');
    expect(markup).not.toContain('>Size<');
    expect(markup).not.toContain('>Gain<');
    expect(markup).not.toContain('>Speed<');
    expect(markup).not.toContain('>Glow<');
    for (const theme of ['Playful', 'Soft Gradient', 'Purple Glow', 'Midnight Blue', 'Pastel']) {
      expect(markup).toContain(`aria-label="${theme}"`);
    }

    // Setting rows in Mascot & Captions
    expect(markup).toContain('Phụ đề');
    expect(markup).toContain('Nhân vật');
    expect(markup).toContain('Kích thước');
  });

  it('renders centered popup dialog with [X] close button matching chatgpt modal style', () => {
    const markup = renderToStaticMarkup(
      React.createElement(VisualizerControls, {
        settings: VISUALIZER_DEFAULTS,
        onSettingsChange: vi.fn(),
        status: 'idle',
        uiLanguage: 'vi',
        onUiLanguageChange: vi.fn(),
        initialCollapsed: false,
      })
    );

    // Centered modal overlay & dialog
    expect(markup).toContain('fixed inset-0');
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain('title="Đóng"');
    expect(markup).toContain('aria-label="Đóng"');
    expect(markup).not.toContain('Hide');
  });
});
