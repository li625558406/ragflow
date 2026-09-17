import { KnowledgeBaseFormField } from '@/components/knowledge-base-item';
import { RAGFlowFormItem } from '@/components/ragflow-form';
import { Form } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { RAGFlowSelect } from '@/components/ui/select';
import { useFileReviewTemplates } from '@/hooks/use-file-review-request';
import { zodResolver } from '@hookform/resolvers/zod';
import { PropsWithChildren, memo, useMemo } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';
import { useWatchFormChange } from '../../hooks/use-watch-form-change';
import { INextOperatorForm } from '../../interface';
import { FormWrapper } from '../components/form-wrapper';
import { Output } from '../components/output';
import { PromptEditor } from '../components/prompt-editor';
import { useValues } from './use-values';

// 字段名与后端 agent/component/file_review.py::FileReviewParam 逐字一致。
// max_rounds 后端不消费（轮次上限权威是服务端 MAX_FIX_ROUNDS），仅界面展示，
// 故校验失败时回落 3，不让它挡住表单提交。
export const FileReviewFormSchema = z.object({
  file_id: z.string().optional(),
  template_id: z.string().optional(),
  custom_prompt: z.string().optional(),
  dataset_ids: z.array(z.string()),
  max_rounds: z.coerce.number().int().min(1).max(10).catch(3),
  outputs: z.object({
    task_id: z.object({ type: z.string() }),
    round_id: z.object({ type: z.string() }),
    content: z.object({ type: z.string() }),
  }),
});

const FILE_ID_HINT =
  '可直接填写上传文件 ID（/documents/upload 返回的 id），也可填写 {begin@review_file_id} 形式的变量引用；' +
  '留空时自动读取「开始」节点输出的 review_file_id（引用展开失败时同样回退扫「开始」节点输出）。';

function FieldHint({ children }: PropsWithChildren) {
  return <p className="text-xs text-text-tertiary">{children}</p>;
}

function FileReviewForm({ node }: INextOperatorForm) {
  const { t } = useTranslation();
  const values = useValues(node);
  // 审核模板列表（5 套预置模板已在 DB，接口启用即拉一次，无轮询）
  const { data: templatesData } = useFileReviewTemplates();

  const templates = useMemo(
    () => templatesData?.data?.templates ?? [],
    [templatesData],
  );

  const outputList = useMemo(() => {
    return [
      { title: 'task_id', type: 'string' },
      { title: 'round_id', type: 'string' },
      { title: 'content', type: 'string' },
    ];
  }, []);

  const form = useForm({
    defaultValues: values,
    resolver: zodResolver(FileReviewFormSchema),
  });

  useWatchFormChange(node?.id, form);

  return (
    <Form {...form}>
      <FormWrapper>
        <div className="space-y-1">
          <RAGFlowFormItem name="file_id" label={t('flow.fileReviewFileId')}>
            <PromptEditor multiLine={false} showToolbar={false}></PromptEditor>
          </RAGFlowFormItem>
          <FieldHint>{FILE_ID_HINT}</FieldHint>
        </div>

        <div className="space-y-1">
          <RAGFlowFormItem
            name="template_id"
            label={t('flow.fileReviewTemplate')}
          >
            {(field) => {
              const options = templates.map((x) => ({
                label: x.name,
                value: x.id,
              }));
              // 已存值不在列表里（模板停用 / 接口未返回）时补一个占位项，避免值不可见
              if (
                field.value &&
                !options.some((x) => x.value === field.value)
              ) {
                options.unshift({ label: field.value, value: field.value });
              }
              return (
                <RAGFlowSelect
                  {...field}
                  allowClear
                  options={options}
                  placeholder="留空使用默认模板"
                  onChange={(val) => field.onChange(val ?? '')}
                ></RAGFlowSelect>
              );
            }}
          </RAGFlowFormItem>
          <FieldHint>留空时使用后端默认审核模板。</FieldHint>
        </div>

        <KnowledgeBaseFormField></KnowledgeBaseFormField>

        <RAGFlowFormItem
          name="custom_prompt"
          label={t('flow.fileReviewCustomPrompt')}
        >
          <PromptEditor placeholder="补充本次审核的额外要求，留空则按模板默认要求审核"></PromptEditor>
        </RAGFlowFormItem>

        <div className="space-y-1">
          <RAGFlowFormItem
            name="max_rounds"
            label={t('flow.fileReviewMaxRounds')}
          >
            {(field) => (
              <Input
                {...field}
                type="number"
                min={1}
                max={10}
                onChange={(e) => field.onChange(e.target.value)}
                onBlur={(e) => {
                  field.onBlur();
                  const value = Number(e.target.value);
                  field.onChange(
                    Number.isInteger(value) && value >= 1 && value <= 10
                      ? value
                      : 3,
                  );
                }}
              />
            )}
          </RAGFlowFormItem>
          <FieldHint>
            仅用于界面展示，实际轮次上限由服务端控制（默认 3 轮）。
          </FieldHint>
        </div>

        <Output list={outputList}></Output>
      </FormWrapper>
    </Form>
  );
}

export default memo(FileReviewForm);
