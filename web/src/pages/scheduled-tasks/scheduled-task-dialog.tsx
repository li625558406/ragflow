import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  RAGFlowSelect,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import {
  fetchKnowledgeBases,
  fetchLlmModels,
} from '@/services/scheduled-task-service';
import request from '@/utils/next-request';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface ScheduledTaskForm {
  name: string;
  description: string;
  script_path: string;
  script_args: string;
  schedule_type: string;
  cron_expression: string;
  interval_seconds: number;
  enabled: boolean;
  timeout: number;
  max_retries: number;
  target_url: string;
  llm_id: string;
  llm_model_name: string;
  kb_id: string;
  access_token: string;
}

interface Props {
  visible: boolean;
  editingTask?: Record<string, any> | null;
  loading: boolean;
  hideModal: () => void;
  onOk: (values: Record<string, any>) => Promise<boolean>;
}

export function ScheduledTaskDialog({
  visible,
  editingTask,
  loading,
  hideModal,
  onOk,
}: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<ScheduledTaskForm>({
    name: '',
    description: '',
    script_path: '',
    script_args: '',
    schedule_type: 'interval',
    cron_expression: '',
    interval_seconds: 3600,
    enabled: true,
    timeout: 3600,
    max_retries: 0,
    target_url: '',
    llm_id: '',
    llm_model_name: '',
    kb_id: '',
    access_token: '',
  });

  const [llmModels, setLlmModels] = useState<any[]>([]);
  const [kbList, setKbList] = useState<any[]>([]);

  const [provinceOptions, setProvinceOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [cityOptions, setCityOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [selectedProvince, setSelectedProvince] = useState<string>('');
  const [selectedCity, setSelectedCity] = useState<string>('');

  // Load provinces on mount
  useEffect(() => {
    request
      .get('/api/v1/bid/areas', { params: { parent_code: '0', level: 1 } })
      .then((res: any) => {
        const list = res?.data?.data ?? [];
        setProvinceOptions(
          list.map((a: any) => ({ label: a.name, value: a.code })),
        );
      })
      .catch(() => {});
  }, []);

  // Load cities when province changes
  useEffect(() => {
    if (!selectedProvince) {
      setCityOptions([]);
      return;
    }
    request
      .get('/api/v1/bid/areas', { params: { parent_code: selectedProvince } })
      .then((res: any) => {
        const list = res?.data?.data ?? [];
        setCityOptions(
          list.map((a: any) => ({ label: a.name, value: a.code })),
        );
      })
      .catch(() => {
        setCityOptions([]);
      });
  }, [selectedProvince]);

  // Parse province/city from editing task's script_args
  useEffect(() => {
    if (editingTask?.script_args) {
      try {
        const args = JSON.parse(editingTask.script_args);
        if (args.province_code) setSelectedProvince(args.province_code);
        if (args.city_code) setSelectedCity(args.city_code);
      } catch {
        // ignore
      }
    } else {
      setSelectedProvince('');
      setSelectedCity('');
    }
  }, [editingTask, visible]);

  // Build script_args JSON from province/city + existing args
  const buildScriptArgs = useCallback(() => {
    try {
      const base = form.script_args ? JSON.parse(form.script_args) : {};
      return JSON.stringify({
        ...base,
        province_code: selectedProvince || undefined,
        city_code: selectedCity || undefined,
      });
    } catch {
      return JSON.stringify({
        province_code: selectedProvince || undefined,
        city_code: selectedCity || undefined,
      });
    }
  }, [form.script_args, selectedProvince, selectedCity]);

  useEffect(() => {
    if (!visible) return;
    fetchLlmModels().then(setLlmModels);
    fetchKnowledgeBases().then(setKbList);
  }, [visible]);

  /** Unique factory names derived from the flat model list */
  const factoryOptions = useMemo(() => {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const m of llmModels) {
      if (!seen.has(m.fid)) {
        seen.add(m.fid);
        result.push(m.fid);
      }
    }
    return result;
  }, [llmModels]);

  /** Model names filtered by the selected factory */
  const modelOptions = useMemo(
    () => llmModels.filter((m) => m.fid === form.llm_id),
    [llmModels, form.llm_id],
  );

  useEffect(() => {
    if (editingTask) {
      setForm({
        name: editingTask.name ?? '',
        description: editingTask.description ?? '',
        script_path: editingTask.script_path ?? '',
        script_args: editingTask.script_args ?? '',
        schedule_type: editingTask.schedule_type ?? 'interval',
        cron_expression: editingTask.cron_expression ?? '',
        interval_seconds: editingTask.interval_seconds ?? 3600,
        enabled: editingTask.enabled ?? true,
        timeout: editingTask.timeout ?? 3600,
        max_retries: editingTask.max_retries ?? 0,
        target_url: editingTask.target_url ?? '',
        llm_id: editingTask.llm_id ?? '',
        llm_model_name: editingTask.llm_model_name ?? '',
        kb_id: editingTask.kb_id ?? '',
        access_token: editingTask.access_token ?? '',
      });
    } else {
      setForm({
        name: '',
        description: '',
        script_path: '',
        script_args: '',
        schedule_type: 'interval',
        cron_expression: '',
        interval_seconds: 3600,
        enabled: true,
        timeout: 3600,
        max_retries: 0,
        target_url: '',
        llm_id: '',
        llm_model_name: '',
        kb_id: '',
        access_token: '',
      });
    }
  }, [editingTask, visible]);

  const handleChange = useCallback(
    (key: keyof ScheduledTaskForm, value: any) => {
      setForm((prev) => {
        // Reset model name when factory changes
        if (key === 'llm_id') {
          return { ...prev, llm_id: value, llm_model_name: '' };
        }
        return { ...prev, [key]: value };
      });
    },
    [],
  );

  const handleSubmit = useCallback(async () => {
    if (!form.name.trim()) return;
    if (!form.script_path.trim()) return;
    const submitData = { ...form, script_args: buildScriptArgs() };
    await onOk(submitData);
  }, [form, onOk, buildScriptArgs]);

  return (
    <Dialog open={visible} onOpenChange={hideModal}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>
            {editingTask
              ? t('scheduledTasks.editTask')
              : t('scheduledTasks.createTask')}
          </DialogTitle>
        </DialogHeader>
        <div className="max-h-[65vh] overflow-y-auto space-y-4 py-4 px-1">
          {/* Basic info */}
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('scheduledTasks.name')}
              <span className="text-red-500 ml-1">*</span>
            </label>
            <Input
              value={form.name}
              onChange={(e) => handleChange('name', e.target.value)}
              placeholder={t('scheduledTasks.name')}
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('scheduledTasks.scriptPath')}
              <span className="text-red-500 ml-1">*</span>
            </label>
            <Input
              value={form.script_path}
              onChange={(e) => handleChange('script_path', e.target.value)}
              placeholder="/path/to/your_script.py"
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('scheduledTasks.scriptArgs')}
            </label>
            <Input
              value={form.script_args}
              onChange={(e) => handleChange('script_args', e.target.value)}
              placeholder="--key value --key2 value2"
            />
          </div>

          {/* 省市联动 */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">省份</label>
              <RAGFlowSelect
                value={selectedProvince}
                onChange={(val) => {
                  setSelectedProvince(val ?? '');
                  setSelectedCity('');
                }}
                options={provinceOptions}
                placeholder="全部省份"
                allowClear
                triggerClassName="w-full h-9 text-sm"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">城市</label>
              <RAGFlowSelect
                value={selectedCity}
                onChange={(val) => setSelectedCity(val ?? '')}
                options={cityOptions}
                placeholder="全部城市"
                allowClear
                disabled={!selectedProvince}
                triggerClassName="w-full h-9 text-sm"
              />
            </div>
          </div>

          {/* Target URL — crawler field */}
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('scheduledTasks.targetUrl')}
            </label>
            <Input
              value={form.target_url}
              onChange={(e) => handleChange('target_url', e.target.value)}
              placeholder={t('scheduledTasks.targetUrlPlaceholder')}
            />
          </div>

          {/* Access token — for authenticated crawlers */}
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('scheduledTasks.accessToken')}
            </label>
            <Input
              value={form.access_token}
              onChange={(e) => handleChange('access_token', e.target.value)}
              placeholder={t('scheduledTasks.accessTokenPlaceholder')}
              type="password"
            />
          </div>

          {/* LLM model selection */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('scheduledTasks.llmModel')}
              </label>
              <Select
                value={form.llm_id}
                onValueChange={(v) => handleChange('llm_id', v)}
              >
                <SelectTrigger>
                  <SelectValue
                    placeholder={t('scheduledTasks.llmModelPlaceholder')}
                  />
                </SelectTrigger>
                <SelectContent>
                  {factoryOptions.map((fid) => (
                    <SelectItem key={fid} value={fid}>
                      {fid}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('scheduledTasks.llmModel')} ({t('common.name')})
              </label>
              <Select
                value={form.llm_model_name}
                onValueChange={(v) => handleChange('llm_model_name', v)}
                disabled={!form.llm_id}
              >
                <SelectTrigger>
                  <SelectValue
                    placeholder={t('scheduledTasks.llmModelPlaceholder')}
                  />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((m) => (
                    <SelectItem key={m.llm_name} value={m.llm_name}>
                      {m.llm_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Target knowledge base */}
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('scheduledTasks.targetKb')}
            </label>
            <Select
              value={form.kb_id}
              onValueChange={(v) => handleChange('kb_id', v)}
            >
              <SelectTrigger>
                <SelectValue
                  placeholder={t('scheduledTasks.targetKbPlaceholder')}
                />
              </SelectTrigger>
              <SelectContent>
                {kbList.map((kb: any) => (
                  <SelectItem key={kb.id} value={kb.id}>
                    {kb.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Schedule config */}
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('scheduledTasks.scheduleType')}
            </label>
            <Select
              value={form.schedule_type}
              onValueChange={(v) => handleChange('schedule_type', v)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="interval">
                  {t('scheduledTasks.intervalSeconds')}
                </SelectItem>
                <SelectItem value="cron">
                  {t('scheduledTasks.cronExpression')}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {form.schedule_type === 'cron' ? (
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('scheduledTasks.cronExpression')}
              </label>
              <Input
                value={form.cron_expression}
                onChange={(e) =>
                  handleChange('cron_expression', e.target.value)
                }
                placeholder="*/5 * * * *"
              />
            </div>
          ) : (
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('scheduledTasks.intervalSeconds')}
              </label>
              <Input
                type="number"
                min={1}
                value={form.interval_seconds}
                onChange={(e) =>
                  handleChange(
                    'interval_seconds',
                    parseInt(e.target.value) || 3600,
                  )
                }
              />
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('scheduledTasks.timeout')}
              </label>
              <Input
                type="number"
                min={1}
                value={form.timeout}
                onChange={(e) =>
                  handleChange('timeout', parseInt(e.target.value) || 3600)
                }
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('scheduledTasks.maxRetries')}
              </label>
              <Input
                type="number"
                min={0}
                value={form.max_retries}
                onChange={(e) =>
                  handleChange('max_retries', parseInt(e.target.value) || 0)
                }
              />
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Switch
              checked={form.enabled}
              onCheckedChange={(v) => handleChange('enabled', v)}
            />
            <label className="text-sm font-medium">
              {form.enabled
                ? t('scheduledTasks.enabled')
                : t('scheduledTasks.disabled')}
            </label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={hideModal}>
            {t('common.cancel')}
          </Button>
          <Button onClick={handleSubmit} loading={loading}>
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
