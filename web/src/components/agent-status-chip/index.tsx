import { IToolUsage } from '@/hooks/use-send-message';
import { cn } from '@/lib/utils';
import { CheckCircle2, Loader2, Wrench, XCircle } from 'lucide-react';
import { useMemo } from 'react';
import styles from './index.module.less';
import { deriveStatus, formatDuration, getNodeAction } from './utils';

interface IProps {
  eventList: INodeEvent[];
  isRunning: boolean;
}

/** Single tool chip below a timeline step */
function ToolChip({
  tool,
  isDone,
  isCurrent,
}: {
  tool: IToolUsage;
  isDone: boolean;
  isCurrent: boolean;
}) {
  return (
    <div
      className={cn(styles.toolChip, {
        [styles.toolChipDone]: isDone,
        [styles.toolChipActive]: isCurrent,
      })}
      title={tool.tool_name}
    >
      <Wrench size={9} />
      <span className={styles.toolChipName}>{tool.tool_name}</span>
      {tool.elapsed_time !== undefined && tool.elapsed_time !== null && (
        <span className={styles.toolChipTime}>
          {formatDuration(tool.elapsed_time)}
        </span>
      )}
    </div>
  );
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

      {/* Label + tools */}
      <div className={styles.timelineStepBody}>
        <span className={styles.timelineLabel}>{label}</span>
        {step.tools.length > 0 && (
          <div className={styles.toolChipList}>
            {step.tools.map((t, i) => (
              <ToolChip
                key={`${t.tool_name}-${i}`}
                tool={t}
                isDone={isDone}
                isCurrent={isCurrent}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function AgentStatusChip({ eventList, isRunning }: IProps) {
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

  const MAX_VISIBLE = 6;
  const { displaySteps, hiddenCount } = useMemo(() => {
    if (resolvedSteps.length <= MAX_VISIBLE) {
      return { displaySteps: resolvedSteps, hiddenCount: 0 };
    }
    // Trim earliest completed steps from the front.
    // When running, never trim past the current step.
    // When all done, all steps can be trimmed.
    const trimEnd = currentStepIdx >= 0 ? currentStepIdx : resolvedSteps.length;
    const keepCount = MAX_VISIBLE - 1; // leave 1 slot for "+N" indicator
    const toTrim = resolvedSteps.length - keepCount;
    const actualTrim = Math.min(toTrim, trimEnd);

    if (actualTrim <= 0) {
      return { displaySteps: resolvedSteps, hiddenCount: 0 };
    }
    return {
      displaySteps: resolvedSteps.slice(actualTrim),
      hiddenCount: actualTrim,
    };
  }, [resolvedSteps, currentStepIdx]);

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
      <div className={styles.summaryRow}>
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
              <span className={styles.actionTextDone}>完成</span>
            </>
          )}
        </div>
      </div>

      {/* Timeline — always visible */}
      {displaySteps.length > 0 && (
        <div className={styles.timeline}>
          {/* Vertical progress bar on the left */}
          {isActive && totalCount > 1 && (
            <div className={styles.timelineProgress}>
              <div
                className={styles.timelineProgressFill}
                style={{
                  height: `${totalCount > 0 ? (completedCount / totalCount) * 100 : 0}%`,
                }}
              />
            </div>
          )}
          <div className={styles.timelineTrack}>
            {hiddenCount > 0 && (
              <div className={styles.timelineStep}>
                <div className={styles.timelineDot}>
                  <div className={styles.dotEmpty} />
                </div>
                <span className={styles.timelineLabelMuted}>
                  +{hiddenCount} 已完成
                </span>
              </div>
            )}
            {displaySteps.map((step, i) => {
              const globalIdx = hiddenCount + i;
              return (
                <TimelineStep
                  key={step.id}
                  step={step}
                  isDone={step.isDone}
                  isCurrent={globalIdx === currentStepIdx}
                  isLast={globalIdx === resolvedSteps.length - 1}
                />
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
