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
      className="flex h-[48px] w-[48px] shrink-0 cursor-pointer items-center justify-center rounded-[13px] bg-[#263c43] text-[#f1f3f9] shadow-sm transition-colors hover:bg-[#304b54] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#7090ec] active:bg-[#1d3036]"
    >
      <svg
        aria-hidden="true"
        className="material-symbols-outlined notranslate ms-button-icon-symbol"
        width="30"
        height="30"
        viewBox="0 0 24 24"
        fill="currentColor"
      >
        <path d="M20 3H4c-1.11 0-2 .89-2 2v12c0 1.1.89 2 2 2h4v2h8v-2h4c1.1 0 2-.9 2-2V5c0-1.11-.9-2-2-2zm0 14H4V5h16v12z" />
        <path d="M6.5 7.5H9V6H5v4h1.5zM19 12h-1.5v2.5H15V16h4z" />
      </svg>
    </button>
  );
}
