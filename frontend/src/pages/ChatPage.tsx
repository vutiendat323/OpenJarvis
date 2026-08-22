import { ScreenShare, ScreenShareOff } from 'lucide-react';

import { ChatArea } from '../components/Chat/ChatArea';
import { SystemPanel } from '../components/Chat/SystemPanel';
import { ScreenShareView } from '@/components/Kiosk/ScreenShareView';
import { useScreenShare } from '@/hooks/useScreenShare';
import { useAppStore } from '../lib/store';

export function ChatPage() {
  const systemPanelOpen = useAppStore((s) => s.systemPanelOpen);
  const share = useScreenShare();

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {!share.unavailable && (
          <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b" style={{ borderColor: 'var(--color-border)' }}>
            <span className="text-[13px]" style={{ color: 'var(--color-text-secondary)' }}>Screen share</span>
            <button
              onClick={share.status === 'live' ? share.stop : share.start}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium cursor-pointer transition-colors"
              style={{ background: 'rgba(255,255,255,.05)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
            >
              {share.status === 'live' ? <ScreenShareOff size={14} /> : <ScreenShare size={14} />}
              {share.status === 'live' ? 'Stop sharing' : 'Share Screen'}
            </button>
          </div>
        )}
        {share.status === 'live' && (
          <div className="shrink-0 relative h-64" style={{ background: '#06060f' }}>
            <ScreenShareView stream={share.stream} />
          </div>
        )}
        <div className="flex-1 min-h-0">
          <ChatArea />
        </div>
      </div>
      {systemPanelOpen && <SystemPanel />}
    </div>
  );
}
