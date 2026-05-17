import { MessageType } from '@/constants/chat';
import { useTranslate } from '@/hooks/common-hooks';
import {
  useFetchChatList,
  useFetchSessionList,
} from '@/hooks/use-chat-request';
import { IConversation } from '@/interfaces/database/chat';
import { generateConversationId } from '@/utils/chat';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router';
import { useChatUrlParams } from './use-chat-url';

// 默认开场白
const DEFAULT_PROLOGUE = '你好！我是你的助理，有什么可以帮到你的吗？';

export const useFindPrologueFromDialogList = () => {
  const { id: dialogId } = useParams();
  const { data } = useFetchChatList();

  const prologue = useMemo(() => {
    const prologueFromConfig = data.chats.find((x) => x.id === dialogId)
      ?.prompt_config?.prologue;
    // 如果配置了开场白就使用配置的，否则使用默认开场白
    return prologueFromConfig || DEFAULT_PROLOGUE;
  }, [dialogId, data]);

  return prologue;
};

export const useSelectDerivedConversationList = () => {
  const { t } = useTranslate('chat');

  const [list, setList] = useState<Array<IConversation>>([]);
  const {
    data: conversationList,
    loading,
    handleInputChange,
    searchString,
  } = useFetchSessionList();

  const { id: dialogId } = useParams();
  const prologue = useFindPrologueFromDialogList();
  const { setConversationBoth } = useChatUrlParams();

  const addTemporaryConversation = useCallback(() => {
    const conversationId = generateConversationId();
    setList((pre) => {
      if (dialogId) {
        setConversationBoth(conversationId, 'true');
        const nextList = [
          {
            id: conversationId,
            name: t('newConversation'),
            chat_id: dialogId,
            is_new: true,
            messages: [
              {
                content: prologue,
                role: MessageType.Assistant,
              },
            ],
          } as any,
          ...conversationList,
        ];
        return nextList;
      }

      return pre;
    });
  }, [dialogId, setConversationBoth, t, prologue, conversationList]);

  const removeTemporaryConversation = useCallback((conversationId: string) => {
    setList((prevList) => {
      return prevList.filter(
        (conversation) => conversation.id !== conversationId,
      );
    });
  }, []);

  // When you first enter the page, select the top conversation card

  useEffect(() => {
    setList([...conversationList]);
  }, [conversationList]);

  return {
    list,
    addTemporaryConversation,
    removeTemporaryConversation,
    loading,
    handleInputChange,
    searchString,
  };
};
