import { RAGFlowNodeType } from '@/interfaces/database/agent';
import { isEmpty, omit } from 'lodash';
import { useMemo } from 'react';

function convertRefOutputsToArray(outputs: Record<string, any>) {
  return Object.entries(outputs)
    .filter(([, value]) => 'ref' in value)
    .map(([key, value]) => ({
      name: key,
      ref: value.ref,
      type: value.type,
    }));
}

export function useFormValues(
  defaultValues: Record<string, any>,
  node?: RAGFlowNodeType,
) {
  const values = useMemo(() => {
    const formData = node?.data?.form;

    if (isEmpty(formData)) {
      return { ...omit(defaultValues, 'outputs'), outputs: [] };
    }

    return {
      ...omit(formData, 'outputs'),
      outputs: convertRefOutputsToArray(formData.outputs),
    };
  }, [defaultValues, node?.data?.form]);

  return values;
}
