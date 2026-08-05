import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DetectTab } from './detect-tab';
import { NotificationAdminTab } from './notification-admin-tab';
import { ParseMonitorTab } from './parse-monitor-tab';
import { ResultsTab } from './results-tab';
import { TasksTab } from './tasks-tab';

export default function Crawl4aiPage() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('results');

  return (
    <article className="size-full flex flex-col px-5 pt-8">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{t('crawl4ai.title')}</h1>
      </header>
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex-1 flex flex-col min-h-0"
      >
        <TabsList className="w-fit">
          <TabsTrigger value="results">{t('crawl4ai.results')}</TabsTrigger>
          <TabsTrigger value="tasks">{t('crawl4ai.tasks')}</TabsTrigger>
          <TabsTrigger value="detect">{t('crawl4ai.detect.tab')}</TabsTrigger>
          <TabsTrigger value="parse-monitor">
            {t('crawl4ai.parseMonitor.tab')}
          </TabsTrigger>
          <TabsTrigger value="notifications">
            {t('notifications.admin.tab')}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="results" className="flex-1 min-h-0 mt-4">
          <ResultsTab />
        </TabsContent>
        <TabsContent value="tasks" className="flex-1 min-h-0 mt-4">
          <TasksTab />
        </TabsContent>
        <TabsContent value="detect" className="flex-1 min-h-0 mt-4">
          <DetectTab />
        </TabsContent>
        <TabsContent value="parse-monitor" className="flex-1 min-h-0 mt-4">
          <ParseMonitorTab />
        </TabsContent>
        <TabsContent value="notifications" className="flex-1 min-h-0 mt-4">
          <NotificationAdminTab />
        </TabsContent>
      </Tabs>
    </article>
  );
}
