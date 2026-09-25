import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { initialBrowserState } from '@/hooks/useSharedBrowser';
import { SharedBrowserPane } from './SharedBrowserPane';

describe('SharedBrowserPane', () => {
  it('keeps browser controls hidden until the reveal handle is used', () => {
    const markup = renderToStaticMarkup(
      <SharedBrowserPane
        browser={{
          ...initialBrowserState,
          url: 'https://example.test/menu',
          send: vi.fn(),
        }}
      />,
    );

    expect(markup).toContain('aria-label="Show browser controls"');
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain('data-testid="browser-controls" aria-hidden="true"');
    expect(markup).toContain('data-testid="browser-controls-handle"');
    expect(markup).toContain('mx-auto flex h-3 w-14');
  });

  it('renders one interactive viewport with navigation and the current frame', () => {
    const markup = renderToStaticMarkup(
      <SharedBrowserPane
        browser={{
          ...initialBrowserState,
          status: 'connected',
          url: 'https://example.test/menu',
          title: 'Menu',
          frame: 'data:image/jpeg;base64,/9j/abc',
          width: 800,
          height: 600,
          send: vi.fn(),
        }}
      />,
    );

    expect(markup).toContain('data-testid="shared-browser-pane"');
    expect(markup).toContain('aria-label="Browser address"');
    expect(markup).toContain('value="https://example.test/menu"');
    expect(markup).toContain('aria-label="Back"');
    expect(markup).toContain('aria-label="Forward"');
    expect(markup).toContain('aria-label="Reload"');
    expect(markup).toContain('data:image/jpeg;base64,/9j/abc');
    expect(markup).toContain('aria-label="Shared browser viewport"');
  });

  it('renders zoom controls and floating toggle button when props are provided', () => {
    const markup = renderToStaticMarkup(
      <SharedBrowserPane
        browser={{
          ...initialBrowserState,
          status: 'connected',
          url: 'https://example.test/menu',
          send: vi.fn(),
        }}
        isFloating={false}
        onToggleFloating={vi.fn()}
        onZoom={vi.fn()}
      />,
    );

    expect(markup).toContain('aria-label="Zoom in"');
    expect(markup).toContain('aria-label="Zoom out"');
    expect(markup).toContain('data-testid="browser-zoom-controls" class="absolute bottom-2 right-2');
    expect(markup).toContain('aria-label="Pop out to floating window"');
  });

  it('renders drag handle, maximize button, and dock button when in floating mode', () => {
    const markup = renderToStaticMarkup(
      <SharedBrowserPane
        browser={{
          ...initialBrowserState,
          status: 'connected',
          url: 'https://example.test/menu',
          send: vi.fn(),
        }}
        isFloating={true}
        isMaximized={false}
        onToggleFloating={vi.fn()}
        onToggleMaximize={vi.fn()}
        dragHandleProps={{ 'data-testid': 'mock-drag-handle' }}
      />,
    );

    expect(markup).toContain('aria-label="Drag window"');
    expect(markup).toContain('data-testid="mock-drag-handle"');
    expect(markup).toContain('cursor-grab');
    expect(markup).toContain('aria-label="Maximize"');
    expect(markup).toContain('aria-label="Dock to split"');
  });
});
