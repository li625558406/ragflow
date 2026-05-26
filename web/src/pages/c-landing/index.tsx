import { useEffect, useRef, useState } from 'react';

export default function CLanding() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Scroll reveal animation
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
      .querySelectorAll('.ds-reveal')
      .forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  return (
    <div className="c-landing-scroll bg-[#f8f6f3] text-[#2d2d4a] antialiased">
      {/* Navigation */}
      <nav className="sticky top-0 z-50 bg-white/80 backdrop-blur-xl border-b border-[rgba(124,92,252,0.06)]">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-[#7c5cfc] to-[#a78bfa] flex items-center justify-center shadow-sm">
              <svg
                className="w-4 h-4 text-white"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
            </div>
            <span className="text-[15px] font-semibold tracking-[-0.02em] text-[#1c1c2e]">
              标书分析助手
            </span>
          </div>
          <div className="hidden md:flex items-center gap-8">
            <a
              href="#features"
              className="text-sm text-[#5a5a7a] hover:text-[#7c5cfc] transition-colors tracking-[-0.01em]"
            >
              功能特性
            </a>
            <a
              href="#flow"
              className="text-sm text-[#5a5a7a] hover:text-[#7c5cfc] transition-colors tracking-[-0.01em]"
            >
              使用流程
            </a>
            <a
              href="#scenarios"
              className="text-sm text-[#5a5a7a] hover:text-[#7c5cfc] transition-colors tracking-[-0.01em]"
            >
              应用场景
            </a>
            <a
              href="#faq"
              className="text-sm text-[#5a5a7a] hover:text-[#7c5cfc] transition-colors tracking-[-0.01em]"
            >
              常见问题
            </a>
          </div>
          <a
            href="/home"
            className="hidden md:inline-flex bg-gradient-to-r from-[#7c5cfc] to-[#a78bfa] hover:from-[#6b4ce0] hover:to-[#9678e8] text-white text-sm font-medium px-5 py-2 rounded-[50px] transition-all duration-200 cursor-pointer shadow-md shadow-[rgba(124,92,252,0.2)] hover:shadow-lg hover:shadow-[rgba(124,92,252,0.3)] hover:-translate-y-0.5 tracking-[-0.01em]"
          >
            开始使用
          </a>
          <button
            className="md:hidden text-[#5a5a7a]"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            aria-label="菜单"
          >
            <svg
              className="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              {mobileMenuOpen ? (
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              ) : (
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 6h16M4 12h16M4 18h16"
                />
              )}
            </svg>
          </button>
        </div>
        {mobileMenuOpen && (
          <div className="md:hidden border-t border-[rgba(124,92,252,0.06)] px-6 py-4 bg-white space-y-3">
            <a
              href="#features"
              onClick={() => setMobileMenuOpen(false)}
              className="block text-sm text-[#5a5a7a] hover:text-[#7c5cfc] py-2"
            >
              功能特性
            </a>
            <a
              href="#flow"
              onClick={() => setMobileMenuOpen(false)}
              className="block text-sm text-[#5a5a7a] hover:text-[#7c5cfc] py-2"
            >
              使用流程
            </a>
            <a
              href="#scenarios"
              onClick={() => setMobileMenuOpen(false)}
              className="block text-sm text-[#5a5a7a] hover:text-[#7c5cfc] py-2"
            >
              应用场景
            </a>
            <a
              href="#faq"
              onClick={() => setMobileMenuOpen(false)}
              className="block text-sm text-[#5a5a7a] hover:text-[#7c5cfc] py-2"
            >
              常见问题
            </a>
            <a
              href="/home"
              className="block bg-gradient-to-r from-[#7c5cfc] to-[#a78bfa] text-white text-center px-5 py-2.5 rounded-[50px] text-sm font-medium tracking-[-0.01em]"
            >
              开始使用
            </a>
          </div>
        )}
      </nav>

      {/* Hero Section */}
      <section className="relative overflow-hidden">
        <div className="hero-gradient-cs">
          {/* Decorative floating shapes */}
          <div className="absolute top-20 left-10 w-64 h-64 rounded-full bg-gradient-to-br from-[#f472b6]/20 to-[#fb923c]/10 blur-3xl pointer-events-none animate-float-slow" />
          <div
            className="absolute bottom-10 right-20 w-80 h-80 rounded-full bg-gradient-to-br from-[#7c5cfc]/15 to-[#a78bfa]/10 blur-3xl pointer-events-none animate-float-slow"
            style={{ animationDelay: '-3s' }}
          />
          <div className="absolute top-40 right-1/3 w-40 h-40 rounded-full bg-gradient-to-br from-[#2ec4b6]/20 to-[#7c5cfc]/10 blur-2xl pointer-events-none" />

          <div className="max-w-6xl mx-auto px-6 pt-20 pb-24 lg:pt-32 lg:pb-36 relative z-10">
            <div className="max-w-3xl">
              <div className="ds-reveal">
                <span className="inline-flex items-center gap-2 text-sm font-medium text-white bg-white/15 rounded-[50px] px-4 py-1.5 mb-8 tracking-[-0.01em] backdrop-blur-sm border border-white/10">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#2ec4b6] animate-pulse" />
                  AI 驱动 · 智能分析
                </span>
              </div>
              <h1 className="ds-reveal ds-reveal-d1 text-4xl sm:text-5xl lg:text-[60px] font-bold leading-[1.05] tracking-[-0.04em] text-white mb-6">
                招标文件
                <br />
                <span className="bg-gradient-to-r from-[#f472b6] via-[#fb923c] to-[#facc15] bg-clip-text text-transparent">
                  智能分析
                </span>
                平台
              </h1>
              <p className="ds-reveal ds-reveal-d2 text-lg text-white/70 leading-relaxed max-w-xl mb-10 tracking-[-0.01em]">
                基于大语言模型与知识库检索技术，为政府采购、投标决策提供精准的招标文件分析、关键信息提取与智能问答服务。
              </p>
              <div className="ds-reveal ds-reveal-d3 flex flex-wrap gap-3">
                <a
                  href="/home"
                  className="inline-flex items-center gap-2 bg-white hover:bg-white/90 text-[#1c1c2e] text-sm font-semibold px-6 py-3 rounded-[50px] transition-all duration-200 cursor-pointer shadow-lg shadow-black/10 hover:shadow-xl hover:-translate-y-0.5 tracking-[-0.01em]"
                >
                  立即体验
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2.5}
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M13 7l5 5m0 0l-5-5m5 5H6"
                    />
                  </svg>
                </a>
                <a
                  href="#features"
                  className="inline-flex items-center gap-2 text-sm font-medium text-white/80 hover:text-white px-6 py-3 rounded-[50px] border border-white/20 hover:border-white/40 transition-colors backdrop-blur-sm tracking-[-0.01em]"
                >
                  了解更多
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M19 9l-7 7-7-7"
                    />
                  </svg>
                </a>
              </div>
            </div>

            {/* Stats */}
            <div className="ds-reveal ds-reveal-d4 mt-20 grid grid-cols-3 gap-4 max-w-lg">
              <div className="text-center py-5 px-4 rounded-2xl bg-white/5 backdrop-blur-sm border border-white/10">
                <div className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-white">
                  10s
                </div>
                <div className="text-sm text-white/50 mt-1 font-medium tracking-[-0.01em]">
                  平均响应时间
                </div>
              </div>
              <div className="text-center py-5 px-4 rounded-2xl bg-white/5 backdrop-blur-sm border border-white/10">
                <div className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-white">
                  95%
                </div>
                <div className="text-sm text-white/50 mt-1 font-medium tracking-[-0.01em]">
                  信息提取准确率
                </div>
              </div>
              <div className="text-center py-5 px-4 rounded-2xl bg-white/5 backdrop-blur-sm border border-white/10">
                <div className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-white">
                  100+
                </div>
                <div className="text-sm text-white/50 mt-1 font-medium tracking-[-0.01em]">
                  支持文件格式
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="py-24 lg:py-32 bg-[#f8f6f3]">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#7c5cfc] tracking-[0.12em] uppercase">
              核心能力
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-[#1c1c2e] mt-3">
              六大核心功能
            </h2>
            <p className="text-[#5a5a7a] mt-3 max-w-md mx-auto text-base tracking-[-0.01em]">
              覆盖招标文件分析全流程，让投标决策更高效
            </p>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {features.map((f, i) => (
              <div
                key={f.title}
                className={`creative-card bg-white rounded-2xl p-7 border border-[rgba(124,92,252,0.06)] ds-reveal ds-reveal-d${Math.min(i + 1, 6)}`}
              >
                <div
                  className={`w-11 h-11 rounded-xl flex items-center justify-center mb-5 ${f.bg}`}
                >
                  <svg
                    className={`w-5 h-5 ${f.color}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d={f.icon}
                    />
                  </svg>
                </div>
                <h3 className="text-base font-semibold text-[#1c1c2e] mb-2 tracking-[-0.01em]">
                  {f.title}
                </h3>
                <p className="text-sm text-[#5a5a7a] leading-relaxed tracking-[-0.005em]">
                  {f.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Flow Section */}
      <section id="flow" className="py-24 lg:py-32 bg-white">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#7c5cfc] tracking-[0.12em] uppercase">
              简单高效
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-[#1c1c2e] mt-3">
              三步完成分析
            </h2>
            <p className="text-[#5a5a7a] mt-3 max-w-md mx-auto text-base tracking-[-0.01em]">
              从上传到智能分析，全程简洁高效
            </p>
          </div>
          <div className="grid md:grid-cols-3 gap-8 md:gap-6">
            {steps.map((s, i) => (
              <div
                key={s.title}
                className={`step-card ds-reveal ds-reveal-d${i + 1} text-center`}
              >
                <div
                  className={`w-16 h-16 bg-gradient-to-br ${s.bg} rounded-2xl mx-auto flex items-center justify-center mb-5 shadow-lg shadow-[${s.shadow}]`}
                >
                  <span className="font-bold text-xl text-white">{s.num}</span>
                </div>
                <h3 className="text-lg font-semibold text-[#1c1c2e] text-center mb-2 tracking-[-0.01em]">
                  {s.title}
                </h3>
                <p className="text-sm text-[#5a5a7a] text-center leading-relaxed max-w-[260px] mx-auto tracking-[-0.005em]">
                  {s.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Scenarios Section */}
      <section id="scenarios" className="py-24 lg:py-32 bg-[#f8f6f3]">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#7c5cfc] tracking-[0.12em] uppercase">
              广泛应用
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-[#1c1c2e] mt-3">
              应用场景
            </h2>
            <p className="text-[#5a5a7a] mt-3 max-w-md mx-auto text-base tracking-[-0.01em]">
              覆盖政府采购、工程建设、信息技术等多个招投标领域
            </p>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {scenarios.map((s, i) => (
              <div
                key={s.title}
                className={`creative-card bg-white rounded-2xl p-7 border border-[rgba(124,92,252,0.06)] ds-reveal ds-reveal-d${Math.min(i + 1, 6)}`}
              >
                <div className="w-11 h-11 rounded-xl bg-[#ede9fe] flex items-center justify-center mb-5">
                  <svg
                    className="w-5 h-5 text-[#7c5cfc]"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d={s.icon}
                    />
                  </svg>
                </div>
                <h3 className="text-base font-semibold text-[#1c1c2e] mb-2 tracking-[-0.01em]">
                  {s.title}
                </h3>
                <p className="text-sm text-[#5a5a7a] leading-relaxed tracking-[-0.005em]">
                  {s.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Advantages Section */}
      <section className="py-24 lg:py-32 bg-white">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#7c5cfc] tracking-[0.12em] uppercase">
              为什么选择我们
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-[#1c1c2e] mt-3">
              技术优势
            </h2>
            <p className="text-[#5a5a7a] mt-3 max-w-md mx-auto text-base tracking-[-0.01em]">
              基于 RAGFlow 开源引擎，企业级安全可靠
            </p>
          </div>
          <div className="grid md:grid-cols-2 gap-6">
            {advantages.map((a, i) => (
              <div
                key={a.title}
                className={`creative-card flex gap-5 p-7 rounded-2xl border border-[rgba(124,92,252,0.06)] bg-white ds-reveal ds-reveal-d${Math.min(i + 1, 6)}`}
              >
                <div
                  className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${a.bg}`}
                >
                  <svg
                    className={`w-5 h-5 ${a.color}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d={a.icon}
                    />
                  </svg>
                </div>
                <div>
                  <h3 className="text-base font-semibold text-[#1c1c2e] mb-1.5 tracking-[-0.01em]">
                    {a.title}
                  </h3>
                  <p className="text-sm text-[#5a5a7a] leading-relaxed tracking-[-0.005em]">
                    {a.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FAQ Section */}
      <section id="faq" className="py-24 lg:py-32 bg-[#f8f6f3]">
        <div className="max-w-3xl mx-auto px-6">
          <div className="text-center mb-16 ds-reveal">
            <span className="text-xs font-semibold text-[#7c5cfc] tracking-[0.12em] uppercase">
              帮助中心
            </span>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-[#1c1c2e] mt-3">
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

      {/* CTA Section */}
      <section className="relative overflow-hidden py-24 lg:py-32">
        <div className="cta-gradient-cs">
          <div className="absolute top-0 right-0 w-96 h-96 rounded-full bg-gradient-to-br from-[#f472b6]/20 to-transparent blur-3xl pointer-events-none" />
          <div className="absolute bottom-0 left-0 w-80 h-80 rounded-full bg-gradient-to-tr from-[#2ec4b6]/15 to-transparent blur-3xl pointer-events-none" />
          <div className="max-w-6xl mx-auto px-6 text-center relative z-10 ds-reveal">
            <h2 className="text-3xl sm:text-4xl font-bold tracking-[-0.03em] text-white mb-4">
              准备好开始了吗？
            </h2>
            <p className="text-white/60 mb-10 max-w-md mx-auto text-base tracking-[-0.01em]">
              登录系统后，即可开始您的标书智能分析之旅
            </p>
            <a
              href="/home"
              className="inline-flex items-center gap-2 bg-white hover:bg-white/90 text-[#1c1c2e] text-sm font-semibold px-8 py-3.5 rounded-[50px] transition-all duration-200 cursor-pointer shadow-xl hover:shadow-2xl hover:-translate-y-0.5 tracking-[-0.01em]"
            >
              进入分析平台
              <svg
                className="w-4 h-4"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.5}
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M13 7l5 5m0 0l-5-5m5 5H6"
                />
              </svg>
            </a>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-8 bg-white border-t border-[rgba(124,92,252,0.06)]">
        <div className="max-w-6xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-[#7c5cfc] to-[#a78bfa] flex items-center justify-center">
              <svg
                className="w-3 h-3 text-white"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
            </div>
            <span className="text-sm text-[#5a5a7a]">标书分析助手</span>
          </div>
          <p className="text-xs text-[#9494b5]">
            &copy; {new Date().getFullYear()} All Rights Reserved
          </p>
        </div>
      </footer>

      {/* Inject CSS */}
      <LandingStyles />
    </div>
  );
}

/* ── Data ─────────────────────────────────────────────────── */

const features = [
  {
    title: '智能问答',
    desc: '基于知识库内容的精准问答，自动关联招标文件原文并标注出处页码，确保信息准确可溯源。',
    icon: 'M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z',
    bg: 'bg-[#ede9fe]',
    color: 'text-[#7c5cfc]',
  },
  {
    title: '关键信息提取',
    desc: '自动提炼预算金额、评分标准、技术要求、商务条款等核心维度，结构化呈现。',
    icon: 'M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z',
    bg: 'bg-[#d1fae5]',
    color: 'text-[#059669]',
  },
  {
    title: '多采购包对比',
    desc: '支持同一标书中多个采购包的横向对比分析，快速发现差异与优劣。',
    icon: 'M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5',
    bg: 'bg-[#fef3c7]',
    color: 'text-[#d97706]',
  },
  {
    title: '评审规则解析',
    desc: '自动识别评审方法、权重分配、评分标准，帮助制定最优投标策略。',
    icon: 'M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z',
    bg: 'bg-[#fce4ec]',
    color: 'text-[#e11d48]',
  },
  {
    title: '合规性检查',
    desc: '自动标注实质性要求和禁止项，避免因疏忽导致废标。',
    icon: 'M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z',
    bg: 'bg-[#e0f2fe]',
    color: 'text-[#0284c7]',
  },
  {
    title: '来源精准标注',
    desc: '每条分析结果均标注文件来源和页码，支持原文追溯验证。',
    icon: 'M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244',
    bg: 'bg-[#f3e8ff]',
    color: 'text-[#9333ea]',
  },
];

const steps = [
  {
    num: '01',
    title: '上传标书',
    desc: '将招标文件上传至知识库系统，自动进行文档解析与切片处理',
    bg: 'from-[#7c5cfc] to-[#a78bfa]',
    shadow: 'rgba(124,92,252,0.3)',
  },
  {
    num: '02',
    title: '智能分析',
    desc: '通过自然语言提问，AI 自动检索知识库并生成结构化分析结果',
    bg: 'from-[#f472b6] to-[#fb923c]',
    shadow: 'rgba(244,114,182,0.3)',
  },
  {
    num: '03',
    title: '辅助决策',
    desc: '基于分析结果，快速掌握标书要点，制定最优投标方案',
    bg: 'from-[#2ec4b6] to-[#7c5cfc]',
    shadow: 'rgba(46,196,182,0.3)',
  },
];

const scenarios = [
  {
    title: '政府采购',
    desc: '自动解析政府公开招标文件，提取预算金额、资质要求、评分标准等关键信息，助力企业高效参与政府采购项目。',
    icon: 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0h5m-4 0a2 2 0 01-2-2V6a2 2 0 012-2 2 2v2m0 0h2a2 2 0 012 2v2m0 0h2a2 2 0 012 2v2',
  },
  {
    title: '工程建设',
    desc: '解析技术规范、施工要求、验收标准等专业条款，帮助施工单位快速理解招标要求。',
    icon: 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2M8 10h.01M12 10h.01M16 10h.01M9 16h.01M13 16h.01M16 16h.01',
  },
  {
    title: 'IT 信息化',
    desc: '解析技术指标、服务级别要求、运维条款等内容，辅助企业进行精准报价和方案设计。',
    icon: 'M9.75 17L9 20l-1 1h8l-1-1-.75-.75M12 12.75a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM9.75 9a.75.75 0 100-1.5.75.75 0 000 1.5z',
  },
  {
    title: '医疗器械',
    desc: '解析注册资质要求、技术参数指标、售后服务条款，确保投标响应完整合规。',
    icon: 'M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z',
  },
  {
    title: '教育采购',
    desc: '智能解析教育装备、教学服务类招标文件，提取评分细则和技术要求。',
    icon: 'M12 14l9-5-9-5-9 5 9 5zM12 14l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14z',
  },
  {
    title: '物业服务',
    desc: '解析合同条款、人员配置要求、服务标准与考核细则，辅助物业公司精准投标。',
    icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2-2m-2 2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6',
  },
];

const advantages = [
  {
    title: 'RAG 检索增强生成',
    desc: '采用先进的 RAG 技术，AI 回答基于招标文件原文，避免幻觉，确保分析结果准确可信。',
    icon: 'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z',
    bg: 'bg-[#ede9fe]',
    color: 'text-[#7c5cfc]',
  },
  {
    title: '深度文档理解',
    desc: '支持 PDF、Word 等多种格式，自动识别表格、章节、附件等复杂结构，精准提取关键信息。',
    icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
    bg: 'bg-[#dbeafe]',
    color: 'text-[#2563eb]',
  },
  {
    title: '企业级数据安全',
    desc: '支持私有化部署，数据全程加密存储与传输，满足政企客户对数据安全的严格要求。',
    icon: 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z',
    bg: 'bg-[#d1fae5]',
    color: 'text-[#059669]',
  },
  {
    title: '灵活可扩展',
    desc: '基于 RAGFlow 开源引擎构建，支持自定义知识库、智能体配置，可根据业务需求灵活扩展。',
    icon: 'M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z',
    bg: 'bg-[#fef3c7]',
    color: 'text-[#d97706]',
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
    <div className="bg-white rounded-2xl border border-[rgba(124,92,252,0.06)] overflow-hidden transition-all duration-200 hover:border-[rgba(124,92,252,0.12)]">
      <button
        className="w-full flex items-center justify-between px-7 py-5 text-left cursor-pointer hover:bg-[#faf8f5] transition-colors"
        onClick={() => setOpen(!open)}
      >
        <span className="text-base font-medium text-[#1c1c2e] pr-4 tracking-[-0.01em]">
          {question}
        </span>
        <svg
          className={`w-5 h-5 text-[#7c5cfc] shrink-0 transition-transform duration-300 ${open ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>
      {open && (
        <div className="px-7 pb-5 text-[#5a5a7a] text-sm leading-relaxed border-t border-[rgba(124,92,252,0.06)] pt-4 tracking-[-0.005em]">
          {answer}
        </div>
      )}
    </div>
  );
}

/* ── Styles (injected once) ──────────────────────────────── */

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
      .hero-gradient-cs {
        background: linear-gradient(135deg, #0f0a1a 0%, #1a0f2e 20%, #2d1b4e 40%, #1c1c2e 65%, #0f0a1a 100%);
        position: relative;
      }
      .hero-gradient-cs::before {
        content: '';
        position: absolute;
        inset: 0;
        background:
          radial-gradient(ellipse 45% 60% at 15% 40%, rgba(124, 92, 252, 0.35) 0%, transparent 65%),
          radial-gradient(ellipse 35% 50% at 65% 30%, rgba(244, 114, 182, 0.25) 0%, transparent 60%),
          radial-gradient(ellipse 30% 40% at 85% 70%, rgba(46, 196, 182, 0.2) 0%, transparent 60%),
          radial-gradient(ellipse 25% 30% at 45% 80%, rgba(251, 146, 60, 0.15) 0%, transparent 55%);
        pointer-events: none;
      }
      .cta-gradient-cs {
        background: linear-gradient(135deg, #0f0a1a 0%, #1a0f2e 30%, #2d1b4e 60%, #1a0f2e 100%);
        position: relative;
      }
      .cta-gradient-cs::before {
        content: '';
        position: absolute;
        inset: 0;
        background:
          radial-gradient(ellipse 40% 50% at 30% 50%, rgba(124, 92, 252, 0.3) 0%, transparent 60%),
          radial-gradient(ellipse 30% 40% at 70% 50%, rgba(244, 114, 182, 0.2) 0%, transparent 55%);
        pointer-events: none;
      }
      .creative-card {
        transition: box-shadow 0.3s ease, transform 0.3s ease, border-color 0.3s ease;
      }
      .creative-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 16px 40px -12px rgba(124, 92, 252, 0.12);
        border-color: rgba(124, 92, 252, 0.15);
      }
      .step-card {
        position: relative;
        padding: 32px 24px;
        background: white;
        border-radius: 20px;
        border: 1px solid rgba(124, 92, 252, 0.06);
        transition: box-shadow 0.3s ease, transform 0.3s ease;
      }
      .step-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 20px 48px -12px rgba(124, 92, 252, 0.12);
      }
      .step-card::after {
        content: '';
        position: absolute;
        top: 50%;
        right: -16px;
        width: 12px;
        height: 12px;
        border-top: 2px solid rgba(124, 92, 252, 0.15);
        border-right: 2px solid rgba(124, 92, 252, 0.15);
        transform: translateY(-50%) rotate(45deg);
      }
      .step-card:last-child::after { display: none; }
      @media (max-width: 768px) {
        .step-card::after { display: none; }
      }
      @keyframes float-slow {
        0%, 100% { transform: translateY(0) scale(1); }
        50% { transform: translateY(-20px) scale(1.05); }
      }
      .animate-float-slow { animation: float-slow 8s ease-in-out infinite; }
    `;
    document.head.appendChild(style);
  }, []);
  return null;
}
