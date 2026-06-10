import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { useTranslation } from 'react-i18next';

interface IClipDialogProps {
  kbId: string;
  kbName: string;
  children: React.ReactNode;
}

const API_BASE = window.location.origin + '/api/v1';

export default function ClipDialog({
  kbId,
  kbName,
  children,
}: IClipDialogProps) {
  const { t } = useTranslation();

  return (
    <Dialog>
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent className="sm:max-w-[540px] max-h-[85vh] overflow-y-auto overflow-x-hidden">
        <DialogHeader>
          <DialogTitle>{t('knowledgeDetails.clip.title')}</DialogTitle>
          <DialogDescription>
            {t('knowledgeDetails.clip.description')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 mt-4">
          {/* KB info */}
          <div className="rounded-md bg-muted p-3 text-sm space-y-1">
            <div className="flex justify-between">
              <span className="text-muted-foreground">API</span>
              <code className="text-xs break-all">
                {API_BASE}/kb/{kbId}/clip
              </code>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">
                {t('knowledgeDetails.clip.kbName')}
              </span>
              <span className="font-medium">{kbName}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">ID</span>
              <code className="text-xs">{kbId}</code>
            </div>
          </div>

          {/* Plugin section */}
          <div>
            <h4 className="font-medium mb-2">
              {t('knowledgeDetails.clip.extensionTitle')}
            </h4>
            <p className="text-xs text-muted-foreground mb-2">
              {t('knowledgeDetails.clip.extensionDesc')}
            </p>
            <a
              href={`${API_BASE}/extension/download`}
              className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 no-underline"
            >
              {t('knowledgeDetails.clip.downloadExtension')}
            </a>
            <p className="text-xs text-muted-foreground mt-2">
              {t('knowledgeDetails.clip.unzipHint')}
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
