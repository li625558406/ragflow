import React from 'react';

interface Section {
  section_id: string;
  section_name: string;
  page_range: number[];
}

interface SectionNavProps {
  sections: Section[];
  activeId: string;
  onSelect: (id: string) => void;
}

export const SectionNav: React.FC<SectionNavProps> = ({
  sections,
  activeId,
  onSelect,
}) => {
  return (
    <div className="w-64 border-r bg-gray-50">
      <div className="p-4 border-b">
        <h3 className="font-semibold">章节目录</h3>
      </div>
      <div className="h-[calc(100vh-120px)] overflow-auto">
        <div className="p-2">
          {sections.map((section) => (
            <div
              key={section.section_id}
              className={`p-3 rounded cursor-pointer mb-1 ${
                activeId === section.section_id
                  ? 'bg-blue-100 text-blue-700'
                  : 'hover:bg-gray-100'
              }`}
              onClick={() => onSelect(section.section_id)}
            >
              <div className="font-medium truncate">{section.section_name}</div>
              <div className="text-xs text-gray-500">
                第 {section.page_range[0]}-{section.page_range[1]} 页
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
