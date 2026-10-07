import { useEffect, useId, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { SettingInput, SettingRow } from '@/components/Settings/SettingsFields';
import type { UiLanguage } from '@/hooks/useUiLanguage';

interface TransitionSettings {
  approach_threshold_m: number;
  approach_entry_debounce: number;
  approach_sustain_seconds: number;
  leave_sustain_seconds_prompting: number;
  leave_sustain_seconds_active: number;
  session_max_seconds: number;
  popup_timeout: number;
  decline_cooldown_seconds: number;
}

type TransitionDraft = { [Key in keyof TransitionSettings]: string };
const numberPattern = /^(?:\d+(?:[.,]\d+)?|[.,]\d+)$/;

function toDraft(values: TransitionSettings): TransitionDraft {
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [key, String(value)]),
  ) as TransitionDraft;
}

const fields: Array<{
  key: keyof TransitionSettings;
  transition: string;
  vi: string;
  en: string;
  min: number;
  max: number;
  unit: string;
}> = [
  { key: 'approach_threshold_m', transition: 'idle → approaching', vi: 'Khoảng cách tiếp cận', en: 'Approach distance', min: 0.1, max: 6, unit: 'm' },
  { key: 'approach_entry_debounce', transition: 'idle → approaching', vi: 'Thời gian xác nhận tiếp cận', en: 'Approach confirmation', min: 0.1, max: 10, unit: 's' },
  { key: 'approach_sustain_seconds', transition: 'approaching → prompting / idle', vi: 'Thời gian giữ hoặc rời vùng', en: 'Approach / departure hold', min: 0.1, max: 30, unit: 's' },
  { key: 'popup_timeout', transition: 'prompting → idle', vi: 'Thời gian chờ xác nhận', en: 'Consent timeout', min: 1, max: 120, unit: 's' },
  { key: 'leave_sustain_seconds_prompting', transition: 'prompting → idle', vi: 'Thời gian vắng khi chờ xác nhận', en: 'Absence during consent', min: 0.1, max: 60, unit: 's' },
  { key: 'decline_cooldown_seconds', transition: 'prompting → idle → approaching', vi: 'Thời gian chờ sau từ chối', en: 'Cooldown after decline', min: 0, max: 120, unit: 's' },
  { key: 'session_max_seconds', transition: 'active → cleanup', vi: 'Thời lượng phiên tối đa', en: 'Maximum session duration', min: 120, max: 3600, unit: 's' },
  { key: 'leave_sustain_seconds_active', transition: 'active → cleanup', vi: 'Thời gian vắng trong phiên', en: 'Absence during session', min: 0.1, max: 120, unit: 's' },
];

async function readSettings(response: Response): Promise<TransitionSettings> {
  if (!response.ok) throw new Error(String(response.status));
  const values = await response.json();
  if (!fields.every(({ key, min, max }) => typeof values?.[key] === 'number'
    && Number.isFinite(values[key]) && values[key] >= min && values[key] <= max)) {
    throw new Error('invalid_settings');
  }
  return values;
}

export function KioskTransitionSettings({ uiLanguage }: { uiLanguage: UiLanguage }) {
  const vi = uiLanguage === 'vi';
  const formId = useId();
  const [settings, setSettings] = useState<TransitionDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void apiFetch('/api/kiosk/settings').then(readSettings).then((values) => {
      if (!cancelled) setSettings(toDraft(values));
    }).catch(() => {
      if (!cancelled) setError('load');
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [retry]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!settings || saving) return;
    const values = Object.fromEntries(fields.map(({ key }) => [
      key, Number(settings[key].trim().replace(',', '.')),
    ])) as Record<keyof TransitionSettings, number>;
    if (!fields.every(({ key, min, max }) => numberPattern.test(settings[key].trim())
      && Number.isFinite(values[key]) && values[key] >= min && values[key] <= max)) {
      setError('range');
      return;
    }
    if (values.approach_entry_debounce > values.approach_sustain_seconds) {
      setError('hold');
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const response = await apiFetch('/api/kiosk/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      setSettings(toDraft(await readSettings(response)));
      setSaved(true);
    } catch (failure) {
      setError(failure instanceof Error && failure.message === '409' ? 'active' : 'save');
    } finally {
      setSaving(false);
    }
  };

  const errors: Record<string, string> = vi ? {
    load: 'Không tải được cài đặt chuyển trạng thái.',
    range: 'Vui lòng nhập các giá trị trong khoảng cho phép.',
    hold: 'Thời gian xác nhận tiếp cận phải nhỏ hơn hoặc bằng thời gian giữ vùng.',
    active: 'Hãy kết thúc phiên gọi món trước khi lưu cài đặt.',
    save: 'Không lưu được cài đặt. Vui lòng thử lại.',
  } : {
    load: 'Could not load transition settings.',
    range: 'Enter values within the allowed ranges.',
    hold: 'Approach confirmation must not exceed the approach hold time.',
    active: 'End the ordering session before saving settings.',
    save: 'Could not save settings. Please try again.',
  };

  return (
    <form onSubmit={save}>
      {loading && <p role="status" className="text-xs py-3">{vi ? 'Đang tải…' : 'Loading…'}</p>}
      {!loading && settings && (
        <fieldset disabled={saving} className="border-0 m-0 p-0 min-w-0">
          {fields.map(({ key, transition, unit, ...labels }) => (
            <SettingRow compact key={key} label={vi ? labels.vi : labels.en} description={transition} htmlFor={`${formId}-${key}`}>
              <span className="flex items-center gap-1 shrink-0">
                <SettingInput id={`${formId}-${key}`} type="text" inputMode="decimal" required
                  pattern={numberPattern.source}
                  value={settings[key]}
                  onChange={(event) => {
                    setSettings({ ...settings, [key]: event.target.value });
                    setSaved(false);
                    setError(null);
                  }}
                  className="w-20"
                />
                <span className="text-xs w-3">{unit}</span>
              </span>
            </SettingRow>
          ))}
          <p className="text-[11px] my-3" style={{ color: 'var(--color-text-tertiary)' }}>
            {vi ? 'Accept: prompting → active. Cleanup → idle tự động.' : 'Accept: prompting → active. Cleanup → idle is automatic.'}
          </p>
          <button type="submit" disabled={saving}
            className="rounded-md px-3 py-1.5 text-xs font-medium cursor-pointer disabled:opacity-50"
            style={{ background: 'var(--color-accent)', color: 'var(--color-on-accent)' }}>
            {saving ? (vi ? 'Đang lưu…' : 'Saving…') : (vi ? 'Lưu chuyển trạng thái' : 'Save transitions')}
          </button>
        </fieldset>
      )}
      {error && <p role="alert" className="text-xs mt-2" style={{ color: 'var(--color-error)' }}>{errors[error]}</p>}
      {!loading && error === 'load' && <button type="button" onClick={() => setRetry(retry + 1)} className="text-xs mt-2 underline cursor-pointer">{vi ? 'Thử lại' : 'Retry'}</button>}
      {saved && <p role="status" className="text-xs mt-2">{vi ? 'Đã lưu và áp dụng.' : 'Saved and applied.'}</p>}
    </form>
  );
}
