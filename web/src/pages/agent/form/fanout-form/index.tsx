import { LargeModelFormField } from '@/components/large-model-form-field';
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
import { Separator } from '@/components/ui/separator';
import { zodResolver } from '@hookform/resolvers/zod';
import { memo } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';
import { ArrayFields, initialFanOutValues } from '../../constant';
import { useWatchFormChange } from '../../hooks/use-watch-form-change';
import { INextOperatorForm } from '../../interface';
import { buildOutputList } from '../../utils/build-output-list';
import { AgentTools } from '../agent-form/agent-tools';
import { FormWrapper } from '../components/form-wrapper';
import { Output } from '../components/output';
import { PromptEditor } from '../components/prompt-editor';
import { QueryVariable } from '../components/query-variable';

const FormSchema = z.object({
  items_ref: z.string().optional(),
  llm_id: z.string().optional(),
  system_prompt: z.string().optional(),
  prompt_template: z.string().optional(),
  max_concurrency: z.number().min(1).max(50).optional(),
  error_strategy: z.enum(['skip', 'stop']).optional(),
});

const outputList = buildOutputList(initialFanOutValues.outputs);

function FanOutForm({ node }: INextOperatorForm) {
  const { t } = useTranslation();

  const defaultValues = node?.data?.form ?? initialFanOutValues;

  const form = useForm<z.infer<typeof FormSchema>>({
    defaultValues,
    resolver: zodResolver(FormSchema),
  });

  useWatchFormChange(node?.id, form);

  return (
    <Form {...form}>
      <FormWrapper>
        <QueryVariable
          name="items_ref"
          types={ArrayFields as any[]}
        ></QueryVariable>
        <LargeModelFormField></LargeModelFormField>
        <FormField
          control={form.control}
          name="system_prompt"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('flow.systemPrompt')}</FormLabel>
              <FormControl>
                <PromptEditor {...field} showToolbar={false}></PromptEditor>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="prompt_template"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('flow.promptTemplate')}</FormLabel>
              <FormControl>
                <PromptEditor {...field} showToolbar={false}></PromptEditor>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="max_concurrency"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('flow.maxConcurrency')}</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type="number"
                  min={1}
                  max={50}
                  onChange={(e) => field.onChange(Number(e.target.value))}
                ></Input>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="error_strategy"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('flow.errorStrategy')}</FormLabel>
              <Select
                onValueChange={field.onChange}
                defaultValue={field.value}
                value={field.value}
              >
                <FormControl>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  <SelectItem value="skip">{t('flow.skip')}</SelectItem>
                  <SelectItem value="stop">{t('flow.stop')}</SelectItem>
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
        <Separator />
        <AgentTools />
      </FormWrapper>
      <div className="p-5">
        <Output list={outputList}></Output>
      </div>
    </Form>
  );
}

export default memo(FanOutForm);
