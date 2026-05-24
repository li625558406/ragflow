import { useEffect, useRef } from 'react';
import { UseFormReturn, useWatch } from 'react-hook-form';
import useGraphStore from '../store';

export function useWatchFormChange(
  id?: string,
  form?: UseFormReturn<any>,
  enableReplacement = false,
) {
  let values = useWatch({ control: form?.control });
  const { updateNodeForm, replaceNodeForm } = useGraphStore((state) => state);
  const isInitialMount = useRef(true);

  useEffect(() => {
    // Skip the first render to avoid overwriting stored data with potentially incomplete initial values
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    // Manually triggered form updates are synchronized to the canvas
    if (id) {
      values = form?.getValues() || {};
      const nextValues: any = values;

      (enableReplacement ? replaceNodeForm : updateNodeForm)(id, nextValues);
    }
  }, [form?.formState.isDirty, id, updateNodeForm, values]);
}
