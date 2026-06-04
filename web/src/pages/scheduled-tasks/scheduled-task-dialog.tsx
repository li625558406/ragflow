import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import message from '@/components/ui/message';
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
  addWechatMpAccount,
  deleteWechatMpAccount,
  deleteWechatMpAuth,
  fetchKnowledgeBases,
  fetchLlmModels,
  fetchWechatMpAccounts,
  fetchWechatMpAuthQrcode,
  fetchWechatMpAuthStatus,
  searchWechatMp,
} from '@/services/scheduled-task-service';
import request from '@/utils/next-request';
import { X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface ScheduledTaskForm {
  task_type: string;
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
  gather_content: boolean;
  first_max_page: number;
}

interface MpAccount {
  id: string;
  mp_name: string;
  faker_id: string;
  mp_cover?: string;
  mp_intro?: string;
}

interface Props {
  visible: boolean;
  editingTask?: Record<string, any> | null;
  loading: boolean;
  hideModal: () => void;
  onOk: (values: Record<string, any>) => Promise<boolean>;
}

const DEFAULT_FORM: ScheduledTaskForm = {
  task_type: 'script',
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
  gather_content: true,
  first_max_page: 10,
};

export function ScheduledTaskDialog({
  visible,
  editingTask,
  loading,
  hideModal,
  onOk,
}: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<ScheduledTaskForm>({ ...DEFAULT_FORM });

  const [llmModels, setLlmModels] = useState<any[]>([]);
  const [kbList, setKbList] = useState<any[]>([]);

  // Bid crawler province/city
  const [provinceOptions, setProvinceOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [cityOptions, setCityOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [selectedProvince, setSelectedProvince] = useState<string>('');
  const [selectedCity, setSelectedCity] = useState<string>('');

  // WeChat MP state
  const [authStatus, setAuthStatus] = useState<{
    login_status: boolean;
    mp_name?: string;
    pending?: boolean;
  }>({ login_status: false });
  const [mpAccounts, setMpAccounts] = useState<MpAccount[]>([]);
  const [selectedMps, setSelectedMps] = useState<MpAccount[]>([]);
  const [searchKw, setSearchKw] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [qrCodeBase64, setQrCodeBase64] = useState('');
  const [qrDialogOpen, setQrDialogOpen] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
    // WeChat MP: load accounts and auth status
    fetchWechatMpAccounts().then(setMpAccounts);
    fetchWechatMpAuthStatus().then(setAuthStatus);
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

  // Detect task type from editing data
  const detectTaskType = useCallback(
    (task: Record<string, any> | null | undefined) => {
      if (!task) return 'script';
      if (task.script_args) {
        try {
          const args = JSON.parse(task.script_args);
          if (args.mp_ids) return 'wechat_mp';
        } catch {
          // ignore
        }
      }
      return 'script';
    },
    [],
  );

  useEffect(() => {
    if (editingTask) {
      const taskType = detectTaskType(editingTask);
      setForm({
        task_type: taskType,
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

      // Restore WeChat MP selected accounts
      if (taskType === 'wechat_mp') {
        try {
          const args = JSON.parse(editingTask.script_args || '{}');
          const mpIds: string[] = args.mp_ids || [];
          fetchWechatMpAccounts().then((all) => {
            setSelectedMps(
              all.filter((a: MpAccount) => mpIds.includes(a.faker_id)),
            );
          });
          if (args.first_max_page) {
            setForm((prev) => ({
              ...prev,
              first_max_page: args.first_max_page,
            }));
          }
        } catch {
          setSelectedMps([]);
        }
      }
    } else {
      setForm({ ...DEFAULT_FORM });
      setSelectedMps([]);
      setSearchKw('');
      setSearchResults([]);
    }
  }, [editingTask, visible, detectTaskType]);

  const handleChange = useCallback(
    (key: keyof ScheduledTaskForm, value: any) => {
      setForm((prev) => {
        // Reset model name when factory changes
        if (key === 'llm_id') {
          return { ...prev, llm_id: value, llm_model_name: '' };
        }
        // When switching task type, reset relevant fields
        if (key === 'task_type') {
          if (value === 'wechat_mp') {
            return {
              ...prev,
              task_type: value,
              script_path: 'rag/svr/wechat_mp_crawler.py',
              script_args: '',
              target_url: '',
              access_token: '',
              llm_id: '',
              llm_model_name: '',
            };
          }
          return { ...prev, task_type: value, script_path: '' };
        }
        return { ...prev, [key]: value };
      });
    },
    [],
  );

  // ── WeChat MP handlers ──────────────────────────────────

  const handleScanQr = useCallback(async () => {
    try {
      const { qrcode_base64 } = await fetchWechatMpAuthQrcode();
      setQrCodeBase64(qrcode_base64);
      setQrDialogOpen(true);

      // Poll for status
      if (pollingRef.current) clearInterval(pollingRef.current);
      pollingRef.current = setInterval(async () => {
        try {
          const status = await fetchWechatMpAuthStatus();
          if (status.login_status) {
            clearInterval(pollingRef.current!);
            pollingRef.current = null;
            setQrDialogOpen(false);
            setAuthStatus(status);
          } else if (status.reason === 'timeout') {
            clearInterval(pollingRef.current!);
            pollingRef.current = null;
            setQrDialogOpen(false);
          }
        } catch {
          // Ignore polling errors
        }
      }, 2000);
    } catch (err: any) {
      console.error('Failed to get QR code:', err);
    }
  }, []);

  const handleLogout = useCallback(async () => {
    await deleteWechatMpAuth();
    setAuthStatus({ login_status: false });
  }, []);

  const handleSearchMp = useCallback(async (kw: string) => {
    setSearchKw(kw);
    if (!kw.trim()) {
      setSearchResults([]);
      setSearchOpen(false);
      return;
    }
    try {
      const results = await searchWechatMp(kw);
      setSearchResults(results);
      setSearchOpen(results.length > 0);
    } catch {
      setSearchResults([]);
    }
  }, []);

  const handleAddMp = useCallback(async (mp: any) => {
    try {
      await addWechatMpAccount({
        mp_name: mp.mp_name,
        faker_id: mp.faker_id,
        mp_cover: mp.mp_cover,
        mp_intro: mp.mp_intro,
      });
      // Refresh account list
      const accounts = await fetchWechatMpAccounts();
      setMpAccounts(accounts);
      setSelectedMps((prev) => {
        const exists = prev.find((a) => a.faker_id === mp.faker_id);
        if (exists) return prev;
        const newAcct = accounts.find(
          (a: MpAccount) => a.faker_id === mp.faker_id,
        );
        return [...prev, newAcct || mp];
      });
    } catch (err) {
      console.error('Failed to add MP:', err);
    }
    setSearchOpen(false);
    setSearchKw('');
  }, []);

  const handleRemoveMp = useCallback(async (accountId: string) => {
    try {
      await deleteWechatMpAccount(accountId);
      setSelectedMps((prev) => prev.filter((a) => a.id !== accountId));
      setMpAccounts((prev) => prev.filter((a) => a.id !== accountId));
    } catch (err) {
      console.error('Failed to remove MP:', err);
    }
  }, []);

  const toggleSelectMp = useCallback((mp: MpAccount) => {
    setSelectedMps((prev) => {
      const exists = prev.find((a) => a.faker_id === mp.faker_id);
      if (exists) return prev.filter((a) => a.faker_id !== mp.faker_id);
      return [...prev, mp];
    });
  }, []);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  // ── Submit ──────────────────────────────────────────────

  const handleSubmit = useCallback(async () => {
    if (!form.name.trim()) {
      message.warning('请输入任务名称');
      return;
    }
    if (form.task_type === 'script' && !form.script_path.trim()) {
      message.warning('请输入脚本路径');
      return;
    }

    // Strip frontend-only fields before sending to backend
    const { gather_content, first_max_page, ...rest } = form;
    let submitData: Record<string, any>;

    if (form.task_type === 'wechat_mp') {
      submitData = {
        ...rest,
        script_path: 'rag/svr/wechat_mp_crawler.py',
        script_args: JSON.stringify({
          mp_ids: selectedMps.map((m) => m.faker_id),
          gather_content,
          first_max_page,
        }),
        target_url: '',
        access_token: rest.access_token || '',
        llm_id: '',
        llm_model_name: '',
      };
    } else {
      submitData = { ...rest, script_args: buildScriptArgs() };
    }

    try {
      await onOk(submitData);
    } catch (err) {
      console.error('Save failed:', err);
    }
  }, [form, onOk, buildScriptArgs, selectedMps]);

  const isWechatMp = form.task_type === 'wechat_mp';

  return (
    <>
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
            {/* ── Task Type ────────────────────────────────── */}
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('scheduledTasks.taskType')}
              </label>
              <Select
                value={form.task_type}
                onValueChange={(v) => handleChange('task_type', v)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="script">
                    {t('scheduledTasks.taskTypeScript')}
                  </SelectItem>
                  <SelectItem value="wechat_mp">
                    {t('scheduledTasks.taskTypeWechatMp')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* ── Task name (always shown) ─────────────────── */}
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

            {/* ══════════════════════════════════════════════════
                SCRIPT MODE FIELDS
               ══════════════════════════════════════════════════ */}
            {!isWechatMp && (
              <>
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    {t('scheduledTasks.scriptPath')}
                    <span className="text-red-500 ml-1">*</span>
                  </label>
                  <Input
                    value={form.script_path}
                    onChange={(e) =>
                      handleChange('script_path', e.target.value)
                    }
                    placeholder="/path/to/your_script.py"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    {t('scheduledTasks.scriptArgs')}
                  </label>
                  <Input
                    value={form.script_args}
                    onChange={(e) =>
                      handleChange('script_args', e.target.value)
                    }
                    placeholder="--key value --key2 value2"
                  />
                </div>

                {/* Province/City */}
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

                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    {t('scheduledTasks.accessToken')}
                  </label>
                  <Input
                    value={form.access_token}
                    onChange={(e) =>
                      handleChange('access_token', e.target.value)
                    }
                    placeholder={t('scheduledTasks.accessTokenPlaceholder')}
                    type="password"
                  />
                </div>

                {/* LLM model */}
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
              </>
            )}

            {/* ══════════════════════════════════════════════════
                WECHAT MP MODE FIELDS
               ══════════════════════════════════════════════════ */}
            {isWechatMp && (
              <>
                {/* ── WeChat Auth ──────────────────────────── */}
                <div className="space-y-2 border rounded-md p-3">
                  <label className="text-sm font-medium">
                    {t('scheduledTasks.wechatMpAuth')}
                  </label>
                  {authStatus.login_status ? (
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-green-600">
                        {t('scheduledTasks.wechatMpLoggedIn')}
                        {authStatus.mp_name ? `: ${authStatus.mp_name}` : ''}
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={handleLogout}
                      >
                        {t('scheduledTasks.wechatMpLogout')}
                      </Button>
                    </div>
                  ) : (
                    <Button size="sm" onClick={handleScanQr}>
                      {t('scheduledTasks.wechatMpScanLogin')}
                    </Button>
                  )}
                </div>

                {/* ── MP Account Selection ─────────────────── */}
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    {t('scheduledTasks.wechatMpSelectedAccounts')}
                  </label>
                  {/* Selected chips */}
                  <div className="flex flex-wrap gap-1 min-h-[28px]">
                    {selectedMps.map((mp) => (
                      <span
                        key={mp.faker_id}
                        className="inline-flex items-center gap-1 bg-blue-100 text-blue-800 text-xs px-2 py-0.5 rounded-full"
                      >
                        {mp.mp_name}
                        <X
                          className="w-3 h-3 cursor-pointer"
                          onClick={() => handleRemoveMp(mp.id)}
                        />
                      </span>
                    ))}
                  </div>
                  {/* Also show subscribed accounts as quick-select */}
                  {mpAccounts.length > 0 && (
                    <div className="text-xs text-muted-foreground mt-1">
                      已订阅:
                      {mpAccounts
                        .filter(
                          (a) =>
                            !selectedMps.find((s) => s.faker_id === a.faker_id),
                        )
                        .map((a) => (
                          <span
                            key={a.faker_id}
                            className="inline-flex items-center gap-1 bg-gray-100 text-gray-700 text-xs px-2 py-0.5 rounded-full ml-1 cursor-pointer hover:bg-blue-100"
                            onClick={() => toggleSelectMp(a)}
                          >
                            {a.mp_name}
                          </span>
                        ))}
                    </div>
                  )}

                  {/* Search dropdown */}
                  <div className="relative">
                    <Input
                      value={searchKw}
                      onChange={(e) => handleSearchMp(e.target.value)}
                      placeholder={t(
                        'scheduledTasks.wechatMpSearchPlaceholder',
                      )}
                      className="text-sm"
                    />
                    {searchOpen && searchResults.length > 0 && (
                      <div className="absolute z-10 w-full bg-white border rounded-md shadow-lg mt-1 max-h-48 overflow-y-auto">
                        {searchResults.map((r: any) => (
                          <div
                            key={r.faker_id}
                            className="flex items-center gap-2 px-3 py-2 hover:bg-gray-100 cursor-pointer text-sm"
                            onClick={() => handleAddMp(r)}
                          >
                            {r.mp_cover && (
                              <img
                                src={r.mp_cover}
                                alt=""
                                className="w-6 h-6 rounded-full"
                              />
                            )}
                            <div className="flex-1 min-w-0">
                              <div className="font-medium truncate">
                                {r.mp_name}
                              </div>
                              <div className="text-xs text-gray-500 truncate">
                                {r.faker_id}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {/* ── Gather content toggle ────────────────── */}
                <div className="flex items-center gap-2">
                  <Switch
                    checked={form.gather_content}
                    onCheckedChange={(v) => handleChange('gather_content', v)}
                  />
                  <label className="text-sm font-medium">
                    {t('scheduledTasks.wechatMpGatherContent')}
                  </label>
                </div>

                {/* ── First-time full crawl max pages ──────── */}
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    {t('scheduledTasks.wechatMpFirstMaxPage')}
                  </label>
                  <Input
                    type="number"
                    min={1}
                    max={50}
                    value={form.first_max_page}
                    onChange={(e) =>
                      handleChange(
                        'first_max_page',
                        parseInt(e.target.value) || 10,
                      )
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    {t('scheduledTasks.wechatMpFirstMaxPageHint')}
                  </p>
                </div>
              </>
            )}

            {/* ── Target KB (always shown) ─────────────────── */}
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

            {/* ── Schedule config (always shown) ───────────── */}
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

      {/* ── QR Code Dialog ──────────────────────────────────── */}
      <Dialog open={qrDialogOpen} onOpenChange={setQrDialogOpen}>
        <DialogContent className="sm:max-w-[360px]">
          <DialogHeader>
            <DialogTitle>{t('scheduledTasks.wechatMpQrScanning')}</DialogTitle>
          </DialogHeader>
          <div className="flex justify-center py-4">
            {qrCodeBase64 ? (
              <img
                src={qrCodeBase64}
                alt="QR Code"
                className="w-64 h-64 border rounded"
              />
            ) : (
              <span className="text-sm text-muted-foreground">Loading...</span>
            )}
          </div>
          <p className="text-center text-sm text-muted-foreground">
            {t('scheduledTasks.wechatMpQrScanning')}
          </p>
        </DialogContent>
      </Dialog>
    </>
  );
}
