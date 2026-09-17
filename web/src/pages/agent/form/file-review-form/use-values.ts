import { RAGFlowNodeType } from '@/interfaces/database/agent';
import { isEmpty } from 'lodash';
import { useMemo } from 'react';
import { initialFileReviewValues } from '../../constant';

export function useValues(node?: RAGFlowNodeType) {
  const defaultValues = useMemo(
    () => ({
      ...initialFileReviewValues,
    }),
    [],
  );

  return useMemo(() => {
    const formData = node?.data?.form;

    if (isEmpty(formData)) {
      return defaultValues;
    }

    return formData;
  }, [defaultValues, node?.data?.form]);
}
