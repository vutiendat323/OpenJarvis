import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { SettingRow } from '@/components/Settings/SettingsFields';
import type { UiLanguage } from '@/hooks/useUiLanguage';

interface VoiceSettings {
  voice: string;
  voices: string[];
}

async function readSettings(response: Response): Promise<VoiceSettings> {
  if (!response.ok) throw new Error(String(response.status));
  const settings = await response.json();
  if (!Array.isArray(settings?.voices)
    || !settings.voices.every((voice: unknown) => typeof voice === 'string')
    || !settings.voices.includes(settings.voice)) {
    throw new Error('invalid_voice_settings');
  }
  return settings;
}

export function KioskVoicePicker({ uiLanguage }: { uiLanguage: UiLanguage }) {
  const vi = uiLanguage === 'vi';
  const label = vi ? 'Giọng đọc' : 'Voice';
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<'load' | 'save' | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiFetch('/api/kiosk/voice').then(readSettings).then((values) => {
      if (!cancelled) setSettings(values);
    }).catch(() => {
      if (!cancelled) setError('load');
    });
    return () => { cancelled = true; };
  }, [retry]);

  const selectVoice = async (voice: string) => {
    if (!settings || saving) return;
    setSaving(true);
    setError(null);
    try {
      const response = await apiFetch('/api/kiosk/voice', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice }),
      });
      setSettings(await readSettings(response));
    } catch {
      setError('save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <SettingRow compact label={label}
        description={<span style={{ color: 'var(--color-text-secondary)' }}>
          {vi ? 'Áp dụng cho câu nói tiếp theo.' : 'Applies to the next spoken sentence.'}
        </span>}
        last
      >
        <select aria-label={label} value={settings?.voice ?? ''}
          disabled={!settings || saving}
          onChange={(event) => { void selectVoice(event.target.value); }}
          className="text-sm px-3 py-1.5 rounded-lg outline-none focus-visible:outline-2 focus-visible:outline-[var(--color-accent)] cursor-pointer max-w-36 disabled:opacity-50"
          style={{
            background: 'var(--color-bg-secondary)',
            color: 'var(--color-text)',
            border: '1px solid var(--color-border)',
          }}
        >
          {!settings && <option value="">{vi ? 'Đang tải…' : 'Loading…'}</option>}
          {settings?.voices.map((voice) => <option key={voice} value={voice}>{voice}</option>)}
        </select>
      </SettingRow>
      {error && (
        <div role="alert" className="text-xs mt-2" style={{ color: 'var(--color-text-secondary)' }}>
          {error === 'load'
            ? (vi ? 'Không tải được danh sách giọng đọc.' : 'Could not load voices.')
            : (vi ? 'Không lưu được giọng đọc. Vui lòng thử lại.' : 'Could not save voice. Please try again.')}
          {error === 'load' && (
            <button type="button" onClick={() => setRetry((value) => value + 1)}
              className="ml-2 underline cursor-pointer"
            >{vi ? 'Thử lại' : 'Retry'}</button>
          )}
        </div>
      )}
    </>
  );
}
