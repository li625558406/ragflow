import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import message from '@/components/ui/message';
import {
  fetchCrawlerState,
  updateCrawlerState,
} from '@/services/scheduled-task-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RotateCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  taskId: string;
  taskName: string;
  visible: boolean;
  hideModal: () => void;
}

export function ScheduledTaskStateDialog({
  taskId,
  taskName,
  visible,
  hideModal,
}: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [editText, setEditText] = useState('');
  const [parseError, setParseError] = useState<string | null>(null);

  const {
    data,
    isFetching: loading,
    refetch,
  } = useQuery({
    queryKey: ['scheduledTaskState', taskId],
    enabled: visible && !!taskId,
    queryFn: async () => {
      const { data: res } = await fetchCrawlerState(taskId);
      return res?.data ?? { processed_urls: [] };
    },
  });

  useEffect(() => {
    if (data) {
      setEditText(JSON.stringify(data, null, 2));
      setParseError(null);
    }
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: async (content: string) => {
      const parsed = JSON.parse(content);
      const { data: res } = await updateCrawlerState(taskId, parsed);
      return res;
    },
    onSuccess: () => {
      message.success(t('scheduledTasks.crawlerStateSaveSuccess'));
      queryClient.invalidateQueries({
        queryKey: ['scheduledTaskState', taskId],
      });
      hideModal();
    },
    onError: () => {
      message.error(t('scheduledTasks.crawlerStateSaveFailed'));
    },
  });

  const handleSave = () => {
    try {
      JSON.parse(editText);
      setParseError(null);
    } catch {
      setParseError(t('scheduledTasks.crawlerStateInvalidJson'));
      return;
    }
    saveMutation.mutate(editText);
  };

  return (
    <Dialog open={visible} onOpenChange={hideModal}>
      <DialogContent className="sm:max-w-[700px] max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {t('scheduledTasks.crawlerState')} - {taskName}
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => refetch()}
              disabled={loading}
              title="Refresh"
            >
              <RotateCw
                className={`size-3.5 ${loading ? 'animate-spin' : ''}`}
              />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 min-h-0">
          <textarea
            className={`w-full h-[400px] font-mono text-sm p-3 border rounded resize-none focus:outline-none focus:ring-2 focus:ring-primary ${
              parseError ? 'border-red-500' : 'border-gray-300'
            }`}
            value={editText}
            onChange={(e) => {
              setEditText(e.target.value);
              setParseError(null);
            }}
            spellCheck={false}
          />
          {parseError && (
            <p className="text-red-500 text-xs mt-1">{parseError}</p>
          )}
        </div>

        <div className="pt-3 border-t mt-3 flex items-center justify-end gap-2">
          <Button variant="outline" onClick={hideModal}>
            {t('common.cancel')}
          </Button>
          <Button onClick={handleSave} disabled={saveMutation.isPending}>
            {t('common.save')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
