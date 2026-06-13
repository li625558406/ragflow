import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Smartphone } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';

// Server-configurable download URLs via env variables.
// Update .env.production after server migration:
//   VITE_ANDROID_DOWNLOAD_URL  — direct APK download link
//   VITE_IOS_DOWNLOAD_URL      — iOS install guide page (or itms-services:// OTA manifest)
const ANDROID_DOWNLOAD_URL =
  import.meta.env.VITE_ANDROID_DOWNLOAD_URL ||
  'http://47.98.102.55/downloads/bidding-app.apk';
const IOS_DOWNLOAD_URL =
  import.meta.env.VITE_IOS_DOWNLOAD_URL ||
  'http://47.98.102.55/downloads/ios-install.html';

interface AppDownloadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function AppDownloadDialog({
  open,
  onOpenChange,
}: AppDownloadDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="text-center text-lg">
            下载标书分析助手
          </DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-6 py-4">
          {/* Android */}
          <div className="flex flex-col items-center gap-3">
            <div className="p-3 bg-white border border-[#E2E8F0] rounded-2xl">
              <QRCodeSVG
                value={ANDROID_DOWNLOAD_URL}
                size={140}
                level="M"
                fgColor="#0F172A"
              />
            </div>
            <div className="flex items-center gap-1.5">
              <Smartphone className="size-4 text-[#10B981]" />
              <span className="text-sm font-semibold text-[#0F172A]">
                Android
              </span>
            </div>
            <p className="text-xs text-[#64748B] text-center leading-relaxed">
              扫描二维码下载
              <br />
              Android 安装包
            </p>
          </div>

          {/* iOS */}
          <div className="flex flex-col items-center gap-3">
            <div className="p-3 bg-white border border-[#E2E8F0] rounded-2xl">
              <QRCodeSVG
                value={IOS_DOWNLOAD_URL}
                size={140}
                level="M"
                fgColor="#0F172A"
              />
            </div>
            <div className="flex items-center gap-1.5">
              <Smartphone className="size-4 text-[#0369A1]" />
              <span className="text-sm font-semibold text-[#0F172A]">iOS</span>
            </div>
            <p className="text-xs text-[#64748B] text-center leading-relaxed">
              扫描二维码查看
              <br />
              iOS 安装说明
            </p>
          </div>
        </div>
        <p className="text-xs text-[#A3A3A3] text-center">
          使用手机相机扫描二维码即可
        </p>
      </DialogContent>
    </Dialog>
  );
}
