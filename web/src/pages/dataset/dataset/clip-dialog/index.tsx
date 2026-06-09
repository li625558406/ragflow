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

function buildBookmarklet(kbId: string): string {
  const code = `(function(){
    var d=document;
    var t=d.title;
    var u=d.location.href;
    var c=document.body.innerText||'';
    c=c.substring(0,8000);
    var x=new XMLHttpRequest();
    x.open('POST','${API_BASE}/kb/${kbId}/clip');
    x.setRequestHeader('Content-Type','application/json');
    x.setRequestHeader('Authorization','YOUR_API_TOKEN');
    x.onload=function(){
      if(x.status!==200){alert('HTTP Error: '+x.status);return}
      try{
        var r=JSON.parse(x.responseText);
        if(r.code===0){alert('采集成功! doc_id: '+r.data.doc_id)}
        else{alert('采集失败: '+r.message)}
      }catch(e){alert('解析响应失败')}
    };
    x.onerror=function(){alert('网络错误')};
    x.send(JSON.stringify({title:t,url:u,content:c}));
  })();`.replace(/\s+/g, ' ');

  return `javascript:${code}`;
}

export default function ClipDialog({
  kbId,
  kbName,
  children,
}: IClipDialogProps) {
  const { t } = useTranslation();

  const bookmarklet = buildBookmarklet(kbId);

  return (
    <Dialog>
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent className="sm:max-w-[540px]">
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
              <code className="text-xs">
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

          {/* Bookmarklet */}
          <div>
            <h4 className="font-medium mb-2">
              {t('knowledgeDetails.clip.bookmarkletTitle')}
            </h4>
            <p className="text-xs text-muted-foreground mb-2">
              {t('knowledgeDetails.clip.bookmarkletDesc')}
            </p>
            <a
              href={bookmarklet}
              className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 no-underline"
              title={t('knowledgeDetails.clip.dragToBookmarks')}
            >
              📎 {t('knowledgeDetails.clip.clipToKB')}
            </a>
            <p className="text-xs text-muted-foreground mt-2">
              {t('knowledgeDetails.clip.replaceTokenHint')}
            </p>
          </div>

          {/* Plugin section */}
          <div>
            <h4 className="font-medium mb-2">
              {t('knowledgeDetails.clip.extensionTitle')}
            </h4>
            <p className="text-xs text-muted-foreground mb-2">
              {t('knowledgeDetails.clip.extensionDesc')}
            </p>
          </div>

          {/* CURL example */}
          <div>
            <h4 className="font-medium mb-2">
              {t('knowledgeDetails.clip.curlTitle')}
            </h4>
            <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
              {`curl -X POST '${API_BASE}/kb/${kbId}/clip' \\
  -H 'Authorization: YOUR_API_TOKEN' \\
  -H 'Content-Type: application/json' \\
  -d '{"title":"My Page","url":"https://...","content":"..."}'`}
            </pre>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
