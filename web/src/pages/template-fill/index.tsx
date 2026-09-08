import { useDebounce } from 'ahooks';
import { useState } from 'react';
import { useNavigate } from 'react-router';

import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import message from '@/components/ui/message';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  useBatchDeleteTemplateFill,
  useDeleteTemplateFill,
  useDisableTemplateFill,
  useListTemplateFill,
  usePublishTemplateFill,
  type TplTemplateItem,
} from '@/hooks/use-template-fill-request';
import { Routes } from '@/routes';
import { UploadWizard } from './upload-wizard';

const PAGE_SIZE = 20;

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  disabled: '已停用',
};

// 后台 AI 识别状态徽标（仅 draft 且非 none 时叠加展示）
const DETECT_BADGE: Partial<
  Record<
    NonNullable<TplTemplateItem['detect_status']>,
    { text: string; cls: string }
  >
> = {
  running: { text: 'AI 识别中', cls: 'text-state-warning' },
  done: { text: '已识别', cls: 'text-state-success' },
  failed: { text: 'AI 识别失败', cls: 'text-state-error' },
};

export default function TemplateFillPage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [wizardOpen, setWizardOpen] = useState(false);
  // 当前页选中的模板 id（翻页/筛选/删除后清空）
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // 待删除目标：单项为该模板；批量时为 null（配合 batchConfirmOpen）
  const [deleteTarget, setDeleteTarget] = useState<TplTemplateItem | null>(
    null,
  );
  const [batchConfirmOpen, setBatchConfirmOpen] = useState(false);
  // 300ms 防抖：输入过程中不触发列表请求
  const debouncedKeyword = useDebounce(keyword, { wait: 300 });
  const { data, isLoading } = useListTemplateFill({
    keyword: debouncedKeyword,
    status,
    page,
    size: PAGE_SIZE,
  });
  const publishMut = usePublishTemplateFill();
  const disableMut = useDisableTemplateFill();
  const deleteMut = useDeleteTemplateFill();
  const batchDeleteMut = useBatchDeleteTemplateFill();

  const items = data?.data ?? [];
  const total = data?.total_datasets;
  const hasMore =
    total != null ? page * PAGE_SIZE < total : items.length >= PAGE_SIZE;

  const handlePublish = (it: TplTemplateItem) => {
    publishMut.mutate(it.id, {
      onSuccess: () => message.success(`模板「${it.name}」已发布`),
      onError: (err) =>
        message.error(err instanceof Error ? err.message : '发布失败'),
    });
  };

  const handleDisable = (it: TplTemplateItem) => {
    disableMut.mutate(it.id, {
      onSuccess: () => message.success(`模板「${it.name}」已停用`),
      onError: (err) =>
        message.error(err instanceof Error ? err.message : '停用失败'),
    });
  };

  const toggleOne = (id: string, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  };

  const allChecked =
    items.length > 0 && items.every((it) => selected.has(it.id));

  const toggleAll = (checked: boolean) => {
    setSelected(checked ? new Set(items.map((it) => it.id)) : new Set());
  };

  const handleDeleteConfirm = () => {
    if (!deleteTarget) return;
    deleteMut.mutate(deleteTarget.id, {
      onSuccess: () => {
        message.success(`模板「${deleteTarget.name}」已删除`);
        setDeleteTarget(null);
        setSelected(new Set());
      },
      onError: (err) =>
        message.error(err instanceof Error ? err.message : '删除失败'),
    });
  };

  const handleBatchDeleteConfirm = () => {
    const ids = [...selected];
    if (ids.length === 0) return;
    batchDeleteMut.mutate(ids, {
      onSuccess: (res) => {
        const failCount = res.failed.length;
        if (failCount === 0) {
          message.success(`已删除 ${res.deleted.length} 个模板`);
        } else {
          message.warning(
            `删除 ${res.deleted.length} 个成功，${failCount} 个失败：${res.failed[0].message}${failCount > 1 ? ' 等' : ''}`,
          );
        }
        setBatchConfirmOpen(false);
        setSelected(new Set());
      },
      onError: (err) =>
        message.error(err instanceof Error ? err.message : '批量删除失败'),
    });
  };

  return (
    <Card className="bg-transparent border-none">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-2xl">范本库</CardTitle>
          <div className="flex gap-2">
            {selected.size > 0 && (
              <Button
                variant="outline"
                disabled={batchDeleteMut.isPending}
                onClick={() => setBatchConfirmOpen(true)}
              >
                批量删除({selected.size})
              </Button>
            )}
            <Button
              variant="outline"
              onClick={() => navigate(Routes.TemplateFillTasks)}
            >
              填写任务
            </Button>
            <Button onClick={() => setWizardOpen(true)}>上传模板</Button>
          </div>
        </div>
        <div className="flex gap-2">
          <Input
            placeholder="搜索模板名称"
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
              setSelected(new Set());
            }}
            className="w-64"
          />
          <Select
            value={status}
            onValueChange={(v) => {
              setStatus(v === 'all' ? '' : v);
              setPage(1);
              setSelected(new Set());
            }}
          >
            <SelectTrigger className="w-32">
              <SelectValue placeholder="全部状态" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="draft">草稿</SelectItem>
              <SelectItem value="published">已发布</SelectItem>
              <SelectItem value="disabled">已停用</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-b-transparent" />
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <p>暂无模板，点击右上角上传</p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={allChecked}
                    onCheckedChange={(v) => toggleAll(v === true)}
                    aria-label="全选本页"
                  />
                </TableHead>
                <TableHead>模板名称</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>版本</TableHead>
                <TableHead className="w-[240px]">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it) => (
                <TableRow key={it.id}>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      checked={selected.has(it.id)}
                      onCheckedChange={(v) => toggleOne(it.id, v === true)}
                      aria-label={`选择模板 ${it.name}`}
                    />
                  </TableCell>
                  <TableCell
                    className="cursor-pointer font-medium hover:text-text-primary"
                    onClick={() =>
                      navigate(`${Routes.TemplateFillDetail}/${it.id}`)
                    }
                  >
                    {it.name}
                  </TableCell>
                  <TableCell>
                    {it.file_type === 'docx' ? 'Word' : 'Excel'}
                  </TableCell>
                  <TableCell>
                    <span className="flex items-center gap-2">
                      {STATUS_LABEL[it.status]}
                      {it.status === 'draft' &&
                        it.detect_status &&
                        (DETECT_BADGE[it.detect_status] ? (
                          <span
                            className={`text-xs ${DETECT_BADGE[it.detect_status]!.cls}`}
                            title={it.detect_error || undefined}
                          >
                            {DETECT_BADGE[it.detect_status]!.text}
                          </span>
                        ) : null)}
                    </span>
                  </TableCell>
                  <TableCell>v{it.latest_version}</TableCell>
                  <TableCell className="space-x-2">
                    {(it.status === 'draft' || it.status === 'disabled') && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={publishMut.isPending}
                        onClick={() => handlePublish(it)}
                      >
                        发布
                      </Button>
                    )}
                    {it.status === 'published' && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={disableMut.isPending}
                        onClick={() => handleDisable(it)}
                      >
                        停用
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        navigate(`${Routes.TemplateFillDetail}/${it.id}`)
                      }
                    >
                      详情
                    </Button>
                    {(it.status === 'draft' || it.status === 'disabled') && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-state-error hover:text-state-error"
                        disabled={deleteMut.isPending}
                        onClick={() => setDeleteTarget(it)}
                      >
                        删除
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!hasMore}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </Button>
        </div>
      </CardContent>
      <ConfirmDeleteDialog
        open={deleteTarget != null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        onOk={handleDeleteConfirm}
        title="删除模板"
        content={{
          title: `确定删除模板「${deleteTarget?.name ?? ''}」？删除后不可恢复。`,
        }}
        cancelButtonText="取消"
        okButtonText="删除"
      />
      <ConfirmDeleteDialog
        open={batchConfirmOpen}
        onOpenChange={setBatchConfirmOpen}
        onOk={handleBatchDeleteConfirm}
        title="批量删除模板"
        content={{
          title: `确定删除选中的 ${selected.size} 个模板？仅草稿/已停用且无填写任务记录的模板会被删除，其余自动跳过。`,
        }}
        cancelButtonText="取消"
        okButtonText="删除"
      />
      {/* 上传后 AI 识别在后台执行，留在列表页看识别进度，不跳详情 */}
      <UploadWizard open={wizardOpen} onOpenChange={setWizardOpen} />
    </Card>
  );
}
