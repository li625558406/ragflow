import { cn } from '@/lib/utils';
import { Mic } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

interface Props {
  onTranscript: (text: string) => void;
  testId?: string;
}

export function RealtimeAudioButton({ onTranscript, testId }: Props) {
  const [isRecording, setIsRecording] = useState(false);
  const recognitionRef = useRef<any>(null);
  const stoppedRef = useRef(false);
  const retryRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopRecognition = useCallback(() => {
    stoppedRef.current = true;
    if (recognitionRef.current) {
      recognitionRef.current.abort();
      recognitionRef.current = null;
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startRecognition = useCallback(() => {
    const SpeechRecognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      toast.error('当前浏览器不支持语音识别，请使用 Chrome 或 Edge');
      return;
    }

    if (recognitionRef.current) {
      recognitionRef.current.abort();
      recognitionRef.current = null;
    }

    stoppedRef.current = false;
    retryRef.current = 0;
    setIsRecording(true);

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'zh-CN';

    recognition.onresult = (event: any) => {
      if (stoppedRef.current) return;
      let transcript = '';
      for (let i = 0; i < event.results.length; i++) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          transcript += /[，,。！？、；：]/.test(chunk.trimEnd().slice(-1))
            ? chunk
            : chunk + '，';
        } else {
          transcript += chunk;
        }
      }
      onTranscript(transcript);
    };

    recognition.onerror = async (event: any) => {
      if (stoppedRef.current) return;
      if (event.error === 'not-allowed' && retryRef.current < 3) {
        retryRef.current++;
        try {
          const stream = await navigator.mediaDevices.getUserMedia({
            audio: true,
          });
          stream.getTracks().forEach((t) => t.stop());
          try {
            recognitionRef.current?.start();
          } catch {
            /* already started */
          }
          return;
        } catch (e: any) {
          if (e.name === 'NotAllowedError') {
            stoppedRef.current = true;
            toast.error(
              '麦克风权限未开启，请在浏览器地址栏左侧锁图标 → 站点设置中允许',
            );
            setIsRecording(false);
            return;
          }
        }
        setTimeout(() => {
          try {
            recognitionRef.current?.start();
          } catch {
            /* already started */
          }
        }, 500);
        return;
      }
      if (event.error === 'not-allowed') {
        toast.error(
          '麦克风权限未开启，请在浏览器地址栏左侧锁图标 → 站点设置中允许',
        );
      } else if (event.error !== 'aborted') {
        toast.error(`语音识别错误: ${event.error}`);
      }
      setIsRecording(false);
    };

    recognition.onend = () => {
      if (!stoppedRef.current && retryRef.current === 0) {
        try {
          recognition.start();
        } catch {
          /* already started */
        }
      }
    };

    recognition.start();
    recognitionRef.current = recognition;
  }, [onTranscript]);

  const handleClick = useCallback(() => {
    if (isRecording) {
      stopRecognition();
      setIsRecording(false);
    } else {
      startRecognition();
    }
  }, [isRecording, startRecognition, stopRecognition]);

  useEffect(() => {
    return () => {
      stopRecognition();
    };
  }, [stopRecognition]);

  return (
    <button
      type="button"
      onClick={handleClick}
      data-testid={testId}
      className={cn(
        'shrink-0 w-9 h-9 flex items-center justify-center rounded-lg transition-all border-0 bg-transparent cursor-pointer',
        isRecording
          ? 'animate-pulse text-[#14B8A6] bg-[#e6f9f7]'
          : 'text-[#A3A3A3] hover:text-[#525252] hover:bg-[#F5F5F5]',
      )}
    >
      {isRecording ? (
        <span className="w-2.5 h-2.5 rounded-full bg-[#14B8A6]" />
      ) : (
        <Mic size={16} />
      )}
    </button>
  );
}
