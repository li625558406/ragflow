import { INodeEvent } from '@/hooks/use-send-message';
import { cn } from '@/lib/utils';
import { CheckCircle2, ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { useMemo } from 'react';
import styles from './index.module.less';
import { deriveStatus, formatDuration, getNodeAction } from './utils';

interface IProps {
  eventList: INodeEvent[];
  isRunning: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
}

export function AgentStatusChip({
  eventList,
  isRunning,
  expanded,
  onToggleExpand,
}: IProps) {
  const status = useMemo(() => deriveStatus(eventList), [eventList]);

  const isActive = isRunning;

  // When stream ended, treat the unfinished last step as completed too
  const allCompletedSteps = useMemo(() => {
    if (isActive || !status.currentStep) return status.completedSteps;
    return [...status.completedSteps, status.currentStep];
  }, [isActive, status.completedSteps, status.currentStep]);

  // Don't render if there's nothing to show
  if (status.steps.length === 0 && !isRunning) {
    return null;
  }

  const currentAction = status.currentStep
    ? getNodeAction(status.currentStep.type)
    : null;

  return (
    <div
      className={cn(styles.statusChip, {
        [styles.running]: isActive,
        [styles.completed]: !isActive,
      })}
    >
      {isActive ? (
        <button className={styles.statusRow} onClick={onToggleExpand}>
          <Loader2 className={cn(styles.icon, 'animate-spin')} size={14} />
          <span className={styles.actionText}>
            {currentAction?.running || '正在处理'}
          </span>
          {status.completedSteps.length > 0 && (
            <span className={styles.stepCount}>
              ({status.completedSteps.length}/{status.totalSteps})
            </span>
          )}
          <span className={styles.expandBtn}>
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </span>
        </button>
      ) : (
        <button className={styles.statusRow} onClick={onToggleExpand}>
          <CheckCircle2
            className={cn(styles.icon, styles.checkIcon)}
            size={14}
          />
          <span className={styles.summaryText}>
            {allCompletedSteps
              .map((s) => getNodeAction(s.type).done)
              .filter((v, i, a) => a.indexOf(v) === i)
              .join(' · ')}
            {status.totalElapsed > 0
              ? ` · ${formatDuration(status.totalElapsed)}`
              : ''}
          </span>
          <span className={styles.expandBtn}>
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </span>
        </button>
      )}
    </div>
  );
}
