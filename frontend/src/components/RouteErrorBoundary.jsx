import { Component } from 'react'

// Without a boundary anywhere above the routes, any error thrown while
// rendering a page — including a route chunk that could not be downloaded —
// unmounts the whole React tree and leaves a blank white page. On the campus
// machine that is all a guard sees: no message, no way back, nothing in the
// UI to report. This catches it and says what happened.

export default class RouteErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Keep the detail in the console for whoever is debugging the machine.
    console.error('Page failed to render:', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children

    const isChunkError = /dynamically imported module|Importing a module script failed|Failed to fetch/i
      .test(this.state.error?.message || '')

    return (
      <div style={{
        minHeight: '60vh', display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 14,
        padding: 24, textAlign: 'center',
        fontFamily: 'system-ui, sans-serif', color: '#0B2340',
      }}>
        <h2 style={{ fontSize: 18, fontWeight: 800, margin: 0 }}>
          {isChunkError ? 'This page could not be loaded' : 'Something went wrong on this page'}
        </h2>
        <p style={{ fontSize: 13.5, color: '#4A6B85', margin: 0, maxWidth: 460, lineHeight: 1.55 }}>
          {isChunkError
            ? 'The app was updated while this tab was open, so part of it is missing. Reloading will fetch the current version.'
            : 'The rest of the system is unaffected. Reload to try again, and tell the administrator if it keeps happening.'}
        </p>
        <button
          onClick={() => { sessionStorage.removeItem('chunk-reload-attempted'); window.location.reload() }}
          style={{
            padding: '9px 20px', borderRadius: 8, border: 'none', cursor: 'pointer',
            background: '#03396C', color: '#fff', fontSize: 13, fontWeight: 700,
          }}
        >
          Reload page
        </button>
      </div>
    )
  }
}
