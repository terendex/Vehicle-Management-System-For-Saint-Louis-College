import { useState, useEffect, useRef } from 'react'
import { X, Folder, HardDrive, Usb, ArrowUp, Loader2, FolderPlus, ChevronRight } from 'lucide-react'
import notify from '../../components/Feedback/notify'
import { usersApi } from '../../api/users'

/* A folder picker for the "Save to folder" setting. The folder lives on the
   SERVER, which the browser's own picker cannot show (and it would never hand
   back a full path anyway), so this asks the server one level at a time.
   At the server PC itself Browse opens the Windows folder window instead
   (accounts/folder_dialog.py); this list is for every other computer.
   Picking only fills the field: the Save that follows still runs the server's
   write test, so nothing here can leave a bad folder configured. */

// Windows paths on the campus PC, POSIX on the cloud server — join with
// whichever the path in hand already uses.
function joinPath(base, name) {
  const sep = /^[A-Za-z]:/.test(base) || base.includes('\\') ? '\\' : '/'
  return base.endsWith(sep) ? base + name : base + sep + name
}

// Characters Windows refuses in a folder name; the server would refuse them on
// Save anyway, but saying so here is kinder than an error after the fact.
const BAD_NAME = /[\\/:*?"<>|]/

// One level of folders. With `fallBack`, a saved folder that has gone (a USB
// stick pulled out) opens the drive list instead of an error.
async function fetchListing(path, { fallBack = false } = {}) {
  try {
    return await usersApi.browseServerFolders(path)
  } catch (err) {
    if (!fallBack || !path || !err.response) throw err
    return usersApi.browseServerFolders('')
  }
}

function openError(err) {
  notify.error(err.response?.data?.error
    || (!err.response ? 'Could not reach the server. Check the connection and try again.'
      : 'That folder could not be opened.'), { title: 'Folder not opened' })
}

function DriveIcon({ kind }) {
  if (kind && kind.startsWith('USB')) return <Usb size={16} />
  return <HardDrive size={16} />
}

export default function ServerFolderPicker({ initialPath, onPick, onClose }) {
  const [listing, setListing] = useState(null)   // { path, parent, folders, truncated }
  const [loading, setLoading] = useState(true)
  const [newName, setNewName] = useState('')
  const latest = useRef(0)                       // drops answers to clicks that were superseded

  // Shows a fetched folder, unless a later click has already moved on.
  const show = (ticket, promise) => promise
    .then((data) => { if (ticket === latest.current) { setListing(data); setNewName('') } })
    .catch((err) => { if (ticket === latest.current) openError(err) })
    .finally(() => { if (ticket === latest.current) setLoading(false) })

  useEffect(() => {
    // `loading` already starts true; state is only set once the answer is in.
    const ticket = ++latest.current
    fetchListing((initialPath || '').trim(), { fallBack: true })
      .then((data) => { if (ticket === latest.current) setListing(data) })
      .catch((err) => { if (ticket === latest.current) openError(err) })
      .finally(() => { if (ticket === latest.current) setLoading(false) })
  }, [initialPath])

  const open = (path) => {
    setLoading(true)
    show(++latest.current, fetchListing(path))
  }

  const atDrives = listing && !listing.path

  const addNewFolder = async () => {
    const name = newName.trim()
    if (!name) return
    if (BAD_NAME.test(name) || name === '.' || name === '..') {
      await notify.error('A folder name cannot contain \\ / : * ? " < > |', { title: 'Folder name not allowed' })
      return
    }
    // Not created here: the field takes the path, and Save creates the folder
    // and test-writes to it in one step.
    onPick(joinPath(listing.path, name))
  }

  return (
    <div className="ss-overlay" onClick={onClose}>
      <div className="ss-modal ss-folder-modal" onClick={e => e.stopPropagation()}
           role="dialog" aria-modal="true" aria-labelledby="ss-folder-title">
        <button className="ss-modal-close" onClick={onClose} aria-label="Close"><X size={16} /></button>
        <h2 className="ss-modal-title" id="ss-folder-title">Choose a folder on the server</h2>

        <div className="ss-folder-bar">
          <button
            type="button"
            className="ss-folder-up"
            onClick={() => open(listing?.parent || '')}
            disabled={loading || !listing || atDrives}
            title="Up one folder"
            aria-label="Up one folder"
          >
            <ArrowUp size={15} />
          </button>
          <div className="ss-folder-path" title={listing?.path || ''}>
            {atDrives || !listing ? 'This server’s drives' : listing.path}
          </div>
        </div>

        <div className="ss-folder-list" aria-busy={loading}>
          {loading && !listing ? (
            <div className="ss-folder-empty"><Loader2 size={18} className="ss-spinner" /> Loading…</div>
          ) : !listing ? (
            // Even the drive list failed (the error modal has said why).
            <div className="ss-folder-empty">
              Could not load the server&rsquo;s folders.
              <button type="button" className="ss-modal-btn ss-modal-btn-ghost" onClick={() => open('')}>
                Try again
              </button>
            </div>
          ) : listing.folders.length === 0 ? (
            <div className="ss-folder-empty">No folders here. You can make one below, or select this folder.</div>
          ) : listing && listing.folders.map(f => (
            <button
              type="button"
              key={f.path}
              className="ss-folder-item"
              onClick={() => open(f.path)}
              disabled={loading}
            >
              {atDrives ? <DriveIcon kind={f.kind} /> : <Folder size={16} />}
              <span className="ss-folder-name">{f.name}</span>
              {atDrives && f.kind && <span className="ss-folder-kind">{f.kind}</span>}
              <ChevronRight size={15} className="ss-folder-chevron" />
            </button>
          ))}
          {listing?.truncated && (
            <div className="ss-folder-empty">Only the first 1,000 folders are shown.</div>
          )}
        </div>

        {listing && !atDrives && (
          <div className="ss-folder-new">
            <FolderPlus size={15} />
            <input
              type="text"
              className="ss-input ss-input--text ss-folder-new-input"
              placeholder="New folder name"
              aria-label="New folder name"
              maxLength={120}
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addNewFolder() } }}
            />
            <button type="button" className="ss-modal-btn ss-modal-btn-ghost"
                    onClick={addNewFolder} disabled={!newName.trim()}>
              Use new folder
            </button>
          </div>
        )}

        <div className="ss-modal-actions ss-folder-actions">
          <button type="button" className="ss-modal-btn ss-modal-btn-ghost" onClick={() => onPick('')}>
            Use app&rsquo;s own folder
          </button>
          <button type="button" className="ss-modal-btn ss-modal-btn-ghost" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="ss-modal-btn ss-modal-btn-primary"
            onClick={() => onPick(listing.path)}
            disabled={loading || !listing || atDrives}
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  )
}
