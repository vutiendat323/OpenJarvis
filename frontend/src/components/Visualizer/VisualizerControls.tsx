import { Section, SettingRow } from '@/components/Settings/SettingsFields';
import { useState } from 'react';
import { createPortal } from 'react-dom';
import { Settings, X } from 'lucide-react';
import { VOICE_UI_TEXT } from '@/hooks/voiceUiText';
import type { UiLanguage } from '@/hooks/useUiLanguage';
import type { VisualizerSettings, VoiceStatus } from './types';
import { KIOSK_THEMES, type KioskTheme } from '@/components/Kiosk/kioskThemes';
import { KioskTransitionSettings } from './KioskTransitionSettings';
import { KioskVoicePicker } from './KioskVoicePicker';

function ScreenIcon({ size = 13, className }: { size?: number; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      width={size}
      height={size}
      viewBox="0 -960 960 960"
      fill="currentColor"
    >
      <path d="M610-326.15h143.85V-470h-47.7v96.15H610v47.7ZM206.15-570h47.7v-96.15H350v-47.7H206.15V-570ZM340-140v-80H172.31Q142-220 121-241q-21-21-21-51.31v-455.38Q100-778 121-799q21-21 51.31-21h615.38Q818-820 839-799q21 21 21 51.31v455.38Q860-262 839-241q-21 21-51.31 21H620v80H340ZM172.31-280h615.38q4.62 0 8.46-3.85 3.85-3.84 3.85-8.46v-455.38q0-4.62-3.85-8.46-3.84-3.85-8.46-3.85H172.31q-4.62 0-8.46 3.85-3.85 3.84-3.85 8.46v455.38q0 4.62 3.85 8.46 3.84 3.85 8.46 3.85ZM160-280v-480 480Z" />
    </svg>
  );
}

// ── Constants ────────────────────────────────────────────────────────────────

const themeKeys = Object.keys(KIOSK_THEMES) as KioskTheme[];

// ── Card & Row Components (matching SettingsPage card template) ─────────────

function SettingToggle({
  checked,
  onChange,
  ariaLabel,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  ariaLabel?: string;
}) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className="relative w-11 h-6 rounded-full transition-colors cursor-pointer shrink-0 border-none outline-none"
      style={{
        background: checked ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
      }}
      aria-label={ariaLabel}
    >
      <span
        className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
        style={{
          transform: checked ? 'translateX(20px)' : 'translateX(0)',
          boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
        }}
      />
    </button>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  settings: VisualizerSettings;
  onSettingsChange: (s: VisualizerSettings) => void;
  status: VoiceStatus;
  uiLanguage: UiLanguage;
  onUiLanguageChange: (language: UiLanguage) => void;
  initialCollapsed?: boolean;
}

