import React from 'react';

interface Props {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  label?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[ErrorBoundary${this.props.label ? ' ' + this.props.label : ''}] Caught error:`, error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex flex-col gap-3 p-6 m-4 rounded-lg bg-red-950 border border-red-700 text-red-200">
          <div className="flex items-center gap-2 font-semibold text-red-300">
            <span>⚠</span>
            <span>Rendering error{this.props.label ? ` in ${this.props.label}` : ''}</span>
          </div>
          <pre className="text-xs whitespace-pre-wrap break-all text-red-400 bg-red-900/40 p-3 rounded">
            {this.state.error?.message}
            {'\n\n'}
            {this.state.error?.stack}
          </pre>
          <button
            className="self-start text-xs px-3 py-1.5 rounded bg-red-800 hover:bg-red-700 text-white transition-colors"
            onClick={() => this.setState({ hasError: false, error: null })}
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
