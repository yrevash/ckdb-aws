"use client";

import { Component, type ReactNode } from "react";

type Props = {
  /** Human label for the surface being guarded (shown in the fallback). */
  surface: string;
  children: ReactNode;
};

type State = { error: Error | null };

/**
 * Isolates a console surface so one malformed frame or render throw cannot blank
 * the whole app. The fallback is honest (Reality Charter R6): it states that the
 * surface could not be rendered rather than showing stale or fabricated values.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // Surface the failure to the console for debugging; the UI degrades to the
    // honest fallback below rather than crashing the tree.
    console.error(`[postmortem] "${this.props.surface}" failed to render:`, error);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div className="surface-error" role="alert">
          <strong>{this.props.surface} unavailable</strong>
          <p>
            This view could not be rendered from the current data. No values are
            shown rather than stale or placeholder ones.
          </p>
          <button type="button" onClick={this.reset}>
            Retry
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