export function VisualizerControls({
  settings,
  onSettingsChange,
  status: _status,
  uiLanguage,
  onUiLanguageChange,
  initialCollapsed = true,
}: Props) {
  const [collapsed, setCollapsed] = useState(initialCollapsed);

  const set = <K extends keyof VisualizerSettings>(key: K, val: VisualizerSettings[K]) =>
    onSettingsChange({ ...settings, [key]: val });

  const panelStyle: React.CSSProperties = {
    background: 'var(--color-bg)',
    border: '1px solid var(--color-border)',
    boxShadow: '0 24px 56px rgba(0,0,0,.22)',
    color: 'var(--color-text)',
  };

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="absolute bottom-6 left-4 z-20 w-9 h-9 flex items-center justify-center transition-all cursor-pointer hover:scale-110 opacity-70 hover:opacity-100"
        style={{
          background: 'none',
          border: 'none',
          boxShadow: 'none',
        }}
        title="Open panel"
        aria-label="Open kiosk display controls"
      >
        <Settings size={20} style={{ color: 'var(--kiosk-muted, var(--color-text-secondary))' }} />
      </button>
    );
  }

  const panel = (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/40 dark:bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) setCollapsed(true);
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="voice-visualizer-title"
        className="kiosk-display-panel relative w-full max-w-lg max-h-[88vh] rounded-2xl flex flex-col gap-3.5 p-5 overflow-y-auto shadow-2xl"
        style={panelStyle}
      >
        {/* Header */}
        <div className="flex items-center justify-between pb-1">
          <div
            id="voice-visualizer-title"
            className="text-base font-bold tracking-tight"
            style={{
              color: 'var(--color-text)',
            }}
          >
            Kiosk Display
          </div>
          <button
            onClick={() => setCollapsed(true)}
            className="w-8 h-8 rounded-lg flex items-center justify-center transition-colors cursor-pointer hover:bg-[var(--color-bg-secondary)] hover:text-[var(--color-text)]"
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--color-text-secondary)',
            }}
            title="Close"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

      {/* Card 1: Language */}
      <Section compact title="Language">
        <SettingRow compact
          label="Language"
          description={VOICE_UI_TEXT[uiLanguage].languageHelper}
        >
          <select
            aria-label="Language"
            value={uiLanguage}
            onChange={(e) => {
              const val = e.target.value;
              if (val === 'vi' || val === 'en') onUiLanguageChange(val);
            }}
            className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
            style={{
              background: 'var(--color-bg-secondary)',
              color: 'var(--color-text)',
              border: '1px solid var(--color-border)',
            }}
          >
            <option value="vi">{VOICE_UI_TEXT[uiLanguage].language.vietnamese}</option>
            <option value="en">{VOICE_UI_TEXT[uiLanguage].language.english}</option>
          </select>
        </SettingRow>
        <KioskVoicePicker uiLanguage={uiLanguage} />
      </Section>

      <Section compact title="Display & Background">
        <SettingRow compact label="Display mode" description="Screen voice display beside the browser">
          <span className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium"
            style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)' }}>
            <ScreenIcon /> Screen
          </span>
        </SettingRow>

        <SettingRow compact label="Theme" last>
          <div role="group" aria-label="Background theme" className="flex items-center gap-2">
            {themeKeys.map((key) => {
              const theme = KIOSK_THEMES[key];
              const selected = settings.theme === key;
              return (
                <button
                  key={key}
                  type="button"
                  title={theme.label}
                  aria-label={theme.label}
                  aria-pressed={selected}
                  onClick={() => set('theme', key)}
                  className="h-6 w-6 shrink-0 rounded-full border-none outline-none cursor-pointer transition-transform focus-visible:brightness-125"
                  style={{
                    background: theme.background,
                    transform: selected ? 'scale(1.1)' : undefined,
                  }}
                />
              );
            })}
          </div>
        </SettingRow>
      </Section>

      <Section compact title={uiLanguage === 'vi' ? 'Chuyển trạng thái' : 'State transitions'}>
        <KioskTransitionSettings uiLanguage={uiLanguage} />
      </Section>

      {/* Card 3: Mascot & Captions */}
      <Section compact title="Mascot & Captions">
        <SettingRow compact
          label="Show Captions"
          description="Display live speech transcription"
        >
          <SettingToggle
            checked={settings.showCaptions}
            onChange={(val) => set('showCaptions', val)}
            ariaLabel="Toggle Show Captions"
          />
        </SettingRow>

        <SettingRow compact
          label="Show Mascot"
          description="Floating Codex Pet companion on kiosk"
        >
          <SettingToggle
            checked={settings.showPet}
            onChange={(val) => set('showPet', val)}
            ariaLabel="Toggle Show Mascot"
          />
        </SettingRow>

        <SettingRow compact
          label="Pet Size"
          description={`${settings.petScale.toFixed(1)}x`}
          last
        >
          <input
            aria-label="Pet Size"
            type="range"
            min={1}
            max={10}
            step={0.1}
            value={settings.petScale}
            onChange={(e) => set('petScale', parseFloat(e.target.value))}
            className="w-28 sm:w-32 cursor-pointer accent-[var(--color-accent)]"
            style={{ accentColor: 'var(--color-accent)' }}
          />
        </SettingRow>
      </Section>
      </div>
    </div>
  );
  return typeof document === 'undefined' ? panel : createPortal(panel, document.body);
}
