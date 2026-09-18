import { describe, expect, it, vi } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ScreenShareHero } from './ScreenShareHero';

describe('ScreenShareHero', () => {
  it('renders the title and screen share action without a manual Voice control', () => {
    const markup = renderToStaticMarkup(
      React.createElement(ScreenShareHero, {
        onStartScreenShare: vi.fn(),
      })
    );

    expect(markup).toContain('Try Live Jarvis');
    expect(markup).toContain('Share Screen');
    expect(markup).not.toContain('data-testid="hero-talk-btn"');
    expect(markup).not.toContain('Talking...');
    expect(markup).toContain('data-testid="hero-share-btn"');
  });

  it('renders localized Vietnamese text when uiLanguage is vi', () => {
    const markup = renderToStaticMarkup(
      React.createElement(ScreenShareHero, {
        onStartScreenShare: vi.fn(),
        uiLanguage: 'vi',
      })
    );

    expect(markup).toContain('Thử Jarvis Trực Tiếp');
    expect(markup).toContain('Chia sẻ màn hình');
  });

  it('disables screen share button when isShareUnavailable is true', () => {
    const markup = renderToStaticMarkup(
      React.createElement(ScreenShareHero, {
        onStartScreenShare: vi.fn(),
        isShareUnavailable: true,
      })
    );

    expect(markup).toContain('disabled=""');
    expect(markup).toContain('opacity-40 cursor-not-allowed');
  });
});
