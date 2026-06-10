import { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
}

/**
 * Catches rendering errors inside markdown content (e.g. malformed
 * LaTeX, DOMPurify crashes) and shows a fallback instead of crashing
 * the entire page.
 */
export class MarkdownErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    console.warn('Markdown render error (caught by boundary):', error.message);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback || (
          <div className="text-sm text-[#8B8B8B] italic py-2">
            Content failed to render.
          </div>
        )
      );
    }
    return this.props.children;
  }
}
