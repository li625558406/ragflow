import { useEffect, useState } from 'react';

export default function CLanding() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const root = document.getElementById('root');
    if (root) {
      root.style.height = '100vh';
      root.style.overflow = 'hidden';
    }
    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    return () => {
      if (root) {
        root.style.height = '';
        root.style.overflow = '';
      }
      document.documentElement.style.overflow = '';
      document.body.style.overflow = '';
    };
  }, []);

  return (
    <div className="c-landing-scroll bg-white font-sans antialiased" style={scrollContainerStyle}>
      {/* Navigation */}
      <nav className="bg-[#162E4D] shadow-lg sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-[#1E3A5F] rounded-lg flex items-center justify-center">
              <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <span className="text-lg font-bold text-white tracking-wide">标书分析助手</span>
          </div>
          <div className="hidden md:flex items-center gap-6">
            <a href="/chat" className="bg-[#059669] hover:bg-[#047857] text-white px-5 py-2 rounded-lg text-sm font-medium transition shadow-md cursor-pointer">
              开始使用
            </a>
          </div>
          <button className="md:hidden text-white" onClick={() => setMobileMenuOpen(!mobileMenuOpen)} aria-label="菜单">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              {mobileMenuOpen
                ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                : <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />}
            </svg>
          </button>
        </div>
        {mobileMenuOpen && (
          <div className="md:hidden border-t border-white/10 px-6 py-4 bg-[#162E4D]">
            <a href="/chat" onClick={() => setMobileMenuOpen(false)} className="block bg-[#059669] text-white text-center px-5 py-2.5 rounded-lg text-sm font-medium cursor-pointer">
              开始使用
            </a>
          </div>
        )}
      </nav>

      {/* Hero Section */}
      <section className="relative overflow-hidden text-white py-24 lg:py-32">
        <div className="absolute inset-0 bg-gradient-to-br from-[#0F2340] via-[#1E3A5F] to-[#162E4D]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_70%_20%,rgba(37,99,235,0.15),transparent_60%)]" />
        <div className="absolute inset-0 opacity-50" style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cpath d='M36 34v-4h-8v4h8v4h-8v-4' fill='%23ffffff' fill-opacity='0.03'/%3E%3Cpath d='M48 34v-4h-8v4h8v4h-8v-4' fill='%23ffffff' fill-opacity='0.02'/%3E%3C/g%3E%3C/svg%3E\")", backgroundRepeat: 'repeat' }} />
        <div className="relative max-w-6xl mx-auto px-6">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-2 bg-white/10 border border-white/20 rounded-full px-4 py-1.5 text-sm text-[#D7E2F0] mb-6">
              <span className="w-2 h-2 bg-[#059669] rounded-full animate-pulse" />
              AI 驱动 &middot; 智能分析
            </div>
            <h1 className="text-4xl lg:text-5xl font-bold leading-tight mb-6">
              招标文件智能分析
              <br />
              <span className="text-[#059669]">决策支持平台</span>
            </h1>
            <p className="text-[#AFC5DF] text-lg leading-relaxed mb-10">
              基于大语言模型与知识库检索技术，为政府采购、投标决策提供精准的招标文件分析、关键信息提取与智能问答服务。
            </p>
            <div className="flex flex-wrap gap-4">
              <a href="/chat" className="inline-flex items-center gap-2 bg-[#059669] hover:bg-[#047857] text-white px-8 py-3.5 rounded-lg font-medium transition shadow-lg hover:shadow-xl text-base cursor-pointer">
                立即体验
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5-5m5 5H6" /></svg>
              </a>
            </div>
          </div>
          {/* Stats */}
          <div className="grid grid-cols-3 gap-6 mt-20">
            <div className="text-center bg-white/5 backdrop-blur rounded-xl p-6 border border-white/10">
              <div className="text-3xl font-bold text-[#059669]">10s</div>
              <div className="text-[#AFC5DF] text-sm mt-1">平均响应时间</div>
            </div>
            <div className="text-center bg-white/5 backdrop-blur rounded-xl p-6 border border-white/10">
              <div className="text-3xl font-bold text-white">95%</div>
              <div className="text-[#AFC5DF] text-sm mt-1">关键信息提取率</div>
            </div>
            <div className="text-center bg-white/5 backdrop-blur rounded-xl p-6 border border-white/10">
              <div className="text-3xl font-bold text-white">100+</div>
              <div className="text-[#AFC5DF] text-sm mt-1">支持文件格式</div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-20 lg:py-28 bg-slate-50">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-16">
            <span className="text-[#059669] font-medium text-sm tracking-wider uppercase">核心能力</span>
            <h2 className="text-3xl font-bold text-[#162E4D] mt-2">功能特性</h2>
            <p className="text-slate-500 mt-3 max-w-2xl mx-auto">六大核心功能，覆盖招标文件分析全流程</p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((f) => (
              <div key={f.title} className="group bg-white rounded-xl p-6 border border-slate-100 hover:border-[#AFC5DF] hover:-translate-y-1 hover:shadow-[0_12px_40px_rgba(30,58,95,0.12)] transition-all duration-300">
                <div className={`w-12 h-12 ${f.bg} rounded-xl flex items-center justify-center mb-4`}>
                  <svg className={`w-6 h-6 ${f.color}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={f.icon} /></svg>
                </div>
                <h3 className="text-lg font-semibold text-[#162E4D] mb-2">{f.title}</h3>
                <p className="text-slate-500 text-sm leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Flow Section */}
      <section className="py-20 lg:py-28 bg-white">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-16">
            <span className="text-[#059669] font-medium text-sm tracking-wider uppercase">简单高效</span>
            <h2 className="text-3xl font-bold text-[#162E4D] mt-2">使用流程</h2>
            <p className="text-slate-500 mt-3 max-w-2xl mx-auto">三步完成，从上传到分析</p>
          </div>
          <div className="grid md:grid-cols-3 gap-8">
            {steps.map((s) => (
              <div key={s.title} className="text-center">
                <div className={`w-16 h-16 ${s.bg} rounded-2xl mx-auto flex items-center justify-center mb-4 shadow-sm`}>
                  <span className={`text-2xl font-bold ${s.text}`}>{s.num}</span>
                </div>
                <h3 className="text-xl font-semibold text-[#162E4D] mb-2">{s.title}</h3>
                <p className="text-slate-500 text-sm leading-relaxed">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Scenarios Section */}
      <section className="py-20 lg:py-28 bg-slate-50">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-16">
            <span className="text-[#059669] font-medium text-sm tracking-wider uppercase">广泛应用</span>
            <h2 className="text-3xl font-bold text-[#162E4D] mt-2">应用场景</h2>
            <p className="text-slate-500 mt-3 max-w-2xl mx-auto">覆盖政府采购、工程建设、信息技术等多个招投标领域</p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {scenarios.map((s) => (
              <div key={s.title} className="group bg-white rounded-xl p-6 border border-slate-100 hover:border-[#AFC5DF] hover:-translate-y-1 hover:shadow-[0_12px_40px_rgba(30,58,95,0.12)] transition-all duration-300">
                <div className="w-12 h-12 bg-[#EFF3F8] rounded-xl flex items-center justify-center mb-4 group-hover:bg-[#1E3A5F] transition-colors duration-300">
                  <svg className="w-6 h-6 text-[#1E3A5F] group-hover:text-white transition-colors duration-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={s.icon} /></svg>
                </div>
                <h3 className="text-lg font-semibold text-[#162E4D] mb-2">{s.title}</h3>
                <p className="text-slate-500 text-sm leading-relaxed">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Advantages Section */}
      <section className="py-20 lg:py-28 bg-white">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-16">
            <span className="text-[#059669] font-medium text-sm tracking-wider uppercase">为什么选择我们</span>
            <h2 className="text-3xl font-bold text-[#162E4D] mt-2">技术优势</h2>
            <p className="text-slate-500 mt-3 max-w-2xl mx-auto">基于 RAGFlow 开源引擎，企业级安全可靠</p>
          </div>
          <div className="grid md:grid-cols-2 gap-8">
            {advantages.map((a) => (
              <div key={a.title} className="flex gap-5 p-6 rounded-xl border border-slate-100 hover:border-[#AFC5DF] hover:shadow-sm transition-all duration-300">
                <div className="w-12 h-12 bg-[#059669]/10 rounded-xl flex items-center justify-center shrink-0">
                  <svg className="w-6 h-6 text-[#059669]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={a.icon} /></svg>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-[#162E4D] mb-1.5">{a.title}</h3>
                  <p className="text-slate-500 text-sm leading-relaxed">{a.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FAQ Section */}
      <section className="py-20 lg:py-28 bg-slate-50">
        <div className="max-w-3xl mx-auto px-6">
          <div className="text-center mb-16">
            <span className="text-[#059669] font-medium text-sm tracking-wider uppercase">帮助中心</span>
            <h2 className="text-3xl font-bold text-[#162E4D] mt-2">常见问题</h2>
          </div>
          <div className="space-y-4">
            {faqs.map((faq) => (
              <FAQItem key={faq.q} question={faq.q} answer={faq.a} />
            ))}
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="bg-[#162E4D] py-16">
        <div className="max-w-4xl mx-auto px-6 text-center">
          <h2 className="text-2xl font-bold text-white mb-3">准备好开始了吗？</h2>
          <p className="text-[#7FA8CF] mb-8">登录系统后，即可开始您的标书智能分析之旅</p>
          <a href="/chat" className="inline-flex items-center gap-2 bg-[#059669] hover:bg-[#047857] text-white px-10 py-4 rounded-lg font-medium transition shadow-lg hover:shadow-xl text-lg cursor-pointer">
            进入分析平台
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5-5m5 5H6" /></svg>
          </a>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-[#0F2340] py-8">
        <div className="max-w-6xl mx-auto px-6 text-center text-sm">
          <p className="text-[#7FA8CF]">标书分析助手 &middot; RAGFlow 驱动的智能招标分析系统</p>
          <p className="text-[#4F8BBF] mt-1">&copy; {new Date().getFullYear()} All Rights Reserved</p>
        </div>
      </footer>
    </div>
  );
}

const features = [
  { title: '智能问答', desc: '基于知识库内容的精准问答，自动关联招标文件原文并标注出处页码，确保信息准确可溯源。', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z', bg: 'bg-[#EFF3F8]', color: 'text-[#1E3A5F]' },
  { title: '关键信息提取', desc: '自动提炼预算金额、评分标准、技术要求、商务条款等核心维度，结构化呈现。', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 012 2v2m2 4h16a2 2 0 002 2V9a2 2 0 00-2-2h-2m-6 4h12a2 2 0 002 2v2M3 10h18', bg: 'bg-[#059669]/10', color: 'text-[#059669]' },
  { title: '多采购包对比', desc: '支持同一标书中多个采购包的横向对比分析，快速发现差异与优劣。', icon: 'M4 6h16M4 10h16M4 14h16M4 18h16', bg: 'bg-blue-50', color: 'text-blue-600' },
  { title: '评审规则解析', desc: '自动识别评审方法、权重分配、评分标准，帮助制定最优投标策略。', icon: 'M12 8v4l3 3m6 3v-4a9 9 0 11-18 0 9 9 0 0118 0', bg: 'bg-amber-50', color: 'text-amber-600' },
  { title: '合规性检查', desc: '自动标注实质性要求和禁止项，避免因疏忽导致废标。', icon: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0', bg: 'bg-emerald-50', color: 'text-emerald-600' },
  { title: '来源精准标注', desc: '每条分析结果均标注文件来源和页码，支持原文追溯验证。', icon: 'M12 15v2m-6 4h6m2 6h-2m2-10h.01M8 9l4-4 4 4', bg: 'bg-violet-50', color: 'text-violet-600' },
];

const steps = [
  { num: '1', title: '上传标书', desc: '将招标文件（PDF）上传至知识库系统，系统自动进行文档解析与切片。', bg: 'bg-[#EFF3F8]', text: 'text-[#1E3A5F]' },
  { num: '2', title: '智能分析', desc: '通过自然语言提问，AI 自动检索知识库并生成结构化分析结果。', bg: 'bg-[#059669]/10', text: 'text-[#059669]' },
  { num: '3', title: '辅助决策', desc: '基于分析结果，快速掌握标书要点，制定最优投标方案。', bg: 'bg-[#1E3A5F]', text: 'text-white' },
];

const scenarios = [
  { title: '政府采购', desc: '自动解析政府公开招标文件，提取预算金额、资质要求、评分标准等关键信息，助力企业高效参与政府采购项目。', icon: 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0h5m-4 0a2 2 0 01-2-2V6a2 2 0 012-2 2 2v2m0 0h2a2 2 0 012 2v2m0 0h2a2 2 0 012 2v2' },
  { title: '工程建设', desc: '解析技术规范、施工要求、验收标准等专业条款，帮助施工单位快速理解招标要求。', icon: 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2M8 10h.01M12 10h.01M16 10h.01M9 16h.01M13 16h.01M16 16h.01' },
  { title: 'IT 信息化', desc: '解析技术指标、服务级别要求、运维条款等内容，辅助企业进行精准报价和方案设计。', icon: 'M9.75 17L9 20l-1 1h8l-1-1-.75-.75M12 12.75a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM9.75 9a.75.75 0 100-1.5.75.75 0 000 1.5z' },
  { title: '医疗器械', desc: '解析注册资质要求、技术参数指标、售后服务条款，确保投标响应完整合规。', icon: 'M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z' },
  { title: '教育采购', desc: '智能解析教育装备、教学服务类招标文件，提取评分细则和技术要求。', icon: 'M12 14l9-5-9-5-9 5 9 5zM12 14l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14z' },
  { title: '物业服务', desc: '解析合同条款、人员配置要求、服务标准与考核细则，辅助物业公司精准投标。', icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2-2m-2 2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6' },
];

const advantages = [
  { title: 'RAG 检索增强生成', desc: '采用先进的 RAG 技术，AI 回答基于招标文件原文，避免幻觉，确保分析结果准确可信。', icon: 'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z' },
  { title: '深度文档理解', desc: '支持 PDF、Word 等多种格式，自动识别表格、章节、附件等复杂结构，精准提取关键信息。', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
  { title: '企业级数据安全', desc: '支持私有化部署，数据全程加密存储与传输，满足政企客户对数据安全的严格要求。', icon: 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z' },
  { title: '灵活可扩展', desc: '基于 RAGFlow 开源引擎构建，支持自定义知识库、智能体配置，可根据业务需求灵活扩展。', icon: 'M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z' },
];

const faqs = [
  { q: '支持哪些招标文件格式？', a: '目前支持 PDF 和 Word（.docx）格式的招标文件。系统会自动进行文档解析、切片和向量化处理，无需手动排版。' },
  { q: '分析结果准确吗？', a: '采用 RAG 检索增强生成技术，AI 回答基于招标文件原文内容，每条结果均标注来源页码，支持原文追溯验证，关键信息提取准确率达 95% 以上。' },
  { q: '数据安全如何保障？', a: '系统支持私有化部署，所有数据全程加密存储与传输。您的招标文件和分析结果不会被用于模型训练，完全满足政企数据安全合规要求。' },
  { q: '是否支持多个采购包对比？', a: '支持。上传包含多个采购包的招标文件后，系统可以自动识别不同采购包的内容，并支持横向对比分析，帮助您快速发现差异与优劣。' },
  { q: '如何开始使用？', a: '点击页面顶部的"开始使用"按钮，注册登录后即可上传招标文件并进行智能分析。系统提供免费试用额度，让您先体验再决定。' },
];

function FAQItem({ question, answer }: { question: string; answer: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-white rounded-xl border border-slate-100 overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-6 py-5 text-left cursor-pointer hover:bg-slate-50 transition"
        onClick={() => setOpen(!open)}
      >
        <span className="text-base font-medium text-[#162E4D] pr-4">{question}</span>
        <svg
          className={`w-5 h-5 text-slate-400 shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="px-6 pb-5 text-slate-500 text-sm leading-relaxed border-t border-slate-100 pt-4">
          {answer}
        </div>
      )}
    </div>
  );
}

// 滚动容器样式 - 不受 global.less 影响
const scrollContainerStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  overflowY: 'auto',
  overflowX: 'hidden',
};

injectScrollStyle();

function injectScrollStyle() {
  if (document.getElementById('c-landing-scrollbar')) return;
  const style = document.createElement('style');
  style.id = 'c-landing-scrollbar';
  style.textContent = `
    .c-landing-scroll { scrollbar-width: none; }
    .c-landing-scroll::-webkit-scrollbar { display: none; }
  `;
  document.head.appendChild(style);
}
