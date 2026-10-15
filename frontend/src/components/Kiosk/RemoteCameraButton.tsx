import type { UiLanguage } from '@/hooks/useUiLanguage';

interface RemoteCameraButtonProps {
  enabled: boolean;
  active: boolean;
  error: string | null;
  language: UiLanguage;
  onToggle: () => void;
}

export function RemoteCameraButton({ enabled, active, error, language, onToggle }: RemoteCameraButtonProps) {
  const action = language === 'vi'
    ? `${enabled ? 'Tắt' : 'Bật'} camera từ xa`
    : `${enabled ? 'Stop' : 'Start'} remote camera`;

  return (
    <>
      <button
        type="button"
        data-testid="remote-camera-button"
        aria-label={action}
        aria-pressed={enabled}
        aria-busy={enabled && !active && !error}
        title={error ? `${action} — ${error}` : action}
        onClick={onToggle}
        className={`flex h-[48px] w-[48px] shrink-0 cursor-pointer items-center justify-center rounded-[13px] backdrop-blur-md transition-all hover:scale-105 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#7090ec] active:scale-95 ${
          active
          ? 'bg-[#7090ec]/25 text-[#a7bcff] hover:bg-[#7090ec]/35'
            : 'bg-[#263c43]/90 text-[#f1f3f9] shadow-sm hover:bg-[#304b54] active:bg-[#1d3036]'
        }`}
      >
        <svg
          aria-hidden="true"
          className="material-symbols-outlined notranslate ms-button-icon-symbol"
          width="20"
          height="20"
          viewBox="0 -960 960 960"
          fill="currentColor"
        >
          <path d="M160-160q-33 0-56.5-23.5T80-240v-480q0-33 23.5-56.5T160-800h480q33 0 56.5 23.5T720-720v180l160-160v440L720-420v180q0 33-23.5 56.5T640-160H160Zm0-80h480v-480H160v480Zm0 0v-480 480Z" />
        </svg>
      </button>
      {error && <span role="status" className="sr-only">{error}</span>}
    </>
  );
}
