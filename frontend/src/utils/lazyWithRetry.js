import { lazy } from 'react'

// A route chunk that fails to download used to blank the whole app.
//
// Every page below is a separate hashed file. When a new bundle is deployed,
// only the chunks whose contents changed are renamed — so a browser holding an
// older index.html still boots (the entry chunk resolved) and most pages still
// work, while the one page that was edited asks for a filename that no longer
// exists. React's lazy() rejects, nothing catches it, and React unmounts the
// entire tree: a white screen on that route alone, with no message.
//
// The shell is served no-cache now (see WHITENOISE_ADD_HEADERS_FUNCTION in
// backend/config/settings.py), so a plain reload is enough to pick up the
// current filenames. Reload once, and only once — a sessionStorage flag stops
// a genuinely missing chunk from becoming a reload loop. If the second attempt
// fails too, the error propagates to the boundary, which explains it instead
// of showing nothing.

const RELOAD_FLAG = 'chunk-reload-attempted'

export default function lazyWithRetry(importer) {
  return lazy(async () => {
    try {
      const mod = await importer()
      // Clear on success so a later deploy gets its own retry.
      sessionStorage.removeItem(RELOAD_FLAG)
      return mod
    } catch (err) {
      if (!sessionStorage.getItem(RELOAD_FLAG)) {
        sessionStorage.setItem(RELOAD_FLAG, '1')
        window.location.reload()
        // Never resolves — the reload replaces the page.
        return new Promise(() => {})
      }
      throw err
    }
  })
}
