import { Video } from 'lucide-react';
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
        <Video size={20} strokeWidth={2} aria-hidden="true" />
      </button>
      {error && <span role="status" className="sr-only">{error}</span>}
    </>
  );
}
