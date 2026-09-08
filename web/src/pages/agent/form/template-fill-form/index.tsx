import { KnowledgeBaseFormField } from '@/components/knowledge-base-item';
import { RAGFlowFormItem } from '@/components/ragflow-form';
import { Form } from '@/components/ui/form';
import { zodResolver } from '@hookform/resolvers/zod';
import { memo, useMemo } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';
import { useWatchFormChange } from '../../hooks/use-watch-form-change';
import { INextOperatorForm } from '../../interface';
import { FormWrapper } from '../components/form-wrapper';
import { Output } from '../components/output';
import { PromptEditor } from '../components/prompt-editor';
import { useValues } from './use-values';

export const TemplateFillFormSchema = z.object({
  query: z.string().optional(),
  dataset_ids: z.array(z.string()),
  outputs: z.object({
    content: z.object({ type: z.string() }),
    download: z.object({ type: z.string() }),
  }),
});

function TemplateFillForm({ node }: INextOperatorForm) {
  const { t } = useTranslation();
  const values = useValues(node);

  const outputList = useMemo(() => {
    return [
      { title: 'content', type: 'string' },
      { title: 'download', type: 'string' },
    ];
  }, []);

  const form = useForm({
    defaultValues: values,
    resolver: zodResolver(TemplateFillFormSchema),
  });

  useWatchFormChange(node?.id, form);

  return (
    <Form {...form}>
      <FormWrapper>
        <RAGFlowFormItem name="query" label={t('flow.templateFillQuery')}>
          <PromptEditor></PromptEditor>
        </RAGFlowFormItem>
        <KnowledgeBaseFormField></KnowledgeBaseFormField>
        <Output list={outputList}></Output>
      </FormWrapper>
    </Form>
  );
}

export default memo(TemplateFillForm);
