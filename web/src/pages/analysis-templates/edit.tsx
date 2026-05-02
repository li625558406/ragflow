import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  useGetAnalysisTemplate,
  useCreateAnalysisTemplate,
  useUpdateAnalysisTemplate,
} from '@/hooks/use-analysis-template-request';
import { useSelectLlmList } from '@/hooks/use-llm-request';
import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useMemo } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import { z } from 'zod';
import { Routes } from '@/routes';

const DOC_TYPES = [
  { value: 'general', label: '通用文档' },
  { value: 'bid', label: '招投标文件' },
  { value: 'contract', label: '合同文件' },
  { value: 'report', label: '报告文件' },
  { value: 'paper', label: '论文' },
  { value: 'resume', label: '简历' },
];

const ANALYSIS_DIMENSIONS = [
  { value: 'summary', label: '摘要' },
  { value: 'key_points', label: '关键要点' },
  { value: 'entities', label: '实体提取' },
  { value: 'risks', label: '风险分析' },
  { value: 'requirements', label: '需求提取' },
  { value: 'timeline', label: '时间线' },
  { value: 'financial', label: '财务分析' },
  { value: 'legal', label: '法律条款' },
];

const formSchema = z.object({
  name: z.string().min(1, '模板名称不能为空'),
  doc_type: z.string().min(1, '请选择文档类型'),
  dimensions: z.array(z.string()).min(1, '请至少选择一个分析维度'),
  prompt_template: z.string().optional(),
  llm_id: z.string().optional(),
});

type FormValues = z.infer<typeof formSchema>;

