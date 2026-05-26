/**
 * Design & Creative System — inspired by awesome-design-md creative brands
 *
 * Design philosophy: Warm, playful, creative, human-centered
 * Influences: Figma (vibrant), Clay (organic warmth), Webflow (refined)
 */

export const DESIGN_TOKENS = {
  colors: {
    canvas: '#f8f6f3',
    surface: '#ffffff',
    accentFrom: '#7c5cfc',
    accentTo: '#a78bfa',
    accentLight: '#ede9fe',
    secondaryFrom: '#f472b6',
    secondaryTo: '#fb923c',
    primary: '#1c1c2e',
    text: '#2d2d4a',
    textSecondary: '#5a5a7a',
    textMuted: '#9494b5',
    border: 'rgba(124, 92, 252, 0.08)',
    borderHover: 'rgba(124, 92, 252, 0.2)',
    shadow: 'rgba(124, 92, 252, 0.06)',
    success: '#2ec4b6',
    warning: '#f59e0b',
    error: '#ef4444',
  },
} as const;

export function injectGlobalStyles() {
  const id = 'design-system-global';
  if (document.getElementById(id)) return;

  const style = document.createElement('style');
  style.id = id;
  style.textContent = `
    :root {
      --ds-canvas: #f8f6f3;
      --ds-surface: #ffffff;
      --ds-accent-from: #7c5cfc;
      --ds-accent-to: #a78bfa;
      --ds-accent-light: #ede9fe;
      --ds-secondary-from: #f472b6;
      --ds-secondary-to: #fb923c;
      --ds-primary: #1c1c2e;
      --ds-text: #2d2d4a;
      --ds-text-secondary: #5a5a7a;
      --ds-text-muted: #9494b5;
      --ds-border: rgba(124, 92, 252, 0.08);
      --ds-border-hover: rgba(124, 92, 252, 0.2);
      --ds-shadow: rgba(124, 92, 252, 0.06);
      --ds-success: #2ec4b6;
      --ds-warning: #f59e0b;
      --ds-error: #ef4444;
    }

    /* Scrollbar */
    .ds-scrollbar::-webkit-scrollbar {
      width: 4px;
      height: 4px;
    }
    .ds-scrollbar::-webkit-scrollbar-track {
      background: transparent;
    }
    .ds-scrollbar::-webkit-scrollbar-thumb {
      background: rgba(124, 92, 252, 0.15);
      border-radius: 4px;
    }
    .ds-scrollbar::-webkit-scrollbar-thumb:hover {
      background: rgba(124, 92, 252, 0.3);
    }

    /* Animation keyframes */
    @keyframes ds-fade-in {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes ds-float {
      0%, 100% { transform: translateY(0); }
      50% { transform: translateY(-6px); }
    }
    @keyframes ds-pulse-glow {
      0%, 100% { box-shadow: 0 0 0 0 rgba(124, 92, 252, 0.15); }
      50% { box-shadow: 0 0 0 12px rgba(124, 92, 252, 0); }
    }
    @keyframes ds-shimmer {
      0% { background-position: -200% 0; }
      100% { background-position: 200% 0; }
    }

    /* Reveal animations for landing */
    .ds-reveal {
      opacity: 0;
      transform: translateY(24px);
      transition: opacity 0.7s cubic-bezier(0.22, 1, 0.36, 1),
                  transform 0.7s cubic-bezier(0.22, 1, 0.36, 1);
    }
    .ds-reveal.visible {
      opacity: 1;
      transform: translateY(0);
    }
    .ds-reveal-d1 { transition-delay: 0.08s; }
    .ds-reveal-d2 { transition-delay: 0.16s; }
    .ds-reveal-d3 { transition-delay: 0.24s; }
    .ds-reveal-d4 { transition-delay: 0.32s; }
    .ds-reveal-d5 { transition-delay: 0.40s; }
    .ds-reveal-d6 { transition-delay: 0.48s; }
  `;
  document.head.appendChild(style);
}
