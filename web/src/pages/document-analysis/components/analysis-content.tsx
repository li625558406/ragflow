import React, { useState } from 'react';

interface AnalysisData {
  [key: string]: {
    label: string;
    content: string;
  };
}

interface AnalysisContentProps {
  section: {
    section_name: string;
    page_range: number[];
    analysis: AnalysisData;
  } | null;
}

export const AnalysisContent: React.FC<AnalysisContentProps> = ({
  section,
}) => {
  const [activeTab, setActiveTab] = useState('key_points');

  if (!section) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        请选择左侧章节查看分析结果
      </div>
    );
  }

  const dimensions = Object.keys(section.analysis);

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="mb-4">
        <h2 className="text-xl font-bold">{section.section_name}</h2>
        <p className="text-sm text-gray-500">
          第 {section.page_range[0]}-{section.page_range[1]} 页
        </p>
      </div>

      <div className="border-b mb-4">
        <div className="flex gap-4">
          {dimensions.map((key) => (
            <button
              key={key}
              className={`pb-2 px-1 ${
                activeTab === key
                  ? 'border-b-2 border-blue-500 text-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              onClick={() => setActiveTab(key)}
            >
              {section.analysis[key]?.label || key}
            </button>
          ))}
        </div>
      </div>

      <div className="prose max-w-none whitespace-pre-wrap">
        {section.analysis[activeTab]?.content || '暂无内容'}
      </div>
    </div>
  );
};
