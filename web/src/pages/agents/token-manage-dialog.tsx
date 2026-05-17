import { useOperateApiKey } from '@/components/api-service/hooks';
import CopyToClipboard from '@/components/copy-to-clipboard';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { IModalProps } from '@/interfaces/common';
import { formatDate } from '@/utils/date';
import { KeyRound, Trash2 } from 'lucide-react';

function TokenManageDialog({ hideModal }: IModalProps<any>) {
  const { createToken, removeToken, tokenList, listLoading, creatingLoading } =
    useOperateApiKey('', '');

  return (
    <Dialog open onOpenChange={hideModal}>
      <DialogContent className="max-w-[60vw]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="h-5 w-5" />
            API Token 管理
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Token 用于调用{' '}
            <code className="rounded bg-muted px-1 py-0.5 text-xs">
              /api/v1/agents/chat/completion
            </code>{' '}
            接口，永不过期。
          </p>
          {listLoading ? (
            <div className="flex justify-center py-8">Loading...</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Token</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead className="w-[100px]">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tokenList?.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={3}
                      className="text-center text-muted-foreground py-8"
                    >
                      暂无 Token，请点击下方按钮创建
                    </TableCell>
                  </TableRow>
                )}
                {tokenList?.map((tokenItem) => (
                  <TableRow key={tokenItem.token}>
                    <TableCell className="font-medium break-all">
                      {tokenItem.token}
                    </TableCell>
                    <TableCell>{formatDate(tokenItem.create_date)}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <CopyToClipboard text={tokenItem.token} />
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => removeToken(tokenItem.token)}
                        >
                          <Trash2 className="h-4 w-4 text-red-500" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <Button onClick={createToken} loading={creatingLoading}>
            创建新 Token
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default TokenManageDialog;
