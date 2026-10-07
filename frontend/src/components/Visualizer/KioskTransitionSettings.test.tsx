// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { KioskTransitionSettings } from './KioskTransitionSettings';
import { VisualizerControls } from './VisualizerControls';
import { VISUALIZER_DEFAULTS } from './types';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

const defaults = {
  approach_threshold_m: 1,
  approach_entry_debounce: 0.4,
  approach_sustain_seconds: 2,
  leave_sustain_seconds_prompting: 5,
  leave_sustain_seconds_active: 10,
  session_max_seconds: 600,
  popup_timeout: 30,
  decline_cooldown_seconds: 10,
};

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify(defaults)));
});
afterEach(cleanup);

describe('Kiosk transition settings', () => {
  it('keeps the settings dialog outside the kiosk pane stacking context', async () => {
    const view = render(
      <div style={{ transform: 'translateZ(0)', overflow: 'hidden' }}>
        <VisualizerControls settings={VISUALIZER_DEFAULTS} onSettingsChange={vi.fn()}
          status="idle" uiLanguage="vi" onUiLanguageChange={vi.fn()} initialCollapsed={false} />
      </div>,
    );
    await view.findAllByRole('textbox');
    expect(view.container.querySelector('[role="dialog"]')).toBeNull();
    expect(view.getByRole('dialog', { name: 'AI Voice Visualizer' })).toBeTruthy();
    fireEvent.click(view.getByRole('button', { name: 'Close' }));
    expect(view.queryByRole('dialog')).toBeNull();
  });
  it('loads actual backend values and exposes only transition boundaries', async () => {
    const view = render(<KioskTransitionSettings uiLanguage="vi" />);
    const distance = await view.findByRole('textbox', { name: /Khoảng cách tiếp cận/ });
    expect(distance.getAttribute('value')).toBe('1');
    expect(view.getAllByRole('textbox')).toHaveLength(8);
    expect(view.getByText(/Cleanup → idle tự động/)).toBeTruthy();
    expect(view.queryByText(/cảnh báo/i)).toBeNull();
    expect(apiFetch).toHaveBeenCalledWith('/api/kiosk/settings');
  });

  it('saves edited values to the backend and displays the confirmed result', async () => {
    const view = render(<KioskTransitionSettings uiLanguage="vi" />);
    const duration = await view.findByRole('textbox', { name: /Thời lượng phiên tối đa/ });
    fireEvent.change(duration, { target: { value: '180' } });
    const values = { ...defaults, session_max_seconds: 180 };
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify(values)));
    fireEvent.click(view.getByRole('button', { name: 'Lưu chuyển trạng thái' }));
    await view.findByText('Đã lưu và áp dụng.');
    expect(apiFetch).toHaveBeenLastCalledWith('/api/kiosk/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: expect.any(String),
    });
    expect(JSON.parse(String(vi.mocked(apiFetch).mock.calls.slice(-1)[0]?.[1]?.body))).toEqual(values);
    expect(duration.getAttribute('value')).toBe('180');
  });

  it('rejects debounce greater than the approach hold without sending a save', async () => {
    const view = render(<KioskTransitionSettings uiLanguage="vi" />);
    const debounce = await view.findByRole('textbox', { name: /Thời gian xác nhận tiếp cận/ });
    fireEvent.change(debounce, { target: { value: '3' } });
    fireEvent.click(view.getByRole('button', { name: 'Lưu chuyển trạng thái' }));
    expect(view.getByRole('alert').textContent).toContain('nhỏ hơn hoặc bằng');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it.each(['0.7', '0,7'])('allows direct decimal entry %s and saves a number', async (entry) => {
    const view = render(<KioskTransitionSettings uiLanguage="vi" />);
    const debounce = await view.findByRole('textbox', { name: /Thời gian xác nhận tiếp cận/ });
    expect(view.queryAllByRole('spinbutton')).toHaveLength(0);
    fireEvent.change(debounce, { target: { value: '0.' } });
    expect(debounce.getAttribute('value')).toBe('0.');
    fireEvent.change(debounce, { target: { value: entry } });
    const values = { ...defaults, approach_entry_debounce: 0.7 };
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify(values)));
    fireEvent.click(view.getByRole('button', { name: 'Lưu chuyển trạng thái' }));
    await view.findByText('Đã lưu và áp dụng.');
    expect(apiFetch).toHaveBeenLastCalledWith('/api/kiosk/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: expect.any(String),
    });
    expect(JSON.parse(String(vi.mocked(apiFetch).mock.calls.slice(-1)[0]?.[1]?.body))).toEqual(values);
  });

  it.each(['', 'abc', '9999'])('rejects invalid direct entry %s without saving', async (entry) => {
    const view = render(<KioskTransitionSettings uiLanguage="vi" />);
    const duration = await view.findByRole('textbox', { name: /Thời lượng phiên tối đa/ });
    fireEvent.change(duration, { target: { value: entry } });
    fireEvent.submit(duration.closest('form')!);
    expect(view.getByRole('alert').textContent).toContain('khoảng cho phép');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('explains why changes cannot be saved during an active ordering session', async () => {
    const view = render(<KioskTransitionSettings uiLanguage="vi" />);
    await view.findAllByRole('textbox');
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response('{}', { status: 409 }));
    fireEvent.click(view.getByRole('button', { name: 'Lưu chuyển trạng thái' }));
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('kết thúc phiên'));
    expect(view.queryByText('Đã lưu và áp dụng.')).toBeNull();
  });

  it('offers retry after a load failure and does not show editable defaults', async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error('offline'));
    const view = render(<KioskTransitionSettings uiLanguage="en" />);
    await view.findByRole('alert');
    expect(view.queryAllByRole('textbox')).toHaveLength(0);
    fireEvent.click(view.getByRole('button', { name: 'Retry' }));
    await view.findAllByRole('textbox');
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });
});