export default function AnalysisTemplateEditPage() {
  const { templateId } = useParams<{ templateId: string }>();
  const isEditing = !!templateId;
  const { t } = useTranslation('translation', { keyPrefix: 'analysisTemplate' });
  const navigate = useNavigate();

  const { data: template, isLoading } = useGetAnalysisTemplate(templateId || '');
  const { mutate: createTemplate, isPending: isCreating, error: createError } = useCreateAnalysisTemplate();
  const { mutate: updateTemplate, isPending: isUpdating, error: updateError } = useUpdateAnalysisTemplate();

  // 获取用户的 Chat 模型列表
  const { myLlmList, loading: llmLoading } = useSelectLlmList();

  // 过滤出 Chat 模型
  const chatModels = useMemo(() => {
    const models: { id: string; name: string; factory: string }[] = [];
    myLlmList?.forEach((factory: any) => {
      factory.llm?.forEach((model: any) => {
        if (model.model_type === 'chat' || !model.model_type) {
          models.push({
            id: model.llm_id || `${factory.name}/${model.name}`,
            name: model.name,
            factory: factory.name,
          });
        }
      });
    });
    return models;
  }, [myLlmList]);

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: '',
      doc_type: '',
      dimensions: [],
      prompt_template: '',
      llm_id: '',
    },
  });

  useEffect(() => {
    if (template) {
      // 转换 llm_id 格式：从 "name@factory" 转换为 "factory/name"（用于前端下拉框）
      let convertedLlmId = template.llm_id || '';
      if (convertedLlmId && convertedLlmId.includes('@')) {
        const [name, factory] = convertedLlmId.split('@');
        convertedLlmId = `${factory}/${name}`;
      }

      form.reset({
        name: template.name,
        doc_type: template.doc_type,
        dimensions: template.dimensions || [],
        // API 返回的是 prompt_templates（复数）
        prompt_template: template.prompt_templates || template.prompt_template || '',
        llm_id: convertedLlmId,
      });
    }
  }, [template, form]);

  // 当 chatModels 加载完成后，确保 llm_id 回显正确
  useEffect(() => {
    if (!llmLoading && chatModels.length > 0 && template?.llm_id) {
      // 使用 setTimeout 确保在下一个事件循环中执行，避免渲染冲突
      setTimeout(() => {
        const currentLlmId = form.getValues('llm_id');
        const exists = chatModels.some(m => m.id === currentLlmId);
        if (exists) {
          // 如果模型在列表中，重新设置值以触发 Select 更新
          form.setValue('llm_id', currentLlmId, { shouldValidate: false });
        } else {
          // 如果模型不在列表中，清空选择
          form.setValue('llm_id', '', { shouldValidate: false });
        }
      }, 0);
    }
  }, [chatModels, llmLoading, template, form]);

  const onSubmit = (values: FormValues) => {
    // 转换 llm_id 格式：从 "factory/name" 转换为 "name@factory"
    let convertedLlmId = values.llm_id || '';
    if (convertedLlmId && convertedLlmId.includes('/')) {
      const [factory, name] = convertedLlmId.split('/');
      convertedLlmId = `${name}@${factory}`;
    }

    // 将 prompt_template 改为 prompt_templates（API 期望的字段名）
    const submitValues = {
      name: values.name,
      doc_type: values.doc_type,
      dimensions: values.dimensions || [],
      prompt_templates: values.prompt_template || '',
      llm_id: convertedLlmId,
    };
    if (isEditing && templateId) {
      updateTemplate(
        { templateId, params: submitValues },
        {
          onSuccess: () => {
            navigate(`${Routes.UserSetting}${Routes.AnalysisTemplates}`);
          },
          onError: (error) => {
            console.error('Update error:', error);
          },
        }
      );
    } else {
      createTemplate(submitValues, {
        onSuccess: () => {
          navigate(`${Routes.UserSetting}${Routes.AnalysisTemplates}`);
        },
        onError: (error) => {
          console.error('Create error:', error);
        },
      });
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[50vh]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">
          {isEditing ? t('edit') : t('create')}
        </h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t('basicInfo')}</CardTitle>
          <CardDescription>{t('analysisConfig')}</CardDescription>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel required>{t('templateName')}</FormLabel>
                    <FormControl>
                      <Input
                        placeholder={t('templateNamePlaceholder')}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="doc_type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel required>{t('docType')}</FormLabel>
                    <Select onValueChange={field.onChange} value={field.value}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t('docTypePlaceholder')} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {DOC_TYPES.map((type) => (
                          <SelectItem key={type.value} value={type.value}>
                            {type.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="llm_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Chat 模型</FormLabel>
                    {llmLoading ? (
                      <div className="text-sm text-muted-foreground">加载模型列表中...</div>
                    ) : (
                      <Select onValueChange={field.onChange} value={field.value || undefined}>
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue placeholder="选择分析使用的 Chat 模型（可选）" />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {chatModels.map((model) => (
                            <SelectItem key={model.id} value={model.id}>
                              {model.factory} - {model.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                    <p className="text-xs text-muted-foreground">
                      不选择则使用系统默认 Chat 模型
                    </p>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="dimensions"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel required>{t('dimensions')}</FormLabel>
                    <div className="flex flex-wrap gap-2">
                      {ANALYSIS_DIMENSIONS.map((dim) => (
                        <Button
                          key={dim.value}
                          type="button"
                          size="sm"
                          variant={
                            field.value?.includes(dim.value)
                              ? 'default'
                              : 'outline'
                          }
                          onClick={() => {
                            const current = field.value || [];
                            if (current.includes(dim.value)) {
                              field.onChange(
                                current.filter((v) => v !== dim.value)
                              );
                            } else {
                              field.onChange([...current, dim.value]);
                            }
                          }}
                        >
                          {dim.label}
                        </Button>
                      ))}
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="prompt_template"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t('promptTemplate')}</FormLabel>
                    <FormControl>
                      <Textarea
                        placeholder={t('promptTemplatePlaceholder')}
                        className="min-h-[120px]"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="flex gap-3 justify-end">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => navigate(`${Routes.UserSetting}${Routes.AnalysisTemplates}`)}
                >
                  取消
                </Button>
                <Button
                  type="submit"
                  disabled={isCreating || isUpdating}
                >
                  {isCreating || isUpdating ? '保存中...' : '保存'}
                </Button>
              </div>
              {(createError || updateError) && (
                <div className="text-red-500 text-sm mt-2">
                  {createError?.message || updateError?.message || '保存失败'}
                </div>
              )}
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
}
