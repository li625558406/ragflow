import { IGraph } from '@/interfaces/database/agent';
import { Operator } from '@/pages/agent/constant';
import { useCallback } from 'react';
import useGraphStore from '../store';

export const useSetGraphInfo = () => {
  const { setEdges, setNodes } = useGraphStore((state) => state);
  const setGraphInfo = useCallback(
    ({ nodes = [], edges = [] }: IGraph) => {
      if (nodes.length || edges.length) {
        // Upgrade FanOut nodes from ragNode to agentNode for tool handle support
        const migratedNodes = nodes.map((node) => {
          if (
            node.data?.label === Operator.FanOut &&
            node.type !== 'agentNode'
          ) {
            return { ...node, type: 'agentNode' };
          }
          return node;
        });
        setNodes(migratedNodes);
        setEdges(edges);
      }
    },
    [setEdges, setNodes],
  );
  return setGraphInfo;
};
