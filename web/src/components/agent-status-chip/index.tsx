import { INodeEvent } from '@/hooks/use-send-message';
import { cn } from '@/lib/utils';
import { CheckCircle2, ChevronUp, Loader2, XCircle } from 'lucide-react';
import { useMemo } from 'react';
import styles from './index.module.less';
import { deriveStatus, getNodeAction } from './utils';

interface IProps {
  eventList: INodeEvent[];
  isRunning: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
}

/** Single step dot on the timeline */
function TimelineStep({
  step,
  isDone,
  isCurrent,
  isLast,
}: {
  step: ReturnType<typeof deriveStatus>['steps'][number];
  isDone: boolean;
  isCurrent: boolean;
  isLast: boolean;
}) {
  const label = getNodeAction(step.type).done || step.name;

  return (
    <div
      className={cn(styles.timelineStep, {
        [styles.timelineStepDone]: isDone && !step.error,
        [styles.timelineStepError]: step.error,
        [styles.timelineStepActive]: isCurrent && !step.error,
        [styles.timelineStepPending]: !isDone && !isCurrent,
      })}
    >
      {/* Connector line to next step */}
      {!isLast && (
        <div
          className={cn(styles.timelineConnector, {
            [styles.timelineConnectorDone]: isDone && !step.error,
            [styles.timelineConnectorActive]: isCurrent && !step.error,
          })}
        />
      )}

      {/* Dot */}
      <div className={styles.timelineDot}>
        {step.error ? (
          <XCircle size={14} />
        ) : isDone ? (
          <CheckCircle2 size={14} />
        ) : isCurrent ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          <div className={styles.dotEmpty} />
        )}
      </div>

      {/* Label */}
      <span className={styles.timelineLabel}>{label}</span>
    </div>
  );
}

export function AgentStatusChip({
  eventList,
  isRunning,
  expanded,
  onToggleExpand,
}: IProps) {
  const status = useMemo(() => deriveStatus(eventList), [eventList]);

  const isActive = isRunning;

  // When stream ended, treat unfinished steps as completed
  const resolvedSteps = useMemo(() => {
    return status.steps.map((s) => ({
      ...s,
      isDone: isActive ? !!s.finishedAt : true,
    }));
  }, [status.steps, isActive]);

  const currentStepIdx = useMemo(
    () => resolvedSteps.findIndex((s) => !s.isDone),
    [resolvedSteps],
  );

  const currentAction = status.currentStep
    ? getNodeAction(status.currentStep.type)
    : null;

  // Don't render if there's nothing to show
  if (status.steps.length === 0 && !isRunning) {
    return null;
  }

  const completedCount = status.completedSteps.length;
  const totalCount = status.totalSteps;

  return (
    <div
      className={cn(styles.container, {
        [styles.containerRunning]: isActive,
        [styles.containerCompleted]: !isActive,
      })}
    >
      {/* Summary row — always visible */}
      <button className={styles.summaryRow} onClick={onToggleExpand}>
        <div className={styles.summaryLeft}>
          {isActive ? (
            <>
              <Loader2 className={cn(styles.icon, 'animate-spin')} size={15} />
              <span className={styles.actionText}>
                {currentAction?.running || '正在处理'}
              </span>
              {totalCount > 1 && (
                <span className={styles.stepCount}>
                  {completedCount}/{totalCount}
                </span>
              )}
            </>
          ) : (
            <>
              <CheckCircle2
                className={cn(styles.icon, styles.iconDone)}
                size={15}
              />
              <span className={styles.actionTextDone}>
                {resolvedSteps
                  .map((s) => getNodeAction(s.type).done)
                  .filter((v, i, a) => a.indexOf(v) === i)
                  .join(' · ')}
              </span>
            </>
          )}
        </div>

        <div className={styles.summaryRight}>
          {/* Mini progress bar when running */}
          {isActive && totalCount > 1 && (
            <div className={styles.miniProgress}>
              <div
                className={styles.miniProgressFill}
                style={{
                  width: `${totalCount > 0 ? (completedCount / totalCount) * 100 : 0}%`,
                }}
              />
            </div>
          )}
          <span className={styles.expandBtn}>
            <ChevronUp
              size={14}
              className={cn(styles.chevron, expanded && styles.chevronOpen)}
            />
          </span>
        </div>
      </button>

      {/* Expanded timeline */}
      {expanded && resolvedSteps.length > 0 && (
        <div className={styles.timeline}>
          <div className={styles.timelineTrack}>
            {resolvedSteps.map((step, i) => (
              <TimelineStep
                key={step.id}
                step={step}
                isDone={step.isDone}
                isCurrent={i === currentStepIdx}
                isLast={i === resolvedSteps.length - 1}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
