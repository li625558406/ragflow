import { useDebounce } from 'ahooks';
import { useState } from 'react';
import { useNavigate } from 'react-router';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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

export default function TemplateFillPage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [wizardOpen, setWizardOpen] = useState(false);
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

  return (
    <Card className="bg-transparent border-none">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-2xl">范本库</CardTitle>
          <div className="flex gap-2">
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
            }}
            className="w-64"
          />
          <Select
            value={status}
            onValueChange={(v) => {
              setStatus(v === 'all' ? '' : v);
              setPage(1);
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
                <TableHead>模板名称</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>版本</TableHead>
                <TableHead className="w-[200px]">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it) => (
                <TableRow key={it.id}>
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
                  <TableCell>{STATUS_LABEL[it.status]}</TableCell>
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
      <UploadWizard
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        onSaved={(id) => {
          setWizardOpen(false);
          navigate(`${Routes.TemplateFillDetail}/${id}`);
        }}
      />
    </Card>
  );
}
