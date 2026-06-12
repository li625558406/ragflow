import DynamicIcon from '@/components/dynamic-icon';
import { ArrowRight, ChevronDown, Menu, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

export default function CLanding() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.1, rootMargin: '0px 0px -40px 0px' },
    );
    document
      .querySelectorAll(
        '.ds-reveal, .ds-scale-in, .ds-slide-left, .ds-slide-right, .ds-blur-in',
      )
      .forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  // Parallax scroll effect
  useEffect(() => {
    const scrollEl = document.querySelector('.c-landing-scroll');
    if (!scrollEl) return;
    const elements = document.querySelectorAll('.ds-parallax');
    const handler = () => {
      const st = scrollEl.scrollTop;
      elements.forEach((el) => {
        const speed = parseFloat(
          (el as HTMLElement).dataset.parallaxSpeed || '-0.15',
        );
        (el as HTMLElement).style.transform = `translateY(${st * speed}px)`;
      });
    };
    scrollEl.addEventListener('scroll', handler, { passive: true });
    return () => scrollEl.removeEventListener('scroll', handler);
  }, []);

  return (
    <div className="c-landing-scroll bg-gradient-to-b from-[#F0F4FF] via-[#F5F0FF] to-[#FFF0F5] text-[#000000] antialiased">
      {/* Navigation */}
      <nav className="sticky top-0 z-50 bg-white border-b border-[#D4D4D4]">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-base font-bold tracking-tight text-[#000000]">
              标书分析助手
            </span>
          </div>
          <div className="hidden md:flex items-center gap-8">
            <a
              href="#features"
              className="text-sm text-[#333333] hover:text-[#000000] transition-colors"
            >
              功能特性
            </a>
            <a
              href="#flow"
              className="text-sm text-[#333333] hover:text-[#000000] transition-colors"
            >
              使用流程
            </a>
            <a
              href="#scenarios"
              className="text-sm text-[#333333] hover:text-[#000000] transition-colors"
            >
              应用场景
            </a>
            <a
              href="#faq"
              className="text-sm text-[#333333] hover:text-[#000000] transition-colors"
            >
              常见问题
            </a>
          </div>
          <a
            href="/home"
            className="hidden md:inline-flex bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium px-5 py-2 rounded-lg transition-colors cursor-pointer"
          >
            开始使用
          </a>
          <button
            className="md:hidden text-[#333333]"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            aria-label="菜单"
          >
            {mobileMenuOpen ? (
              <X className="w-6 h-6" strokeWidth={2} />
            ) : (
              <Menu className="w-6 h-6" strokeWidth={2} />
            )}
          </button>
        </div>
        {mobileMenuOpen && (
          <div className="md:hidden border-t border-[#D4D4D4] px-6 py-4 bg-white space-y-3">
            <a
              href="#features"
              onClick={() => setMobileMenuOpen(false)}
              className="block text-sm text-[#333333] hover:text-[#000000] py-2"
            >
              功能特性
            </a>
            <a
              href="#flow"
              onClick={() => setMobileMenuOpen(false)}
              className="block text-sm text-[#333333] hover:text-[#000000] py-2"
            >
              使用流程
            </a>
            <a
              href="#scenarios"
              onClick={() => setMobileMenuOpen(false)}
              className="block text-sm text-[#333333] hover:text-[#000000] py-2"
            >
              应用场景
            </a>
            <a
              href="#faq"
              onClick={() => setMobileMenuOpen(false)}
              className="block text-sm text-[#333333] hover:text-[#000000] py-2"
            >
              常见问题
            </a>
            <a
              href="/home"
              className="block bg-[#000000] text-white text-center px-5 py-2.5 rounded-lg text-sm font-medium"
            >
              开始使用
            </a>
          </div>
        )}
      </nav>

      {/* Hero */}
      <section className="relative overflow-hidden">
        {/* Floating decorative blobs */}
        <div
          className="absolute inset-0 pointer-events-none overflow-hidden"
          aria-hidden
        >
          <div
            className="ds-parallax absolute top-[15%] left-[10%] w-64 h-64 rounded-full bg-[#E0E7FF] opacity-40 blur-3xl animate-[ds-hero-float_12s_ease-in-out_infinite]"
            data-parallax-speed="-0.25"
          />
          <div
            className="ds-parallax absolute top-[50%] right-[5%] w-80 h-80 rounded-full bg-[#FCE7F3] opacity-40 blur-3xl animate-[ds-hero-float_15s_ease-in-out_infinite_2s]"
            data-parallax-speed="-0.12"
          />
          <div
            className="ds-parallax absolute bottom-[10%] left-[30%] w-48 h-48 rounded-full bg-[#D1FAE5] opacity-40 blur-3xl animate-[ds-hero-float_10s_ease-in-out_infinite_4s]"
            data-parallax-speed="-0.18"
          />
        </div>
        <div className="max-w-5xl mx-auto px-6 pt-20 pb-24 lg:pt-32 lg:pb-36 relative z-10">
          <div className="max-w-2xl">
            <div className="ds-reveal mb-8">
              <span className="inline-flex items-center gap-2 text-sm font-medium text-[#525252] border border-[#1a1a1a] rounded-full px-4 py-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#10B981] animate-pulse" />
                AI 驱动 · 智能分析
              </span>
            </div>
            <h1 className="ds-reveal ds-reveal-d1 text-4xl sm:text-5xl lg:text-[56px] font-bold leading-[1.08] tracking-tight text-[#000000] mb-6">
              招标文件
              <br />
              <span>智能分析</span>
              平台
            </h1>
            <p className="ds-reveal ds-reveal-d2 text-lg text-[#525252] leading-relaxed max-w-xl mb-10">
              基于大语言模型与知识库检索技术，为政府采购、投标决策提供精准的招标文件分析、关键信息提取与智能问答服务。
            </p>
            <div className="ds-reveal ds-reveal-d3 flex flex-wrap gap-3">
              <a
                href="/home"
                className="ds-btn-hover inline-flex items-center gap-2 bg-[#000000] hover:bg-[#171717] text-white text-sm font-semibold px-6 py-3 rounded-lg cursor-pointer"
              >
                立即体验
                <ArrowRight className="w-4 h-4" strokeWidth={2} />
              </a>
              <a
                href="#features"
                className="ds-btn-hover inline-flex items-center gap-2 text-sm font-medium text-[#525252] hover:text-[#000000] px-6 py-3 rounded-lg border border-[#D4D4D4] hover:border-[#000000] cursor-pointer"
              >
                了解更多
              </a>
            </div>
          </div>

          {/* Stats */}
          <div className="ds-reveal ds-reveal-d4 mt-20 grid grid-cols-3 gap-4 max-w-lg">
            <div className="text-center py-5 px-4 rounded-xl border border-[#E5E5E5] ds-card-lift">
              <div className="text-3xl sm:text-4xl font-bold text-[#000000]">
                <CountUp end={10} suffix="s" />
              </div>
              <div className="text-sm text-[#525252] mt-1">平均响应时间</div>
            </div>
            <div className="text-center py-5 px-4 rounded-xl border border-[#E5E5E5] ds-card-lift">
              <div className="text-3xl sm:text-4xl font-bold text-[#000000]">
                <CountUp end={95} suffix="%" />
              </div>
              <div className="text-sm text-[#525252] mt-1">信息提取准确率</div>
            </div>
            <div className="text-center py-5 px-4 rounded-xl border border-[#E5E5E5] ds-card-lift">
              <div className="text-3xl sm:text-4xl font-bold text-[#000000]">
                <CountUp end={100} suffix="+" />
              </div>
              <div className="text-sm text-[#525252] mt-1">支持文件格式</div>
            </div>
          </div>
        </div>
      </section>

      <Divider />

      {/* Features */}
      <section id="features" className="py-24 lg:py-32">
        <div className="max-w-5xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#000000] tracking-[0.1em] uppercase">
              核心能力
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#000000] mt-3">
              六大核心功能
            </h2>
            <p className="text-[#333333] mt-3 max-w-md mx-auto text-base">
              覆盖招标文件分析全流程，让投标决策更高效
            </p>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {features.map((f, i) => (
              <div
                key={f.title}
                className={`ds-scale-in ds-reveal-d${Math.min(i + 1, 6)} ds-card-lift bg-white rounded-xl p-7 border border-[#D4D4D4] hover:border-[#000000]`}
              >
                <div
                  className={`w-10 h-10 rounded-lg flex items-center justify-center mb-5 ${f.bg}`}
                >
                  <DynamicIcon
                    name={f.icon}
                    className={`w-5 h-5 ${f.color}`}
                    strokeWidth={1.5}
                  />
                </div>
                <h3 className="text-base font-semibold text-[#000000] mb-2">
                  {f.title}
                </h3>
                <p className="text-sm text-[#333333] leading-relaxed">
                  {f.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <Divider />

      {/* Flow */}
      <section id="flow" className="py-24 lg:py-32">
        <div className="max-w-5xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#000000] tracking-[0.1em] uppercase">
              简单高效
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#000000] mt-3">
              三步完成分析
            </h2>
            <p className="text-[#333333] mt-3 max-w-md mx-auto text-base">
              从上传到智能分析，全程简洁高效
            </p>
          </div>
          <div className="grid md:grid-cols-3 gap-8">
            {steps.map((s, i) => (
              <div
                key={s.title}
                className={`ds-scale-in ds-reveal-d${i + 1} ds-card-lift text-center py-8 px-6 bg-[#FFFFFF] rounded-xl border border-[#D4D4D4]`}
              >
                <div className="w-14 h-14 bg-[#F0F4FF] rounded-xl mx-auto flex items-center justify-center mb-5">
                  <span className="font-bold text-lg text-[#4F46E5]">
                    {s.num}
                  </span>
                </div>
                <h3 className="text-lg font-semibold text-[#000000] mb-2">
                  {s.title}
                </h3>
                <p className="text-sm text-[#333333] leading-relaxed max-w-[260px] mx-auto">
                  {s.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <Divider />

      {/* Scenarios */}
      <section id="scenarios" className="py-24 lg:py-32">
        <div className="max-w-5xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#000000] tracking-[0.1em] uppercase">
              广泛应用
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#000000] mt-3">
              应用场景
            </h2>
            <p className="text-[#333333] mt-3 max-w-md mx-auto text-base">
              覆盖政府采购、工程建设、信息技术等多个招投标领域
            </p>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {scenarios.map((s, i) => (
              <div
                key={s.title}
                className={`${i % 2 === 0 ? 'ds-slide-left' : 'ds-slide-right'} ds-reveal-d${Math.min(i + 1, 6)} ds-card-lift bg-white rounded-xl p-7 border border-[#D4D4D4] hover:border-[#000000]`}
              >
                <div className="w-10 h-10 rounded-lg bg-[#EAEAEA] flex items-center justify-center mb-5">
                  <DynamicIcon
                    name={s.icon}
                    className="w-5 h-5 text-[#000000]"
                    strokeWidth={1.5}
                  />
                </div>
                <h3 className="text-base font-semibold text-[#000000] mb-2">
                  {s.title}
                </h3>
                <p className="text-sm text-[#333333] leading-relaxed">
                  {s.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <Divider />

      {/* Advantages */}
      <section className="py-24 lg:py-32">
        <div className="max-w-5xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#000000] tracking-[0.1em] uppercase">
              为什么选择我们
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#000000] mt-3">
              技术优势
            </h2>
            <p className="text-[#333333] mt-3 max-w-md mx-auto text-base">
              基于 RAGFlow 开源引擎，企业级安全可靠
            </p>
          </div>
          <div className="grid md:grid-cols-2 gap-5">
            {advantages.map((a, i) => (
              <div
                key={a.title}
                className={`ds-blur-in ds-reveal-d${Math.min(i + 1, 6)} ds-card-lift flex gap-5 p-7 rounded-xl border border-[#D4D4D4] hover:border-[#000000] bg-white`}
              >
                <div
                  className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${a.bg}`}
                >
                  <DynamicIcon
                    name={a.icon}
                    className={`w-5 h-5 ${a.color}`}
                    strokeWidth={1.5}
                  />
                </div>
                <div>
                  <h3 className="text-base font-semibold text-[#000000] mb-1.5">
                    {a.title}
                  </h3>
                  <p className="text-sm text-[#333333] leading-relaxed">
                    {a.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <Divider />

      {/* FAQ */}
      <section id="faq" className="py-24 lg:py-32">
        <div className="max-w-3xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#000000] tracking-[0.1em] uppercase">
              帮助中心
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#000000] mt-3">
              常见问题
            </h2>
          </div>
          <div className="space-y-3 ds-reveal">
            {faqs.map((faq) => (
              <FAQItem key={faq.q} question={faq.q} answer={faq.a} />
            ))}
          </div>
        </div>
      </section>

      <Divider />

      {/* CTA */}
      <section className="py-24 lg:py-32">
        <div className="max-w-5xl mx-auto px-6 text-center ds-scale-in">
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#000000] mb-4">
            准备好开始了吗？
          </h2>
          <p className="text-[#525252] mb-10 max-w-md mx-auto text-base">
            登录系统后，即可开始您的标书智能分析之旅
          </p>
          <a
            href="/home"
            className="ds-btn-hover inline-flex items-center gap-2 bg-white hover:bg-[#EAEAEA] text-[#000000] text-sm font-semibold px-8 py-3.5 rounded-lg cursor-pointer"
          >
            进入分析平台
            <ArrowRight className="w-4 h-4" strokeWidth={2.5} />
          </a>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-8 border-t border-[#E5E5E5]">
        <div className="max-w-5xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-3">
          <span className="text-sm text-[#333333]">标书分析助手</span>
          <p className="text-xs text-[#525252]">
            &copy; {new Date().getFullYear()} All Rights Reserved
          </p>
        </div>
      </footer>

      <LandingStyles />
    </div>
  );
}

/* ── Data ─────────────────────────────────────────────────── */

const features = [
  {
    title: '智能问答',
    desc: '基于知识库内容的精准问答，自动关联招标文件原文并标注出处页码，确保信息准确可溯源。',
    icon: 'messages-square',
    bg: 'bg-[#EAEAEA]',
    color: 'text-[#000000]',
  },
  {
    title: '关键信息提取',
    desc: '自动提炼预算金额、评分标准、技术要求、商务条款等核心维度，结构化呈现。',
    icon: 'file-search-2',
    bg: 'bg-[#DCFCE7]',
    color: 'text-[#059669]',
  },
  {
    title: '多采购包对比',
    desc: '支持同一标书中多个采购包的横向对比分析，快速发现差异与优劣。',
    icon: 'arrow-left-right',
    bg: 'bg-[#FEF9C3]',
    color: 'text-[#CA8A04]',
  },
  {
    title: '评审规则解析',
    desc: '自动识别评审方法、权重分配、评分标准，帮助制定最优投标策略。',
    icon: 'gavel',
    bg: 'bg-[#FEE2E2]',
    color: 'text-[#DC2626]',
  },
  {
    title: '合规性检查',
    desc: '自动标注实质性要求和禁止项，避免因疏忽导致废标。',
    icon: 'shield-check',
    bg: 'bg-[#EAEAEA]',
    color: 'text-[#1a1a1a]',
  },
  {
    title: '来源精准标注',
    desc: '每条分析结果均标注文件来源和页码，支持原文追溯验证。',
    icon: 'bookmark-check',
    bg: 'bg-[#F3E8FF]',
    color: 'text-[#7C3AED]',
  },
];

const steps = [
  {
    num: '01',
    title: '上传标书',
    desc: '将招标文件上传至知识库系统，自动进行文档解析与切片处理',
  },
  {
    num: '02',
    title: '智能分析',
    desc: '通过自然语言提问，AI 自动检索知识库并生成结构化分析结果',
  },
  {
    num: '03',
    title: '辅助决策',
    desc: '基于分析结果，快速掌握标书要点，制定最优投标方案',
  },
];

const scenarios = [
  {
    title: '政府采购',
    desc: '自动解析政府公开招标文件，提取预算金额、资质要求、评分标准等关键信息，助力企业高效参与政府采购项目。',
    icon: 'landmark',
  },
  {
    title: '工程建设',
    desc: '解析技术规范、施工要求、验收标准等专业条款，帮助施工单位快速理解招标要求。',
    icon: 'hard-hat',
  },
  {
    title: 'IT 信息化',
    desc: '解析技术指标、服务级别要求、运维条款等内容，辅助企业进行精准报价和方案设计。',
    icon: 'monitor',
  },
  {
    title: '医疗器械',
    desc: '解析注册资质要求、技术参数指标、售后服务条款，确保投标响应完整合规。',
    icon: 'heart-pulse',
  },
  {
    title: '教育采购',
    desc: '智能解析教育装备、教学服务类招标文件，提取评分细则和技术要求。',
    icon: 'graduation-cap',
  },
  {
    title: '物业服务',
    desc: '解析合同条款、人员配置要求、服务标准与考核细则，辅助物业公司精准投标。',
    icon: 'building-2',
  },
];

const advantages = [
  {
    title: 'RAG 检索增强生成',
    desc: '采用先进的 RAG 技术，AI 回答基于招标文件原文，避免幻觉，确保分析结果准确可信。',
    icon: 'sparkles',
    bg: 'bg-[#EAEAEA]',
    color: 'text-[#000000]',
  },
  {
    title: '深度文档理解',
    desc: '支持 PDF、Word 等多种格式，自动识别表格、章节、附件等复杂结构，精准提取关键信息。',
    icon: 'file-search-2',
    bg: 'bg-[#EAEAEA]',
    color: 'text-[#1a1a1a]',
  },
  {
    title: '企业级数据安全',
    desc: '支持私有化部署，数据全程加密存储与传输，满足政企客户对数据安全的严格要求。',
    icon: 'lock',
    bg: 'bg-[#DCFCE7]',
    color: 'text-[#059669]',
  },
  {
    title: '灵活可扩展',
    desc: '基于 RAGFlow 开源引擎构建，支持自定义知识库、智能体配置，可根据业务需求灵活扩展。',
    icon: 'blocks',
    bg: 'bg-[#FEF9C3]',
    color: 'text-[#CA8A04]',
  },
];

const faqs = [
  {
    q: '支持哪些招标文件格式？',
    a: '目前支持 PDF 和 Word（.docx）格式的招标文件。系统会自动进行文档解析、切片和向量化处理，无需手动排版。',
  },
  {
    q: '分析结果准确吗？',
    a: '采用 RAG 检索增强生成技术，AI 回答基于招标文件原文内容，每条结果均标注来源页码，支持原文追溯验证，关键信息提取准确率达 95% 以上。',
  },
  {
    q: '数据安全如何保障？',
    a: '系统支持私有化部署，所有数据全程加密存储与传输。您的招标文件和分析结果不会被用于模型训练，完全满足政企数据安全合规要求。',
  },
  {
    q: '是否支持多个采购包对比？',
    a: '支持。上传包含多个采购包的招标文件后，系统可以自动识别不同采购包的内容，并支持横向对比分析，帮助您快速发现差异与优劣。',
  },
  {
    q: '如何开始使用？',
    a: '点击页面顶部的"开始使用"按钮，注册登录后即可上传招标文件并进行智能分析。系统提供免费试用额度，让您先体验再决定。',
  },
];

/* ── FAQ Component ───────────────────────────────────────── */

function FAQItem({ question, answer }: { question: string; answer: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-white rounded-xl border border-[#D4D4D4] overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-7 py-5 text-left cursor-pointer hover:bg-[#FFFFFF] transition-colors"
        onClick={() => setOpen(!open)}
      >
        <span className="text-base font-medium text-[#000000] pr-4">
          {question}
        </span>
        <ChevronDown
          className={`w-5 h-5 text-[#000000] shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          strokeWidth={2}
        />
      </button>
      {open && (
        <div className="px-7 pb-5 text-[#333333] text-sm leading-relaxed border-t border-[#D4D4D4] pt-4">
          {answer}
        </div>
      )}
    </div>
  );
}

/* ── Styles ───────────────────────────────────────────────── */

function CountUp({
  end,
  suffix = '',
  duration = 1500,
}: {
  end: number;
  suffix?: string;
  duration?: number;
}) {
  const [val, setVal] = useState(0);
  const ref = useRef<HTMLSpanElement>(null);
  const triggered = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !triggered.current) {
          triggered.current = true;
          const start = performance.now();
          const tick = (now: number) => {
            const p = Math.min((now - start) / duration, 1);
            const eased = 1 - Math.pow(1 - p, 3); // ease-out cubic
            setVal(Math.round(eased * end));
            if (p < 1) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
          obs.disconnect();
        }
      },
      { threshold: 0.5 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [end, duration]);

  return (
    <span ref={ref}>
      {val}
      {suffix}
    </span>
  );
}

function Divider() {
  return (
    <div className="max-w-5xl mx-auto px-6">
      <div
        className="h-px w-full"
        style={{
          background:
            'linear-gradient(to right, transparent, #D4D4D4 20%, #D4D4D4 80%, transparent)',
        }}
      />
    </div>
  );
}

function LandingStyles() {
  const injected = useRef(false);
  useEffect(() => {
    if (injected.current) return;
    injected.current = true;

    const id = 'c-landing-styles';
    if (document.getElementById(id)) return;
    const style = document.createElement('style');
    style.id = id;
    style.textContent = `
      .c-landing-scroll { position: fixed; inset: 0; overflow-y: auto; overflow-x: hidden; scrollbar-width: none; }
      .c-landing-scroll::-webkit-scrollbar { display: none; }
      .ds-reveal { opacity: 0; transform: translateY(20px); transition: opacity 0.6s cubic-bezier(0.22, 1, 0.36, 1), transform 0.6s cubic-bezier(0.22, 1, 0.36, 1); }
      .ds-reveal.visible { opacity: 1; transform: translateY(0); }
      .ds-reveal-d1 { transition-delay: 0.06s; }
      .ds-reveal-d2 { transition-delay: 0.12s; }
      .ds-reveal-d3 { transition-delay: 0.18s; }
      .ds-reveal-d4 { transition-delay: 0.24s; }
      .ds-reveal-d5 { transition-delay: 0.30s; }
      .ds-reveal-d6 { transition-delay: 0.36s; }
      .ds-reveal-d7 { transition-delay: 0.42s; }
      .ds-reveal-d8 { transition-delay: 0.48s; }
      .ds-reveal-d9 { transition-delay: 0.54s; }
      .ds-scale-in { opacity: 0; transform: scale(0.92); transition: opacity 0.6s cubic-bezier(0.22, 1, 0.36, 1), transform 0.6s cubic-bezier(0.22, 1, 0.36, 1); }
      .ds-scale-in.visible { opacity: 1; transform: scale(1); }
      .ds-slide-left { opacity: 0; transform: translateX(-24px); transition: opacity 0.6s cubic-bezier(0.22, 1, 0.36, 1), transform 0.6s cubic-bezier(0.22, 1, 0.36, 1); }
      .ds-slide-left.visible { opacity: 1; transform: translateX(0); }
      .ds-slide-right { opacity: 0; transform: translateX(24px); transition: opacity 0.6s cubic-bezier(0.22, 1, 0.36, 1), transform 0.6s cubic-bezier(0.22, 1, 0.36, 1); }
      .ds-slide-right.visible { opacity: 1; transform: translateX(0); }
      .ds-blur-in { opacity: 0; filter: blur(8px); transition: opacity 0.7s cubic-bezier(0.22, 1, 0.36, 1), filter 0.7s cubic-bezier(0.22, 1, 0.36, 1); }
      .ds-blur-in.visible { opacity: 1; filter: blur(0); }
      .ds-parallax { will-change: transform; }
      .ds-card-lift { transition: transform 0.2s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.2s cubic-bezier(0.22, 1, 0.36, 1), border-color 0.2s ease; }
      .ds-card-lift:hover { transform: translateY(-4px); box-shadow: 0 12px 32px rgba(0,0,0,0.06); }
      .ds-btn-hover { transition: all 0.2s cubic-bezier(0.22, 1, 0.36, 1); }
      .ds-btn-hover:hover { transform: scale(1.03); }
      .ds-btn-hover:active { transform: scale(0.98); }
      @keyframes ds-hero-float {
        0%, 100% { transform: translateY(0) rotate(0deg); opacity: 0.06; }
        33% { transform: translateY(-20px) rotate(1deg); opacity: 0.04; }
        66% { transform: translateY(-10px) rotate(-1deg); opacity: 0.08; }
      }
    `;
    document.head.appendChild(style);
  }, []);
  return null;
}
