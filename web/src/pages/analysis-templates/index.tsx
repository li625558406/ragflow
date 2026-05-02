import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import {
  useListAnalysisTemplates,
  useDeleteAnalysisTemplate,
} from '@/hooks/use-analysis-template-request';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Plus, Pencil, Trash2 } from 'lucide-react';
import { Routes } from '@/routes';

export default function AnalysisTemplatesPage() {
  const { t } = useTranslation('translation', { keyPrefix: 'analysisTemplate' });
  const { data, isLoading } = useListAnalysisTemplates({});
  const { mutate: deleteTemplate } = useDeleteAnalysisTemplate();
  const navigate = useNavigate();

  const templates = data?.data || [];

  return (
    <div className="p-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold">{t('title')}</h1>
          <p className="text-muted-foreground mt-1">
            {t('list')}
          </p>
        </div>
        <Button onClick={() => navigate(`${Routes.UserSetting}${Routes.AnalysisTemplateEdit}/create`)}>
          <Plus className="size-4 mr-2" />
          {t('create')}
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
            </div>
          ) : templates.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <p>{t('noTemplates')}</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('templateName')}</TableHead>
                  <TableHead>{t('docType')}</TableHead>
                  <TableHead>{t('dimensions')}</TableHead>
                  <TableHead className="w-[100px]">{t('type')}</TableHead>
                  <TableHead className="w-[120px]">{t('action')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {templates.map((template) => (
                  <TableRow key={template.id}>
                    <TableCell className="font-medium">{template.name}</TableCell>
                    <TableCell>{template.doc_type}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {template.dimensions?.slice(0, 3).map((dim, idx) => (
                          <Badge key={idx} variant="secondary" className="text-xs">
                            {dim}
                          </Badge>
                        ))}
                        {(template.dimensions?.length || 0) > 3 && (
                          <Badge variant="outline" className="text-xs">
                            +{(template.dimensions?.length || 0) - 3}
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={template.is_system ? 'default' : 'secondary'}>
                        {template.is_system ? t('systemTemplate') : t('customTemplate')}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          disabled={template.is_system}
                          onClick={() =>
                            navigate(`${Routes.UserSetting}${Routes.AnalysisTemplateEdit}/${template.id}/edit`)
                          }
                          title={template.is_system ? t('cannotEditSystem') : t('edit')}
                        >
                          <Pencil className="size-[1em]" />
                        </Button>
                        <ConfirmDeleteDialog
                          onOk={() => deleteTemplate(template.id)}
                          disabled={template.is_system}
                          title={t('deleteConfirm')}
                        >
                          <Button
                            size="icon-xs"
                            variant="ghost"
                            disabled={template.is_system}
                            title={template.is_system ? t('cannotDeleteSystem') : t('delete')}
                          >
                            <Trash2 className="size-[1em]" />
                          </Button>
                        </ConfirmDeleteDialog>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
