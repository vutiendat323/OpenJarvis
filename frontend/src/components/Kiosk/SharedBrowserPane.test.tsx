import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { initialBrowserState } from '@/hooks/useSharedBrowser';
import { SharedBrowserPane } from './SharedBrowserPane';

describe('SharedBrowserPane', () => {
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
});
