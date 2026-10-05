import type { UiLanguage } from '@/hooks/useUiLanguage';

interface ScreensMonitorButtonProps {
  visible: boolean;
  language: UiLanguage;
  onToggle: () => void;
}

export function ScreensMonitorButton({ visible, language, onToggle }: ScreensMonitorButtonProps) {
  const action = language === 'vi'
    ? `${visible ? 'Ẩn' : 'Hiện'} màn hình chia sẻ`
    : `${visible ? 'Hide' : 'Show'} shared screen`;

  return (
    <button
      type="button"
      data-testid="screens-monitor-button"
      aria-label={`Screens Monitor — ${action}`}
      aria-controls="kiosk-shared-screen"
      aria-pressed={visible}
      title={`Screens Monitor — ${action}`}
      onClick={onToggle}
      className={`flex h-[48px] w-[48px] shrink-0 cursor-pointer items-center justify-center rounded-[13px] shadow-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#7090ec] active:bg-[#1d3036] ${
        visible
          ? 'bg-[#7090ec]/25 text-[#a7bcff] hover:bg-[#7090ec]/35'
          : 'bg-[#263c43] text-[#f1f3f9] hover:bg-[#304b54]'
      }`}
    >
      <svg
        aria-hidden="true"
        className="material-symbols-outlined notranslate ms-button-icon-symbol"
        width="26"
        height="26"
        viewBox="0 -960 960 960"
        fill="currentColor"
      >
        <path d="M610-326.15h143.85V-470h-47.7v96.15H610v47.7ZM206.15-570h47.7v-96.15H350v-47.7H206.15V-570ZM340-140v-80H172.31Q142-220 121-241q-21-21-21-51.31v-455.38Q100-778 121-799q21-21 51.31-21h615.38Q818-820 839-799q21 21 21 51.31v455.38Q860-262 839-241q-21 21-51.31 21H620v80H340ZM172.31-280h615.38q4.62 0 8.46-3.85 3.85-3.84 3.85-8.46v-455.38q0-4.62-3.85-8.46-3.84-3.85-8.46-3.85H172.31q-4.62 0-8.46 3.85-3.85 3.84-3.85 8.46v455.38q0 4.62 3.85 8.46 3.84 3.85 8.46 3.85ZM160-280v-480 480Z" />
      </svg>
    </button>
  );
}
