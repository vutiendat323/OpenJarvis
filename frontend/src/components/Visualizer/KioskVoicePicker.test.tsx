// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { KioskVoicePicker } from './KioskVoicePicker';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

const settings = { voice: 'Trúc Ly', voices: ['Trúc Ly', 'Mai Anh'] };
const response = (voice = settings.voice) => new Response(JSON.stringify({ ...settings, voice }));

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  vi.mocked(apiFetch).mockImplementation(async () => response());
});
afterEach(cleanup);

describe('Kiosk voice picker', () => {
  it('loads installed voices, saves a selection, and restores it when reopened', async () => {
    const view = render(<KioskVoicePicker uiLanguage="en" />);
    const picker = await view.findByRole('combobox', { name: 'Voice' });
    await waitFor(() => expect((picker as HTMLSelectElement).value).toBe('Trúc Ly'));
    expect(view.getAllByRole('option').map((option) => option.textContent)).toEqual(settings.voices);
    let finishSave!: (value: Response) => void;
    vi.mocked(apiFetch).mockReturnValueOnce(new Promise((resolve) => { finishSave = resolve; }));
    fireEvent.change(picker, { target: { value: 'Mai Anh' } });
    expect((picker as HTMLSelectElement).disabled).toBe(true);
    expect(apiFetch).toHaveBeenLastCalledWith('/api/kiosk/voice', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice: 'Mai Anh' }),
    });
    finishSave(response('Mai Anh'));
    await waitFor(() => expect((picker as HTMLSelectElement).value).toBe('Mai Anh'));
    view.unmount();
    vi.mocked(apiFetch).mockResolvedValueOnce(response('Mai Anh'));
    const reopened = render(<KioskVoicePicker uiLanguage="en" />);
    await waitFor(() => expect((reopened.getByRole('combobox', { name: 'Voice' }) as HTMLSelectElement).value).toBe('Mai Anh'));
  });

  it('keeps the confirmed voice and shows an error when saving fails', async () => {
    const view = render(<KioskVoicePicker uiLanguage="en" />);
    const picker = view.getByRole('combobox', { name: 'Voice' });
    await waitFor(() => expect((picker as HTMLSelectElement).value).toBe('Trúc Ly'));
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response('{}', { status: 500 }));
    fireEvent.change(picker, { target: { value: 'Mai Anh' } });
    expect((await view.findByRole('alert')).textContent).toContain('Could not save');
    expect((picker as HTMLSelectElement).value).toBe('Trúc Ly');
    expect((picker as HTMLSelectElement).disabled).toBe(false);
  });

  it('disables the picker on load failure and lets the operator retry', async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error('offline'));
    const view = render(<KioskVoicePicker uiLanguage="vi" />);
    await view.findByRole('alert');
    const picker = view.getByRole('combobox', { name: 'Giọng đọc' });
    expect((picker as HTMLSelectElement).disabled).toBe(true);
    fireEvent.click(view.getByRole('button', { name: 'Thử lại' }));
    await waitFor(() => expect((picker as HTMLSelectElement).value).toBe('Trúc Ly'));
    expect((picker as HTMLSelectElement).disabled).toBe(false);
  });
});
