import React from 'react';
import { ScreenShare } from 'lucide-react';

export interface ScreenShareHeroProps {
  onStartScreenShare: () => void;
  isShareUnavailable?: boolean;
  uiLanguage?: string;
  className?: string;
}

export function ScreenShareHero({
  onStartScreenShare,
  isShareUnavailable = false,
  uiLanguage = 'en',
  className = '',
}: ScreenShareHeroProps) {
  const isVi = uiLanguage === 'vi';

  return (
    <div
      data-testid="screen-share-hero"
      className={`absolute inset-0 flex flex-col items-center justify-center pointer-events-none select-none z-10 px-4 ${className}`}
    >
      <div className="flex flex-col items-center text-center max-w-lg gap-6 pointer-events-auto">
        {/* Hero Title */}
        <h1
          className="text-3xl sm:text-4xl font-semibold tracking-tight"
          style={{ color: 'var(--color-text)' }}
        >
          {isVi ? 'Thử Jarvis Trực Tiếp' : 'Try Live Jarvis'}
        </h1>

        {/* Action Pills Row */}
        <div className="flex items-center gap-3">
          <button
            type="button"
            data-testid="hero-share-btn"
            disabled={isShareUnavailable}
            onClick={isShareUnavailable ? undefined : onStartScreenShare}
            title={
              isShareUnavailable
                ? (isVi ? 'Chia sẻ màn hình không khả dụng trên thiết bị này' : 'Screen sharing is unavailable on this device')
                : (isVi ? 'Chia sẻ màn hình' : 'Share Screen')
            }
            className={`flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-medium transition-all duration-200 shadow-md ${
              isShareUnavailable
                ? 'opacity-40 cursor-not-allowed'
                : 'cursor-pointer hover:scale-105 active:scale-95'
            }`}
            style={{
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text)',
            }}
          >
            <ScreenShare size={16} />
            <span>{isVi ? 'Chia sẻ màn hình' : 'Share Screen'}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
