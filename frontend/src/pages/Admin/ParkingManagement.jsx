import { useState, useEffect, useCallback, useMemo, useRef, Fragment } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  ParkingCircle, Bike, Car, Camera, Plus, RefreshCw, Upload, Save,
  Pencil, Eye, Trash2, X, Loader2, CheckCircle2, Video, Wifi,
  AlertTriangle, CheckCircle, Square, PenTool, LayoutGrid, SlidersHorizontal,
  VideoOff, Search, Maximize2, Minimize2,
} from 'lucide-react'
import notify, { toast } from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import DoubleParkingAlerts from '../../components/DoubleParkingAlerts'
import { BayOccupantDetails } from '../../components/BayOccupant'
import AdminLayout from '../../components/Layout/AdminLayout'
import { zoneApi } from '../../api/parking'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import { useFullscreen } from '../../hooks/useFullscreen'
import './ParkingManagement.css'

const CAT_OPTS = [
  { key: 'motorcycle', label: 'Motorcycle', Icon: Bike },
  { key: 'car',        label: 'Car',        Icon: Car  },
]

const DRAG_MIN = 0.02

let _tid = 0
const tid = () => `_n${++_tid}`

function svgPt(e, el) {
  const r = el.getBoundingClientRect()
  return {
    x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
    y: Math.max(0, Math.min(1, (e.clientY - r.top)  / r.height)),
  }
}

function pointInPolygon(pt, points) {
  let inside = false
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i]
    const [xj, yj] = points[j]
    const intersect = ((yi > pt.y) !== (yj > pt.y)) &&
      (pt.x < (xj - xi) * (pt.y - yi) / (yj - yi) + xi)
    if (intersect) inside = !inside
  }
  return inside
}

function hitTest(pt, s) {
  if (s.points && s.points.length >= 3) return pointInPolygon(pt, s.points)
  return pt.x >= s.x1 && pt.x <= s.x2 && pt.y >= s.y1 && pt.y <= s.y2
}

function autoLabel(list, cat) {
  const pre  = cat === 'motorcycle' ? 'M' : 'C'
  const nums = list.map(s => parseInt(s.space_number.replace(/\D/g, ''), 10)).filter(n => !isNaN(n))
  const n    = nums.length ? Math.max(...nums) + 1 : 1
  return `${pre}${String(n).padStart(2, '0')}`
}

// `embedded` renders the content without its own AdminLayout so the page can
// live as a tab inside Parking Space Management.
export default function ParkingManagement({ embedded = false }) {
  const Wrapper = embedded ? Fragment : AdminLayout
  const [zones,        setZones]        = useState([])
  const [selId,        setSelId]        = useState(null)
  const [mode,         setMode]         = useState('live')
  const [drafts,       setDrafts]       = useState([])
  const [tool,         setTool]         = useState('box') // 'box' | 'pen' (Edit Layout only)
  const [penPoints,    setPenPoints]    = useState([])
  const [penCursor,    setPenCursor]    = useState(null)
  const [selDraft,     setSelDraft]     = useState(null)
  const [draftLabel,   setDraftLabel]   = useState('')
  const [rubberBand,   setRubberBand]   = useState(null)
  const [loading,      setLoading]      = useState(true)
  const [saving,       setSaving]       = useState(false)
  const [spaceOp,      setSpaceOp]      = useState(null)
  const [spaceOpPlate, setSpaceOpPlate] = useState('')
  const [showNew,      setShowNew]      = useState(false)
  const [newZone,      setNewZone]      = useState({ name: '', vehicle_category: 'motorcycle', camera: '' })
  const [addingZone,   setAddingZone]   = useState(false)
  const [confirmModal, setConfirmModal] = useState(null) // { type: 'deleteZone' }
  const [resultModal,  setResultModal]  = useState(null) // { type: 'success'|'error', message }
  // Live-view RTSP cameras panel
  // Open by default: this stopped being an optional drawer when the live feed
  // moved into the main canvas. It now carries the camera's status and controls,
  // including the only route to a zone for an unzoned camera.
  const [showCamPanel, setShowCamPanel] = useState(true)
  // Device Management cameras assignable to a zone, and per-zone detection status
  const [deviceCams,   setDeviceCams]   = useState([])
  const [camRunning,   setCamRunning]   = useState({})
  const [assigning,    setAssigning]    = useState(false)
  const [toggling,     setToggling]     = useState(false)
  const [capturing,    setCapturing]    = useState(false)
  const [methodSaving,   setMethodSaving]   = useState(false)
  const [baselineSaving, setBaselineSaving] = useState(false)

  const { cameras: allCameras, addCamera: addPkCamera, removeCamera: removePkCameraHook,
          registerCanvas: registerPkCanvas, paneCounts: livePaneCounts } = useCameraContext()
  const [pkActiveCamId, setPkActiveCam] = useState(null)
  const [camQuery, setCamQuery] = useState('')
  // The reference image URL is signed and expires, so "it loaded an hour ago"
  // is not a guarantee it loads now. Tracked per zone id: switching zones must
  // not carry one zone's failure over to the next.
  const [imgFailedFor, setImgFailedFor] = useState(null)
  // Both keyed by zone id rather than reset in an effect: a different zone is a
  // different question — its image may be single-lens, or the same lens may not
  // be the right one — and deriving the answer avoids a reset that would run
  // one render after the zone already changed.
  //
  // imgDims: natural size of the reference image, so the editor can tell a
  // stacked dual-lens frame from an ordinary one (the backend's own test).
  // lensSel: which view is being worked on; absent = not chosen yet, a state
  // the editor refuses to draw in.
  const [imgDimsFor, setImgDimsFor] = useState({})
  const [lensSelFor, setLensSelFor] = useState({})
  const parkingCams = allCameras.filter(c => c.assignment === 'parking')
  const pkActiveCam = parkingCams.find(c => c.id === pkActiveCamId) ?? parkingCams[0] ?? null
  const camFs = useFullscreen()

  // Strip only. The canvases above stay mounted for every camera — filtering
  // them would unregister a live feed and force it to reconnect.
  // How many views this camera actually sends, as measured by the render
  // loop from the frame itself — not guessed from the reference image,
  // which may not exist yet when no zone has been created.
  const livePanes = pkActiveCam ? (livePaneCounts[pkActiveCam.id] ?? 1) : 1

  // For the create-zone modal: the camera being picked there is not necessarily
  // the one on screen. Matched by name because CameraContext keys live feeds by
  // its own id, not the Camera row's.
  const newZoneLensCount = (() => {
    const dev = deviceCams.find(c => String(c.id) === String(newZone.camera))
    if (!dev) return 1
    const live = parkingCams.find(c => c.name === dev.name)
    return live ? (livePaneCounts[live.id] ?? 1) : 1
  })()

  const camQ = camQuery.trim().toLowerCase()
  const shownCams = camQ
    ? parkingCams.filter(c => String(c.name ?? '').toLowerCase().includes(camQ))
    : parkingCams

  useEffect(() => {
    if (!pkActiveCamId && parkingCams.length > 0) setPkActiveCam(parkingCams[0].id)
  }) // intentionally no deps — runs after every render until activeCamId is set

  // Open the feed by itself the first time a parking camera turns up, zone or
  // no zone. Guarded by a ref so closing the panel keeps it closed.
  const camPanelAutoOpened = useRef(false)
  useEffect(() => {
    if (!camPanelAutoOpened.current && parkingCams.length > 0) {
      camPanelAutoOpened.current = true
      setShowCamPanel(true)
    }
  }, [parkingCams.length])

  const svgEl       = useRef(null)
  const fileRef     = useRef(null)
  const dragStart   = useRef(null)
  const dragging    = useRef(false)
  const rbRef       = useRef(null)
  const draftsRef   = useRef([])
  const camCanvasRefs = useRef({})

  useEffect(() => { draftsRef.current = drafts }, [drafts])
  useEffect(() => { rbRef.current = rubberBand }, [rubberBand])

  const selZone = zones.find(z => z.id === selId) ?? null
  // Scoped to the zone that actually failed, so selecting another zone shows
  // its own image rather than inheriting the previous one's error.
  const imgFailed = !!selZone && imgFailedFor === selZone.id
  const imgDims  = selZone ? (imgDimsFor[selZone.id] ?? null) : null
  // The zone remembers which view it covers, so the editor opens on it instead
  // of asking again every visit. A switch made on screen still wins for this
  // session — it is how you check the other view without editing the zone.
  const lensView = selZone
    ? (lensSelFor[selZone.id] ?? (selZone.lens_index != null ? selZone.lens_index : null))
    : null

  // Identical rule to lens_layout.lens_count() on the backend and lensCount()
  // in CameraContext: taller than wide, and the halves are widescreen.
  const lensCount = (() => {
    // The live frame is the better witness: it is measured by the render loop
    // from the picture the camera is actually sending, and it exists before any
    // reference image does. Deriving this from the reference image alone meant a
    // zone that had never captured one showed both lenses stacked in Live View —
    // the very thing the lens split exists to prevent.
    if (livePanes > 1) return livePanes
    if (!imgDims) return 1
    const { w, h } = imgDims
    if (!w || !h || h <= w) return 1
    return w / (h / 2) >= 1.6 ? 2 : 1
  })()
  const lensIdx = lensCount > 1 ? (lensView ?? 0) : 0
  // Drawing is blocked until a view is picked, so a bay can never be defined
  // against the seam between two lenses.
  const needsLensChoice = lensCount > 1 && lensView == null

  // Bay geometry is stored normalised against the WHOLE frame — the detector
  // returns full-frame boxes and `bay_occupancy._rect_for` reads them that way.
  // So the lens view is a viewport, not a coordinate system: the SVG viewBox
  // shows one band and pointer input is mapped back out of it, leaving every
  // stored coordinate full-frame. Cropping the saved image instead would have
  // silently moved every bay to the seam.
  const toFullFrame = (pt) => (
    lensCount > 1 ? { x: pt.x, y: (pt.y + lensIdx) / lensCount } : pt
  )

  // ── Which camera is this zone (and this feed) actually about? ────
  //
  // Three different ideas of "camera" meet on this page, and only two of them
  // share an id:
  //
  //   * `deviceCams`  — Device Management rows. `zone.camera` holds one of
  //                     these ids, and so does the assignment dropdown.
  //   * `parkingCams` — live feeds from CameraContext. Their `id` is a
  //                     client-side counter (genId()), NOT the database id.
  //   * `zones`       — each points at a deviceCam id, or at nothing.
  //
  // The RTSP URL is the only field the first two share, so it is the join key.
  // Without this join the sidebar could show camera B's picture while the
  // selected zone was watched by camera A, with nothing on screen saying so —
  // and bays drawn that way line up with a view the detector never sees.
  //
  // Indexed rather than scanned. Every lookup below happens inside a render
  // that repeats on an 8-second occupancy poll, and the naive form — a
  // `deviceCams.find()` per zone tab and per camera thumbnail — is O(zones ×
  // cameras) of that work each time. Two Maps built once per data change make
  // each lookup O(1) and the whole render O(zones + cameras).
  const camIndex = useMemo(() => {
    const byId = new Map(), byUrl = new Map()
    for (const d of deviceCams) {
      byId.set(d.id, d)
      // First wins, matching the .find() this replaced. The API already rejects
      // duplicate stream URLs, so this only decides a case that cannot arise.
      const url = (d.rtsp_url || '').trim()
      if (!byUrl.has(url)) byUrl.set(url, d)
    }
    return { byId, byUrl }
  }, [deviceCams])

  const zonesByCamera = useMemo(() => {
    const m = new Map()
    for (const z of zones) {
      if (z.camera == null) continue
      const list = m.get(z.camera)
      if (list) list.push(z)
      else      m.set(z.camera, [z])
    }
    return m
  }, [zones])

  const deviceCamFor = useCallback(
    (streamCam) => streamCam ? camIndex.byUrl.get((streamCam.url || '').trim()) ?? null : null,
    [camIndex],
  )

  const activeDeviceCam  = deviceCamFor(pkActiveCam)
  const activeCamZones   = activeDeviceCam ? zonesByCamera.get(activeDeviceCam.id) ?? [] : []
  // A registered parking camera nobody has drawn a zone for yet.
  const activeCamUnzoned = !!activeDeviceCam && activeCamZones.length === 0
  const unzonedCams      = useMemo(
    () => deviceCams.filter(d => !(zonesByCamera.get(d.id)?.length)),
    [deviceCams, zonesByCamera],
  )
  // The feed on screen belongs to a different camera than the selected zone.
  const camZoneMismatch  = !!(selZone && activeDeviceCam && selZone.camera !== activeDeviceCam.id)
  const selZoneCamName   = selZone?.camera_name
    ?? (selZone?.camera != null ? camIndex.byId.get(selZone.camera)?.name : null)
    ?? null

  // Everything in the panel is conditional, and with one camera on a zone in
  // Live View every condition is false — which rendered an empty bordered box
  // under the feed. Work out whether it holds anything before drawing it.
  const camPanelHasContent = Boolean(
    activeCamUnzoned ||
    camZoneMismatch ||
    parkingCams.length !== 1 ||     // 0 shows a note, 2+ shows the picker
    !selZone ||                     // the "create a zone" hint
    mode === 'edit'                 // capture + scoring method
  )

  // ── Load zones ──────────────────────────────────────────────────
  const loadZones = useCallback(async () => {
    setLoading(true)
    try {
      const data = await zoneApi.listAll()
      setZones(data)
      setSelId(id => id ?? data[0]?.id ?? null)
      // The camera list is fetched on mount and would otherwise stay there for
      // the life of the page. "Which cameras have no zone" is answered from
      // both lists, so a camera registered after this page opened has to reach
      // it too — otherwise Refresh reports a stale count with a straight face.
      try {
        setDeviceCams(await camerasApi.list({ assignment: 'parking' }))
      } catch { /* keep the cameras already known */ }
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadZones() }, [loadZones])

  // Live-refresh zones/occupancy on parking changes
  useLiveUpdates(loadZones, ['parkingzone', 'parkingspace'])

  // Load parking cameras once on mount — persists across navigation
  useEffect(() => {
    camerasApi.list({ assignment: 'parking' })
      .then(cams => {
        setDeviceCams(cams)
        cams.forEach(c => addPkCamera(c.name, c.rtsp_url, 'parking'))
      })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll detection (auto-camera) status for all zones
  const refreshCamStatus = useCallback(async () => {
    try { setCamRunning(await zoneApi.getCameraStatus()) } catch { /* silent */ }
  }, [])

  useEffect(() => {
    refreshCamStatus()
    const t = setInterval(refreshCamStatus, 8000)
    return () => clearInterval(t)
  }, [refreshCamStatus])

  // Live occupancy polling (always on in live mode)
  const refreshZone = useCallback(async () => {
    if (!selId) return
    try {
      const z = await zoneApi.get(selId)
      setZones(p => p.map(x => x.id === z.id ? z : x))
    } catch { /* silent */ }
  }, [selId])

  useEffect(() => {
    if (mode !== 'live') return
    const t = setInterval(refreshZone, 8000)
    return () => clearInterval(t)
  }, [mode, refreshZone])

  // Copy live spaces into drafts when entering edit mode
  useEffect(() => {
    if (mode === 'edit' && selZone) {
      setDrafts(selZone.spaces.map(s => ({ ...s, _id: s.id })))
      setSelDraft(null)
    }
    setTool('box')
    setPenPoints([])
    setPenCursor(null)
  }, [mode, selId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Escape cancels an in-progress pen shape
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && tool === 'pen' && penPoints.length > 0) setPenPoints([])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tool, penPoints])

  // ── Zone CRUD ───────────────────────────────────────────────────
  // A zone is born attached to a camera, defaulting to the one on screen. It
  // used to be created camera-less every time, which is how a zone ended up
  // being drawn from one feed and watched by another.
  const openNewZone = (cameraId = null) => {
    setNewZone({
      name: '',
      vehicle_category: 'motorcycle',
      camera: String(cameraId ?? activeDeviceCam?.id ?? ''),
    })
    setShowNew(true)
  }

  const handleAddZone = async (e) => {
    e.preventDefault()
    // The form carries noValidate, so the browser's own bubble is gone and
    // its complaints have to be re-raised here.
    if (await notify.validation(fieldProblems(e.currentTarget))) return
    if (!newZone.name.trim()) {
      await notify.error('Give the zone a name.', { title: 'Zone not created' })
      return
    }
    setAddingZone(true)
    try {
      const z = await zoneApi.create({
        ...newZone,
        camera: newZone.camera ? Number(newZone.camera) : null,
        lens_index: Number(newZone.lens_index ?? 0),
      })
      setZones(p => [...p, { ...z, spaces: [] }])
      setSelId(z.id)
      setShowNew(false)
      setNewZone({ name: '', vehicle_category: 'motorcycle', camera: '', lens_index: 0 })
      setMode('edit')
    } catch {
      setShowNew(false)
      setResultModal({ type: 'error', message: 'Failed to create zone. Please try again.' })
    } finally { setAddingZone(false) }
  }

  const handleDeleteZone = () => {
    if (!selZone) return
    setConfirmModal({ type: 'deleteZone' })
  }

  const executeDeleteZone = async () => {
    setConfirmModal(null)
    try {
      await zoneApi.remove(selZone.id)
      const rest = zones.filter(z => z.id !== selZone.id)
      setZones(rest)
      setSelId(rest[0]?.id ?? null)
      setMode('live')
      setResultModal({ type: 'success', message: `Zone "${selZone.name}" has been deleted.` })
    } catch {
      setResultModal({ type: 'error', message: 'Failed to delete zone. Please try again.' })
    }
  }

  // ── Camera assignment (Device Management) ──────────────────────
  const assignCamera = async (cameraId) => {
    if (!selId) return false
    setAssigning(true)
    try {
      const z = await zoneApi.update(selId, { camera: cameraId })
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
      return true
    } catch {
      setResultModal({ type: 'error', message: 'Failed to assign camera. Please try again.' })
      return false
    } finally { setAssigning(false) }
  }

  const handleAssignCamera = (e) =>
    assignCamera(e.target.value ? Number(e.target.value) : null)

  const toggleDetection = async () => {
    if (!selZone) return
    setToggling(true)
    try {
      if (camRunning[selZone.id]) {
        await zoneApi.stopCamera(selZone.id)
      } else {
        await zoneApi.startCamera(selZone.id)
      }
      await refreshCamStatus()
    } catch (err) {
      setResultModal({
        type: 'error',
        message: err?.response?.data?.error || 'Failed to toggle camera detection.',
      })
    } finally { setToggling(false) }
  }

  // ── Capture a still frame from the live feed as the reference image ─────
  //
  // The frame comes from whichever camera the sidebar is showing, but it is
  // saved onto the *selected zone*. When those are two different cameras the
  // result is a zone whose bays were traced over a picture its own camera never
  // produces, so the mismatch has to be settled before the capture, not
  // discovered later by an admin wondering why detection reads nothing.
  const handleCapture = () => {
    if (!selId || !pkActiveCam) return
    if (camZoneMismatch) {
      setConfirmModal({ type: 'captureMismatch' })
      return
    }
    doCapture()
  }

  const doCapture = async () => {
    if (!selId || !pkActiveCam) return
    const canvas = camCanvasRefs.current[pkActiveCam.id]
    if (!canvas || !pkActiveCam.streamConnected) {
      setResultModal({ type: 'error', message: 'Camera is not connected yet — wait for the live feed then try again.' })
      return
    }
    setCapturing(true)
    try {
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.92))
      if (!blob) throw new Error('empty capture')
      const file = new File([blob], `zone-${selId}-capture.jpg`, { type: 'image/jpeg' })
      const z    = await zoneApi.uploadImage(selId, file)
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
      toast.success('Captured frame set as reference image.')
    } catch {
      setResultModal({ type: 'error', message: 'Failed to capture frame. Please try again.' })
    } finally { setCapturing(false) }
  }

  // ── Bay scoring method ──────────────────────────────────────────
  const handleMethodChange = async (method) => {
    if (!selId || method === (selZone?.occupancy_method ?? 'ml')) return
    setMethodSaving(true)
    try {
      const z = await zoneApi.setOccupancyMethod(selId, method)
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
      toast.success(method === 'classic'
        ? (z.has_baseline
            ? 'Zone now scores bays against its baseline.'
            : 'Switched to baseline scoring — capture a baseline to activate it.')
        : 'Zone now scores bays with the vehicle detector.')
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Could not change the detection method.')
    } finally { setMethodSaving(false) }
  }

  // Captured server-side from the running feed, not from the browser canvas:
  // the baseline has to be the exact frame the detector sees, and the canvas is
  // a re-encoded copy that may be a frame or two behind.
  const handleSetBaseline = async () => {
    if (!selId) return
    setBaselineSaving(true)
    try {
      const z = await zoneApi.setBaseline(selId)
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
      toast.success('Empty-lot baseline captured.')
    } catch (err) {
      setResultModal({
        type: 'error',
        message: err?.response?.data?.error || 'Failed to capture the baseline.',
      })
    } finally { setBaselineSaving(false) }
  }

  // ── Image upload ────────────────────────────────────────────────
  const onImageFile = async (e) => {
    const f = e.target.files?.[0]
    if (!f || !selId) return
    try {
      const z = await zoneApi.uploadImage(selId, f)
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
    } catch { toast.error('Image upload failed.') }
    finally { e.target.value = '' }
  }

  // ── SVG drawing (edit mode) ─────────────────────────────────────
  const onMouseDown = (e) => {
    if (mode !== 'edit' || tool !== 'box') return
    if (needsLensChoice) return          // no view picked: a bay would straddle the seam
    e.preventDefault()
    const pt = toFullFrame(svgPt(e, svgEl.current))
    dragStart.current = pt
    dragging.current  = false
    setSelDraft(null)
  }

  const onMouseMove = (e) => {
    if (mode === 'edit' && tool === 'pen') {
      if (penPoints.length > 0) setPenCursor(toFullFrame(svgPt(e, svgEl.current)))
      return
    }
    if (!dragStart.current) return
    const pt = toFullFrame(svgPt(e, svgEl.current))
    const dx = Math.abs(pt.x - dragStart.current.x)
    const dy = Math.abs(pt.y - dragStart.current.y)
    if (dx > DRAG_MIN || dy > DRAG_MIN) dragging.current = true
    if (dragging.current) {
      const rb = { x1: dragStart.current.x, y1: dragStart.current.y, x2: pt.x, y2: pt.y }
      setRubberBand(rb)
      rbRef.current = rb
    }
  }

  const onMouseUp = (e) => {
    if (tool !== 'box') return
    if (!dragStart.current) return
    const pt          = toFullFrame(svgPt(e, svgEl.current))
    const wasDragging = dragging.current
    const rb          = rbRef.current

    dragStart.current = null
    dragging.current  = false
    setRubberBand(null)
    rbRef.current = null

    if (!wasDragging) {
      const hit = [...draftsRef.current].reverse().find(s => hitTest(pt, s))
      if (hit) { setSelDraft(hit._id); setDraftLabel(hit.space_number) }
      else      setSelDraft(null)
      return
    }

    if (!rb) return
    const nx1 = Math.min(rb.x1, rb.x2), nx2 = Math.max(rb.x1, rb.x2)
    const ny1 = Math.min(rb.y1, rb.y2), ny2 = Math.max(rb.y1, rb.y2)
    if (nx2 - nx1 < DRAG_MIN || ny2 - ny1 < DRAG_MIN) return

    const id    = tid()
    const label = autoLabel(draftsRef.current, selZone?.vehicle_category ?? 'motorcycle')
    setDrafts(p => [...p, {
      _id: id, id: null,
      space_number: label,
      vehicle_category: selZone?.vehicle_category,
      x1: nx1, y1: ny1, x2: nx2, y2: ny2,
      points: null,
      lens_index: lensIdx,
      is_occupied: false, occupied_by: '',
    }])
    setSelDraft(id)
    setDraftLabel(label)
  }

  const onMouseLeave = () => {
    setPenCursor(null)
    if (!dragStart.current) return
    dragStart.current = null; dragging.current = false
    setRubberBand(null); rbRef.current = null
  }

  // ── Pen tool (freeform polygon spaces) ───────────────────────────
  const finalizePenShape = (points) => {
    if (points.length < 3) return
    const xs = points.map(p => p.x), ys = points.map(p => p.y)
    const id    = tid()
    const label = autoLabel(draftsRef.current, selZone?.vehicle_category ?? 'motorcycle')
    setDrafts(p => [...p, {
      _id: id, id: null,
      space_number: label,
      vehicle_category: selZone?.vehicle_category,
      x1: Math.min(...xs), y1: Math.min(...ys), x2: Math.max(...xs), y2: Math.max(...ys),
      points: points.map(p => [p.x, p.y]),
      lens_index: lensIdx,
      is_occupied: false, occupied_by: '',
    }])
    setSelDraft(id)
    setDraftLabel(label)
  }

  const onSvgClick = (e) => {
    if (needsLensChoice) return
    if (mode !== 'edit' || tool !== 'pen') return
    const pt = toFullFrame(svgPt(e, svgEl.current))
    if (penPoints.length >= 3 && Math.hypot(pt.x - penPoints[0].x, pt.y - penPoints[0].y) < 0.02) {
      finalizePenShape(penPoints)
      setPenPoints([])
      setPenCursor(null)
      return
    }
    setPenPoints(prev => [...prev, pt])
  }

  const commitLabel = () => {
    if (!selDraft || !draftLabel.trim()) return
    setDrafts(p => p.map(s => s._id === selDraft ? { ...s, space_number: draftLabel.trim() } : s))
  }

  const deleteSelDraft = () => {
    setDrafts(p => p.filter(s => s._id !== selDraft))
    setSelDraft(null)
  }

  // ── Save layout ─────────────────────────────────────────────────
  const saveLayout = async () => {
    if (!selId) return
    setSaving(true)
    try {
      const payload = drafts.map(s => ({
        space_number: s.space_number, x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2,
        points: s.points ?? null,
        lens_index: s.lens_index ?? 0,
      }))
      const saved   = await zoneApi.saveLayout(selId, payload)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: saved } : z))
      setDrafts(saved.map(s => ({ ...s, _id: s.id })))
      setMode('live')
    } catch { setResultModal({ type: 'error', message: 'Failed to save layout. Please try again.' }) }
    finally { setSaving(false) }
  }

  // ── Space click (live mode) ─────────────────────────────────────
  // An occupied bay opens as a question — who is in it — with freeing it as
  // the action underneath. It used to open straight into "Mark as free?" over
  // a bare plate, which asked for a decision without showing what it was about.
  const onSpaceClick = (sp) => {
    if (mode !== 'live') return
    if (sp.is_occupied) setSpaceOp({ type: 'free',   space: sp })
    else                { setSpaceOp({ type: 'occupy', space: sp }); setSpaceOpPlate('') }
  }

  const confirmOccupy = async () => {
    if (!spaceOpPlate.trim()) {
      await notify.error('Enter the plate number parked in this space.', {
        title: 'Space not updated',
      })
      return
    }
    try {
      const u = await zoneApi.markOccupied(spaceOp.space.id, spaceOpPlate)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: z.spaces.map(s => s.id === u.id ? u : s) } : z))
      setSpaceOp(null)
    } catch { toast.error('Failed to mark space as occupied.') }
  }

  const confirmFree = async () => {
    try {
      const u = await zoneApi.markFree(spaceOp.space.id)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: z.spaces.map(s => s.id === u.id ? u : s) } : z))
      setSpaceOp(null)
    } catch { toast.error('Failed to free space.') }
  }

  // ── Derived ─────────────────────────────────────────────────────
  // Slots belong to one view of the camera. Showing another lens's boxes over
  // this picture would draw them against a scene they were never placed in —
  // the geometry is full-frame, so they would land somewhere plausible and
  // wrong. Single-lens cameras have everything at lens 0 and are unaffected.
  const allSpaces   = mode === 'edit' ? drafts : (selZone?.spaces ?? [])
  const spaceList   = lensCount > 1
    ? allSpaces.filter(s => (s.lens_index ?? 0) === lensIdx)
    : allSpaces
  const selDraftSp  = drafts.find(s => s._id === selDraft)
  const liveSpaces  = selZone?.spaces ?? []
  // Bays are what the camera reads on this zone's map. Free/Occupied come from
  // the gate ledger for the whole vehicle category, because that is what
  // capacity actually means — a slot is taken from the entry scan to the exit
  // scan. The bay count falls back in only when the API has not answered yet.
  const baysOccupied = liveSpaces.filter(s => s.is_occupied).length
  const occ         = selZone?.category_occupied  ?? baysOccupied
  const sumFr       = selZone?.category_available ?? Math.max(0, liveSpaces.length - baysOccupied)
  const catLabel    = selZone?.vehicle_category === 'motorcycle' ? 'Motorcycle' : 'Car'

  // ════════════════════════════════════════════════════════════════
  return (
    <Wrapper>
      <div className="pm-page">

        {/* The visible page title and blurb are gone — the sidebar already says
            which page this is, and they cost a whole band of vertical space.
            The heading stays for screen readers, which have no sidebar context.
            Standalone (non-embedded) use keeps the visible header. */}
        {embedded ? (
          <h1 className="pm-sr-only">Parking Space Management</h1>
        ) : (
          <div className="pm-header">
            <div className="pm-header-left">
              <ParkingCircle size={22} className="pm-header-icon" />
              <div>
                <h1 className="pm-title">Parking</h1>
                <p className="pm-subtitle">
                  Draw space boxes in Edit Layout mode. Connect an IP CCTV camera via RTSP to
                  detect vehicles automatically — the backend updates occupancy in real time.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Live double-parking banner. Self-clearing — it disappears when the
            vehicle moves off the line. */}
        <DoubleParkingAlerts zoneId={selId} />

        {/* Occupancy at a glance — these numbers used to be a run of tiny text
            buried in the toolbar between the detection controls. */}
        {selZone && mode === 'live' && (
          <div className="pm-stats-row">
            <div className="pm-stat-card">
              <div className="pm-stat-icon green"><CheckCircle2 size={18} /></div>
              <div>
                <p className="pm-stat-val">{sumFr}</p>
                <p className="pm-stat-lbl">Free</p>
              </div>
            </div>
            <div className="pm-stat-card">
              <div className="pm-stat-icon red"><Car size={18} /></div>
              <div>
                <p className="pm-stat-val">{occ}</p>
                <p className="pm-stat-lbl">Occupied</p>
              </div>
            </div>
            <div className="pm-stat-card">
              <div className="pm-stat-icon blue"><ParkingCircle size={18} /></div>
              <div>
                <p className="pm-stat-val">{selZone?.category_capacity ?? liveSpaces.length}</p>
                <p className="pm-stat-lbl">Capacity</p>
              </div>
            </div>
            <div className="pm-stat-card">
              <div className="pm-stat-icon purple"><LayoutGrid size={18} /></div>
              <div>
                <p className="pm-stat-val">{baysOccupied}/{liveSpaces.length}</p>
                <p className="pm-stat-lbl">Bays Taken</p>
              </div>
            </div>
          </div>
        )}

        {/* Naming the two sources beats letting an admin discover the mismatch
            and assume something is broken. */}
        {selZone && mode === 'live' && (
          <p className="pm-stat-caption">
            Free / Occupied / Capacity count <strong>{catLabel.toLowerCase()}s on campus</strong> from
            gate entry and exit scans. <strong>Bays Taken</strong> is what the camera sees in {selZone.name}.
          </p>
        )}

        {/* Zone bar — labelled tabs on the left, page actions on the right.
            The actions used to sit in their own header row beside the title;
            folding them in here removes a row and fills the bar's dead space. */}
        <div className="pm-zone-bar">
          <span className="pm-zone-bar-label">
            <LayoutGrid size={13} /> Zones
          </span>
          <div className="pm-zone-tabs">
            {/* Each tab names the camera that watches it. Which zone belongs to
                which feed is the whole question this page turns on, and the
                tabs were the one place it could be answered at a glance. */}
            {zones.map(z => {
              const C   = CAT_OPTS.find(c => c.key === z.vehicle_category)?.Icon ?? ParkingCircle
              const cam = z.camera_name
                ?? (z.camera != null ? camIndex.byId.get(z.camera)?.name : null)
                ?? null
              return (
                <button
                  key={z.id}
                  className={`pm-zone-tab${z.id === selId ? ' pm-zone-tab--active' : ''}`}
                  onClick={() => { setSelId(z.id); setMode('live') }}
                  title={cam ? `${z.name} — watched by ${cam}` : `${z.name} — no camera assigned`}
                >
                  <C size={13} /> {z.name}
                  <span className={`pm-zone-tab-cam${cam ? '' : ' pm-zone-tab-cam--none'}`}>
                    {cam ? <Video size={11} /> : <VideoOff size={11} />}
                    {cam ?? 'No camera'}
                  </span>
                </button>
              )
            })}
            {!loading && zones.length === 0 && (
              <span className="pm-zone-empty">No zones yet — create one to start.</span>
            )}
          </div>

          <div className="pm-zone-bar-actions">
            <button className="pm-btn pm-btn--outline" onClick={loadZones} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'pm-spin' : ''} /> Refresh
            </button>
            {/* Available with no zone selected too: the feed is what you draw
                the zone from, so it has to be visible before one exists. */}
            {(mode === 'live' || !selZone) && (
              <button
                className={`pm-btn ${showCamPanel ? 'pm-btn--camera-on' : 'pm-btn--outline'}`}
                onClick={() => setShowCamPanel(p => !p)}
                title={unzonedCams.length > 0
                  ? `${unzonedCams.length} camera(s) with no zone drawn yet: ${unzonedCams.map(c => c.name).join(', ')}`
                  : 'Show the live parking feeds'}
              >
                <Video size={14} /> Cameras {parkingCams.length > 0 && `(${parkingCams.length})`}
                {/* Visible with the panel shut, which is where an unzoned
                    camera would otherwise go unnoticed indefinitely. */}
                {unzonedCams.length > 0 && (
                  <span className="pm-unzoned-badge">{unzonedCams.length} unzoned</span>
                )}
              </button>
            )}
            <button className="pm-btn pm-btn--primary" onClick={() => openNewZone()}>
              <Plus size={14} /> New Zone
            </button>
          </div>
        </div>

        {/* Main content row: parking map + camera sidebar. The sidebar sits
            outside the zone check — the live feed is how an admin decides where
            the spaces go, so it must be watchable before any zone exists. */}
        <div className="pm-content-row">
        {!selZone ? (
          <div className="pm-canvas-area" style={{ flex: 1, minWidth: 0 }}>
            {/* No zone yet, but the camera still belongs on screen: choosing
                which camera to draw a zone for is exactly what you need to see
                to do. This used to be the sidebar's job.

                One tile per lens, never the raw stacked frame. A dual-lens unit
                packs two unrelated scenes into one picture, and showing it whole
                squeezes both into a 16:9 box — neither view is usable, and it
                reads as one broken camera rather than two working ones. */}
            {pkActiveCam ? (
              <div className={`pm-live-panes${livePanes > 1 ? ' pm-live-panes--split' : ''}`}>
                {Array.from({ length: livePanes }, (_, i) => (
                  <div className="pm-live-pane" key={i}>
                    <div className="pm-canvas-wrapper">
                      <canvas
                        className="pm-canvas-live"
                        ref={el => registerPkCanvas(pkActiveCam.id, el, livePanes > 1 ? i : undefined)}
                      />
                    </div>
                    {livePanes > 1 && (
                      <span className="pm-live-pane-tag">Lens {i + 1}</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="pm-canvas-wrapper">
                <div className="pm-canvas-no-img">
                  <ParkingCircle size={26} />
                  <p className="pm-canvas-no-img-title">
                    {loading ? 'Loading…' : 'Select or create a parking zone'}
                  </p>
                  <p className="pm-canvas-no-img-sub">
                    Connect a parking camera to see it here.
                  </p>
                </div>
              </div>
            )}
            <div className="pm-legend">
              <span className="pm-legend-note">
                {loading ? 'Loading…' : 'Pick a zone above, or create one for this camera below.'}
              </span>
            </div>
          </div>
        ) : (
          <div className="pm-canvas-area" style={{ flex: 1, minWidth: 0 }}>

            {/* Toolbar */}
            <div className="pm-toolbar">
              <div className="pm-toolbar-left">
                <div className="pm-mode-toggle">
                  <button
                    className={`pm-mode-btn${mode === 'live' ? ' pm-mode-btn--active' : ''}`}
                    onClick={() => setMode('live')}
                  >
                    <Eye size={13} /> Live View
                  </button>
                  <button
                    className={`pm-mode-btn${mode === 'edit' ? ' pm-mode-btn--active' : ''}`}
                    onClick={() => setMode('edit')}
                  >
                    <Pencil size={13} /> Edit Parking Slots
                  </button>
                </div>


                {mode === 'live' && selZone.camera != null && (
                  <button
                    className="pm-btn pm-btn--outline"
                    onClick={toggleDetection}
                    disabled={toggling}
                  >
                    {toggling
                      ? <Loader2 size={13} className="pm-spin" />
                      : <Video size={13} />}
                    {camRunning[selZone.id] ? 'Stop Detection' : 'Start Detection'}
                  </button>
                )}

                {mode === 'live' && (
                  <span className={`pm-detect-badge ${camRunning[selZone.id] ? 'pm-detect-badge--on' : 'pm-detect-badge--off'}`}>
                    {camRunning[selZone.id] ? 'Auto-detect running' : 'Auto-detect off'}
                  </span>
                )}

                {/* Occupancy counts now live in the stats row above. */}

                {mode === 'edit' && (
                  <div className="pm-mode-toggle">
                    <button
                      className={`pm-mode-btn${tool === 'box' ? ' pm-mode-btn--active' : ''}`}
                      onClick={() => { setTool('box'); setPenPoints([]) }}
                    >
                      <Square size={13} /> Box
                    </button>
                    <button
                      className={`pm-mode-btn${tool === 'pen' ? ' pm-mode-btn--active' : ''}`}
                      onClick={() => setTool('pen')}
                    >
                      <PenTool size={13} /> Pen
                    </button>
                  </div>
                )}

                {/* No box hint here: the footer already says "Click-drag to
                    draw · click a box to rename or delete" for this tool, and
                    saying it twice only widened an already crowded toolbar. The
                    pen hint below stays because it is stateful — it counts the
                    points placed and says how to close the shape, which the
                    footer cannot. */}
                {mode === 'edit' && tool === 'pen' && (
                  <>
                    <span className="pm-edit-hint">
                      {penPoints.length === 0
                        ? 'Click to place points, then click the first (yellow) point to close the shape'
                        : `${penPoints.length} point${penPoints.length === 1 ? '' : 's'} placed — click the yellow point to close, or Esc to cancel`}
                    </span>
                    {penPoints.length >= 3 && (
                      <button
                        className="pm-btn pm-btn--outline"
                        onClick={() => { finalizePenShape(penPoints); setPenPoints([]) }}
                      >
                        <CheckCircle2 size={13} /> Finish Shape
                      </button>
                    )}
                    {penPoints.length > 0 && (
                      <button className="pm-btn pm-btn--outline" onClick={() => setPenPoints([])}>
                        <X size={13} /> Cancel
                      </button>
                    )}
                  </>
                )}
              </div>

              <div className="pm-toolbar-right">
                {mode === 'edit' && (
                  <>
                    <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={onImageFile} />
                    <button className="pm-btn pm-btn--outline" onClick={() => fileRef.current?.click()}>
                      <Upload size={13} /> Upload Image
                    </button>
                    <button className="pm-btn pm-btn--primary" onClick={saveLayout} disabled={saving}>
                      {saving ? <Loader2 size={13} className="pm-spin" /> : <Save size={13} />} Save Layout
                    </button>
                  </>
                )}
                {/* Separated from the working controls: deleting a zone is
                    irreversible and sat one button away from Start Detection. */}
                <span className="pm-toolbar-divider" />
                <button
                  className="pm-btn pm-btn--danger-outline"
                  onClick={handleDeleteZone}
                  title={`Delete the "${selZone.name}" zone and its spaces`}
                >
                  <Trash2 size={13} /> Delete Zone
                </button>
              </div>
            </div>

            {/* Drawing bays is only meaningful against the picture the detector
                will actually read. Both of these say, before a single box is
                drawn, that this layout will not be scored the way it looks. */}
            {mode === 'edit' && selZone.camera == null && (
              <div className="pm-draw-warn">
                <AlertTriangle size={15} />
                <span>
                  <strong>{selZone.name} has no camera assigned.</strong> Bays drawn here are saved,
                  but nothing will detect them until you pick a camera above.
                </span>
              </div>
            )}
            {mode === 'edit' && camZoneMismatch && (
              <div className="pm-draw-warn">
                <AlertTriangle size={15} />
                <span>
                  You are viewing <strong>{activeDeviceCam.name}</strong>, but{' '}
                  <strong>{selZone.name}</strong> is watched by{' '}
                  <strong>{selZoneCamName ?? 'no camera'}</strong>. Draw against this zone's own
                  view, or reassign the camera.
                </span>
                <button
                  className="pm-btn pm-btn--outline"
                  onClick={() => assignCamera(activeDeviceCam.id)}
                  disabled={assigning}
                >
                  {assigning ? <Loader2 size={13} className="pm-spin" /> : <Video size={13} />}
                  Use {activeDeviceCam.name}
                </button>
              </div>
            )}

            {/* Canvas.
                Live View draws the bays over the *live feed*; only Edit Parking
                Slots falls back to the still reference image. Drawing on moving
                video is guesswork, and a "Live View" that was really a photo of
                the car park read as a frozen feed. */}
            <div className="pm-canvas-wrapper" ref={camFs.setRef('parking')}>
              {/* Fullscreen the picture itself. It moved here with the live
                  feed — it was attached to the sidebar view that no longer
                  exists. */}
              {mode === 'live' && pkActiveCam && (
                <button
                  className="pm-cam-fs"
                  onClick={async () => {
                    if (!(await camFs.toggle('parking'))) toast.error('Fullscreen was blocked by the browser.')
                  }}
                  title={camFs.isFullscreen('parking') ? 'Exit fullscreen' : 'Fullscreen'}
                  aria-label={camFs.isFullscreen('parking') ? 'Exit fullscreen' : 'Fullscreen'}
                >
                  {camFs.isFullscreen('parking') ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                </button>
              )}
              {/* The live picture. Registered per lens, so a stacked dual-lens
                  camera shows the one view being worked on rather than both
                  squeezed into a 16:9 box. */}
              {/* Hidden, and deliberately the WHOLE frame.
                  The visible canvas above shows one lens, but a reference image
                  captured from that would be a single view — while every bay
                  coordinate, and the editor's own lens cropping, is expressed
                  against the full stacked frame. Capturing the crop would make
                  the picture and the geometry disagree by exactly one lens.
                  CameraContext keys canvases by pane, so a full-frame one and a
                  per-lens one coexist and both receive frames. */}
              {pkActiveCam && (
                <canvas
                  style={{ display: 'none' }}
                  ref={el => {
                    registerPkCanvas(pkActiveCam.id, el)
                    camCanvasRefs.current[pkActiveCam.id] = el
                  }}
                />
              )}

              {mode === 'live' && (
                pkActiveCam ? (
                  <canvas
                    className="pm-canvas-live"
                    // Always an explicit pane, never undefined. `pane == null` is the
                    // FULL_FRAME key, which the hidden capture canvas above already
                    // holds — passing undefined here would make the two overwrite
                    // each other on a single-lens camera and leave one of them
                    // dark. With one lens the loop draws pane 0 and the whole
                    // frame identically, so this costs nothing.
                    ref={el => registerPkCanvas(pkActiveCam.id, el, lensIdx)}
                  />
                ) : (
                  <div className="pm-canvas-no-img">
                    <VideoOff size={26} />
                    <p className="pm-canvas-no-img-title">No camera connected</p>
                    <p className="pm-canvas-no-img-sub">
                      Assign a camera to {selZone.name} to watch this zone live.
                    </p>
                  </div>
                )
              )}
              {/* Background: reference image OR placeholder.
                  `imgFailed` matters as much as the missing case: the URL is
                  signed and expires, so a page left open overnight comes back
                  to a dead link. Without onError that renders as a broken-image
                  glyph on an empty void — no hint that a refresh fixes it. */}
              {mode === 'edit' && selZone.reference_image_url && !imgFailed ? (
                <img
                  src={selZone.reference_image_url}
                  className="pm-canvas-img"
                  draggable={false}
                  alt=""
                  /* Stretch to lensCount x height and slide the wanted band
                     into the wrapper, which clips. object-fit is `fill`, so the
                     band fills the viewport exactly as the viewBox expects. */
                  style={lensCount > 1 ? {
                    height: `${lensCount * 100}%`,
                    top: `${-lensIdx * 100}%`,
                    bottom: 'auto',
                  } : undefined}
                  onError={() => setImgFailedFor(selZone.id)}
                  onLoad={e => {
                    setImgFailedFor(f => (f === selZone.id ? null : f))
                    setImgDimsFor(m => ({ ...m, [selZone.id]: { w: e.target.naturalWidth, h: e.target.naturalHeight } }))
                  }}
                />
              ) : mode === 'edit' ? (
                <div className="pm-canvas-no-img">
                  {imgFailed ? (
                    <>
                      <AlertTriangle size={26} />
                      <p className="pm-canvas-no-img-title">Reference image could not be loaded</p>
                      <p className="pm-canvas-no-img-sub">
                        The link may have expired. Refresh to get a fresh one, or
                        capture a new image from the live feed.
                      </p>
                      <button className="pm-btn pm-btn--outline" onClick={loadZones}>
                        <RefreshCw size={13} /> Refresh
                      </button>
                    </>
                  ) : (
                    <>
                      <Camera size={26} />
                      <p className="pm-canvas-no-img-title">No reference image yet</p>
                      <p className="pm-canvas-no-img-sub">
                        Draw slots directly, upload a photo, or capture one from the live feed.
                      </p>
                    </>
                  )}
                </div>
              ) : null}

              {/* One view at a time, and the choice comes first.
                  A stacked frame is two unrelated scenes; a bay drawn across
                  the join describes neither, and the geometry is stored against
                  the whole frame so nothing downstream would flag it. */}
              {needsLensChoice && (
                <div className="pm-lens-prompt">
                  <LayoutGrid size={26} />
                  <p className="pm-lens-prompt-title">This camera has {lensCount} views</p>
                  <p className="pm-lens-prompt-sub">
                    {selZone.camera_name || 'This camera'} stacks {lensCount} pictures into one
                    frame. Pick the view this zone covers — you can set up the other one
                    afterwards.
                  </p>
                  <div className="pm-lens-prompt-actions">
                    {Array.from({ length: lensCount }, (_, i) => (
                      <button key={i} className="pm-btn pm-btn--primary" onClick={() => setLensSelFor(m => ({ ...m, [selZone.id]: i }))}>
                        Lens {i + 1}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* SVG overlay */}
              <svg
                ref={svgEl}
                className="pm-canvas-svg"
                /* One band of the full frame. Every bay is stored in
                   full-frame coordinates, so narrowing the viewBox is all it
                   takes to show a single lens — no geometry is rewritten. */
                viewBox={lensCount > 1 ? `0 ${lensIdx / lensCount} 1 ${1 / lensCount}` : '0 0 1 1'}
                preserveAspectRatio="none"
                style={{ cursor: mode === 'edit' ? 'crosshair' : 'default' }}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={onMouseLeave}
                onClick={onSvgClick}
              >
                {/* Parking space boxes */}
                {spaceList.map(s => {
                  const x  = Math.min(s.x1, s.x2), y = Math.min(s.y1, s.y2)
                  const w  = Math.abs(s.x2 - s.x1), h = Math.abs(s.y2 - s.y1)
                  const id = s._id ?? s.id
                  const sel    = id === selDraft
                  const color  = s.is_occupied ? '#D93B3B' : '#1BA968'
                  const fill   = s.is_occupied ? 'rgba(217, 59, 59,0.3)' : 'rgba(27, 169, 104,0.25)'
                  const stroke = sel ? '#F6CE11' : color
                  return (
                    <g
                      key={id}
                      onClick={() => mode === 'live'
                        ? onSpaceClick(s)
                        : (setSelDraft(id), setDraftLabel(s.space_number))
                      }
                      style={{ cursor: 'pointer' }}
                    >
                      {s.points && s.points.length >= 3 ? (
                        <polygon
                          points={s.points.map(p => p.join(',')).join(' ')}
                          fill={fill}
                          stroke={stroke}
                          strokeWidth={sel ? 0.006 : 0.003}
                          strokeDasharray={sel ? '0.015 0.007' : undefined}
                        />
                      ) : (
                        <rect
                          x={x} y={y} width={w} height={h}
                          fill={fill}
                          stroke={stroke}
                          strokeWidth={sel ? 0.006 : 0.003}
                          strokeDasharray={sel ? '0.015 0.007' : undefined}
                          rx={0.004}
                        />
                      )}
                      <text
                        x={x + w/2}
                        y={y + h/2 - (s.is_occupied && s.occupied_by ? 0.013 : 0)}
                        textAnchor="middle" dominantBaseline="middle"
                        fill="#fff" fontSize={0.028} fontWeight="bold"
                        style={{ paintOrder:'stroke', stroke:'rgba(0,0,0,0.55)', strokeWidth:'0.005' }}
                      >
                        {s.space_number}
                      </text>
                      {s.is_occupied && s.occupied_by && (
                        <text
                          x={x + w/2} y={y + h/2 + 0.023}
                          textAnchor="middle" dominantBaseline="middle"
                          fill="#F3C0C0" fontSize={0.02} fontWeight="600"
                          style={{ paintOrder:'stroke', stroke:'rgba(0,0,0,0.5)', strokeWidth:'0.004' }}
                        >
                          {s.occupied_by}
                        </text>
                      )}
                    </g>
                  )
                })}

                {/* Rubber band (edit mode) */}
                {rubberBand && (() => {
                  const { x1, y1, x2, y2 } = rubberBand
                  return (
                    <rect
                      x={Math.min(x1,x2)} y={Math.min(y1,y2)}
                      width={Math.abs(x2-x1)} height={Math.abs(y2-y1)}
                      fill="rgba(3, 57, 108,0.15)"
                      stroke="#03396C"
                      strokeWidth={0.004}
                      strokeDasharray="0.018 0.008"
                      rx={0.004}
                    />
                  )
                })()}

                {/* In-progress pen shape */}
                {mode === 'edit' && tool === 'pen' && penPoints.length > 0 && (
                  <g>
                    <polyline
                      points={
                        penPoints.map(p => `${p.x},${p.y}`).join(' ')
                        + (penCursor ? ` ${penCursor.x},${penCursor.y}` : '')
                      }
                      fill="none"
                      stroke="#03396C"
                      strokeWidth={0.003}
                      strokeDasharray="0.01 0.006"
                    />
                    {penPoints.map((p, i) => (
                      <circle
                        key={i}
                        cx={p.x} cy={p.y}
                        r={i === 0 ? 0.01 : 0.006}
                        fill={i === 0 ? '#F6CE11' : '#03396C'}
                        stroke="#fff"
                        strokeWidth={0.0015}
                      />
                    ))}
                  </g>
                )}
              </svg>

              {/* Space label popover (edit mode) */}
              {selDraftSp && mode === 'edit' && (
                <div
                  className="pm-popover"
                  style={{
                    left: `${(selDraftSp.x1 + selDraftSp.x2) / 2 * 100}%`,
                    top:  `${selDraftSp.y1 * 100}%`,
                  }}
                  onMouseDown={e => e.stopPropagation()}
                >
                  <input
                    className="pm-popover-input"
                    value={draftLabel}
                    onChange={e => setDraftLabel(e.target.value)}
                    onBlur={commitLabel}
                    onKeyDown={e => { if (e.key === 'Enter') commitLabel() }}
                    maxLength={10} autoFocus
                  />
                  <button className="pm-popover-del" onClick={deleteSelDraft} title="Delete space">
                    <Trash2 size={13} />
                  </button>
                </div>
              )}
            </div>

            {/* Camera options, under the picture rather than above it: the feed
                is the subject of this screen, and the controls that pick which
                camera and which of its views you are looking at belong beneath
                it like the controls of a player. */}
            <div className="pm-cam-bar">
              <div className="pm-cam-bar-group">
                <Video size={14} className="pm-cam-bar-icon" />
                <select
                  className="pm-cam-assign-select"
                  value={selZone.camera ?? ''}
                  onChange={handleAssignCamera}
                  disabled={assigning}
                  aria-label="Camera for this zone"
                >
                  <option value="">No camera assigned</option>
                  {deviceCams.map(c => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
                {assigning && <Loader2 size={13} className="pm-spin" />}
                {deviceCams.length === 0 && (
                  <span className="pm-cam-bar-note">None registered — add one in Device Management</span>
                )}
              </div>

              {lensCount > 1 && (
                <div className="pm-lens-picker" role="group" aria-label="Camera view">
                  <span className="pm-lens-label">View</span>
                  {Array.from({ length: lensCount }, (_, i) => (
                    <button
                      key={i}
                      className={`pm-lens-btn${lensView === i ? ' pm-lens-btn--active' : ''}`}
                      onClick={() => setLensSelFor(m => ({ ...m, [selZone.id]: i }))}
                    >
                      Lens {i + 1}
                    </button>
                  ))}
                  <span className="pm-cam-bar-note">
                    {spaceList.length} slot{spaceList.length === 1 ? '' : 's'} on this view
                  </span>
                </div>
              )}
            </div>

            {/* Legend */}
            <div className="pm-legend">
              <span className="pm-legend-item"><span className="pm-legend-dot pm-legend-dot--free" />Free</span>
              <span className="pm-legend-item"><span className="pm-legend-dot pm-legend-dot--occ" />Occupied</span>
              <span className="pm-legend-note">
                {mode === 'live'
                  ? 'Click a space to toggle manually · auto-refreshes every 8 s'
                  : tool === 'pen'
                    ? 'Click to trace a freeform shape · click a space to rename or delete'
                    : 'Click-drag to draw · click a box to rename or delete'}
              </span>
            </div>
          </div>
        )}{/* /pm-canvas-area */}




        </div>{/* /pm-content-row */}

        {/* ── Cameras, beneath the picture ─────────────────────────────────
            The live feed used to run in a right-hand sidebar while the main
            canvas showed a still photo, so the screen had two camera views and
            the bigger one was not the live one. The main canvas is the live
            view now, which leaves this as what it should always have been:
            the controls and status for the camera being watched, under it. */}
        {showCamPanel && camPanelHasContent && (
        <div className="pm-cam-panel">
            {/* ── Is this camera zoned? ──
                Every camera can have its own zone (and more than one), but
                nothing on the page used to say which ones already do. An
                admin looking at an unzoned feed would draw bays into whatever
                zone happened to be selected — a zone fed by a different
                camera — and the layout would silently never match. */}
            {activeCamUnzoned && (
              <div className="pm-cam-notice pm-cam-notice--warn">
                <div className="pm-cam-notice-head">
                  <AlertTriangle size={13} /> Camera not yet zoned
                </div>
                <p className="pm-cam-notice-body">
                  <strong>{activeDeviceCam.name}</strong> has no parking zone drawn for it.
                  {selZone
                    ? <> Anything you draw now is saved to <strong>{selZone.name}</strong>, which
                        is watched by <strong>{selZoneCamName ?? 'no camera'}</strong>.</>
                    : <> Create a zone for it before drawing any bays.</>}
                </p>
                <button
                  className="pm-btn pm-btn--primary pm-cam-notice-btn"
                  onClick={() => openNewZone(activeDeviceCam.id)}
                >
                  <Plus size={13} /> Create Zone for {activeDeviceCam.name}
                </button>
              </div>
            )}

            {/* The camera is zoned, just not with the zone on screen. */}
            {!activeCamUnzoned && camZoneMismatch && (
              <div className="pm-cam-notice pm-cam-notice--info">
                <div className="pm-cam-notice-head">
                  <Video size={13} /> Different camera
                </div>
                <p className="pm-cam-notice-body">
                  This feed is <strong>{activeDeviceCam.name}</strong>; the selected zone{' '}
                  <strong>{selZone.name}</strong> is watched by{' '}
                  <strong>{selZoneCamName ?? 'no camera'}</strong>.
                </p>
                <button
                  className="pm-btn pm-btn--outline pm-cam-notice-btn"
                  onClick={() => { setSelId(activeCamZones[0].id); setMode('live') }}
                >
                  <LayoutGrid size={13} /> Switch to {activeCamZones[0].name}
                </button>
              </div>
            )}

            {/* A captured frame becomes a zone's reference image, so it needs
                a zone to belong to. */}
            {parkingCams.length > 0 && !selZone && (
              <p className="pm-cam-hint">
                Create a zone to use this view as its reference image.
              </p>
            )}

            {/* Setup, not monitoring: capturing a reference image and choosing
                how bays are scored are both things you do while laying a zone
                out. On the Live View they were permanent clutter under a feed
                someone is watching. */}
            {parkingCams.length > 0 && selZone && mode === 'edit' && (
              <button
                className="pm-btn pm-btn--primary"
                style={{ width: '100%', justifyContent: 'center', marginTop: 8 }}
                onClick={handleCapture}
                disabled={capturing || !pkActiveCam?.streamConnected}
                title={pkActiveCam?.streamConnected ? '' : 'Waiting for the live feed to connect…'}
              >
                {capturing ? <Loader2 size={13} className="pm-spin" /> : <Camera size={13} />}
                Use as Reference Image for {selZone.name}
              </button>
            )}

            {/* ── Bay scoring method ──
                The detector is the default. The baseline method needs no
                model at all, but it only works while the camera stays put —
                it judges each bay against a picture of that same bay empty. */}
            {selZone && mode === 'edit' && (
              <div className="pm-method-box">
                <div className="pm-method-head">
                  <SlidersHorizontal size={12} /> Bay Detection
                </div>

                <div className="pm-method-tabs">
                  {[
                    { key: 'ml',      label: 'Detector' },
                    { key: 'classic', label: 'Baseline' },
                  ].map(m => (
                    <button
                      key={m.key}
                      className={`pm-method-tab${(selZone.occupancy_method ?? 'ml') === m.key ? ' pm-method-tab--active' : ''}`}
                      onClick={() => handleMethodChange(m.key)}
                      disabled={methodSaving}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>

                {(selZone.occupancy_method ?? 'ml') === 'classic' && (
                  <>
                    <button
                      className="pm-btn pm-btn--outline"
                      style={{ width: '100%', justifyContent: 'center', marginTop: 8 }}
                      onClick={handleSetBaseline}
                      disabled={baselineSaving || !camRunning[selZone.id]}
                      title={camRunning[selZone.id]
                        ? 'Capture the current frame as the empty-lot reference'
                        : 'Start this zone’s camera first'}
                    >
                      {baselineSaving ? <Loader2 size={13} className="pm-spin" /> : <Camera size={13} />}
                      {selZone.has_baseline ? 'Re-capture Baseline' : 'Set Empty Baseline'}
                    </button>

                    {/* Says which method is really running, not which was
                        picked — without a baseline this zone is still on the
                        detector, and silently doing so would be worse. */}
                    <p className={`pm-method-note${selZone.has_baseline ? '' : ' pm-method-note--warn'}`}>
                      {selZone.has_baseline
                        ? `Baseline captured ${selZone.baseline_captured_at
                            ? new Date(selZone.baseline_captured_at).toLocaleString()
                            : ''}. Re-capture it after moving the camera.`
                        : 'No baseline yet — this zone is still using the detector. Capture one with the lot empty.'}
                    </p>
                  </>
                )}
              </div>
            )}

            {/* Thumbnail strip — only when 2+ cameras */}
            {parkingCams.length > 1 && (
              <div className="pm-cam-search">
                <Search size={13} className="pm-cam-search-icon" />
                <input
                  type="search"
                  placeholder="Search cameras…"
                  value={camQuery}
                  onChange={e => setCamQuery(e.target.value)}
                  aria-label="Search parking cameras"
                />
                {camQ && (
                  <>
                    <span className="pm-cam-search-count">{shownCams.length}/{parkingCams.length}</span>
                    <button type="button" className="pm-cam-search-clear"
                      onClick={() => setCamQuery('')} title="Clear search" aria-label="Clear search">
                      <X size={12} />
                    </button>
                  </>
                )}
              </div>
            )}

            {parkingCams.length > 1 && (
              <div className="pm-cam-thumb-strip">
                {shownCams.length === 0 && (
                  <div className="pm-cam-thumb-none">No cameras match “{camQuery.trim()}”</div>
                )}
                {shownCams.map(cam => {
                  const dev     = deviceCamFor(cam)
                  const unzoned = !!dev && !(zonesByCamera.get(dev.id)?.length)
                  return (
                    <div
                      key={`st-${cam.id}`}
                      className={`pm-cam-strip-thumb ${pkActiveCamId === cam.id ? 'active' : ''}`}
                      onClick={() => setPkActiveCam(cam.id)}
                      title={unzoned ? `${cam.name} — no zone drawn yet` : cam.name}
                    >
                      <span
                        className="pm-cam-strip-dot"
                        style={{ background: cam.streamConnected ? '#1BA968' : cam.wsActive ? '#E0B00C' : '#5C7B92' }}
                      />
                      <Wifi size={14} />
                      <span className="pm-cam-strip-label">{cam.name}</span>
                      {/* Which feeds still need a zone, without clicking through each */}
                      {unzoned && <span className="pm-cam-strip-badge">No zone</span>}
                    </div>
                  )
                })}
              </div>
            )}

            {parkingCams.length === 0 && (
              <p style={{ fontSize: 12, color: '#6B8CA6', margin: '8px 0 0', textAlign: 'center' }}>
                No parking cameras configured — add them in Device Management.
              </p>
            )}
        </div>
        )}

      </div>

      {/* ── Modal: New Zone ── */}
      {showNew && (
        <div className="pm-overlay" onClick={() => setShowNew(false)}>
          <form className="pm-modal" noValidate onSubmit={handleAddZone} onClick={e => e.stopPropagation()}>
            <div className="pm-modal-header">
              <span>Create Parking Zone</span>
              <button type="button" className="pm-modal-close" onClick={() => setShowNew(false)}><X size={16} /></button>
            </div>
            <div className="pm-modal-body">
              <label className="pm-modal-label">Zone Name <span className="pm-req">*</span></label>
              <input
                className="pm-modal-input"
                value={newZone.name}
                onChange={e => setNewZone(p => ({ ...p, name: e.target.value }))}
                placeholder="e.g. Motorcycle Bay A"
                required autoFocus
              />
              <label className="pm-modal-label" style={{ marginTop: 16 }}>Vehicle Category</label>
              <div className="pm-cat-toggle">
                {CAT_OPTS.map(c => (
                  <button
                    key={c.key} type="button"
                    className={`pm-cat-btn${newZone.vehicle_category === c.key ? ' pm-cat-btn--active' : ''}`}
                    onClick={() => setNewZone(p => ({ ...p, vehicle_category: c.key }))}
                  >
                    <c.Icon size={13} /> {c.label}
                  </button>
                ))}
              </div>

              {/* Chosen at creation, not afterwards: the camera decides which
                  picture the bays get drawn on, so picking it later means
                  drawing them twice. Several zones may share one camera — a
                  motorcycle zone and a car zone in the same frame is normal. */}
              <label className="pm-modal-label" style={{ marginTop: 16 }}>Camera</label>
              <select
                className="pm-modal-input"
                value={newZone.camera}
                onChange={e => setNewZone(p => ({ ...p, camera: e.target.value }))}
              >
                <option value="">No camera (assign later)</option>
                {deviceCams.map(c => {
                  const n = zonesByCamera.get(c.id)?.length ?? 0
                  return (
                    <option key={c.id} value={c.id}>
                      {c.name}{n === 0 ? ' — no zone yet' : ` — ${n} zone${n === 1 ? '' : 's'}`}
                    </option>
                  )
                })}
              </select>
              {deviceCams.length === 0 && (
                <p className="pm-modal-note">
                  No parking cameras registered yet — add one in Device Management, then assign it here.
                </p>
              )}

              {/* Which view of the camera. Only asked for a camera that is
                  actually sending more than one — a stacked dual-lens unit
                  watches two different places, and a zone covers one of them.
                  Asked here so the answer is stored with the zone instead of
                  being re-picked in the editor every session. */}
              {newZoneLensCount > 1 && (
                <>
                  <label className="pm-modal-label" style={{ marginTop: 16 }}>
                    Camera View <span className="pm-req">*</span>
                  </label>
                  <div className="pm-lens-picker" style={{ width: 'fit-content' }}>
                    {Array.from({ length: newZoneLensCount }, (_, i) => (
                      <button
                        type="button"
                        key={i}
                        className={`pm-lens-btn${Number(newZone.lens_index ?? 0) === i ? ' pm-lens-btn--active' : ''}`}
                        onClick={() => setNewZone(p => ({ ...p, lens_index: i }))}
                      >
                        Lens {i + 1}
                      </button>
                    ))}
                  </div>
                  <p className="pm-modal-note">
                    This camera sends {newZoneLensCount} views in one frame. The zone covers the
                    one you pick — create a second zone for the other.
                  </p>
                </>
              )}
            </div>
            <div className="pm-modal-footer">
              <button type="button" className="pm-btn pm-btn--outline" onClick={() => setShowNew(false)}>Cancel</button>
              <button type="submit" className="pm-btn pm-btn--primary" disabled={addingZone}>
                {addingZone ? <Loader2 size={13} className="pm-spin" /> : <Plus size={13} />} Create Zone
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── Modal: Occupy ── */}
      {spaceOp?.type === 'occupy' && (
        <div className="pm-overlay" onClick={() => setSpaceOp(null)}>
          <div className="pm-modal" onClick={e => e.stopPropagation()}>
            <div className="pm-modal-header">
              <span>Mark Space {spaceOp.space.space_number} Occupied</span>
              <button className="pm-modal-close" onClick={() => setSpaceOp(null)}><X size={16} /></button>
            </div>
            <div className="pm-modal-body">
              <label className="pm-modal-label">Plate Number <span className="pm-req">*</span></label>
              <input
                className="pm-modal-input"
                value={spaceOpPlate}
                onChange={e => setSpaceOpPlate(e.target.value.toUpperCase())}
                placeholder="e.g. ABC 1234"
                maxLength={20} autoFocus
                onKeyDown={e => e.key === 'Enter' && confirmOccupy()}
              />
            </div>
            <div className="pm-modal-footer">
              <button className="pm-btn pm-btn--outline" onClick={() => setSpaceOp(null)}>Cancel</button>
              <button className="pm-btn pm-btn--primary" onClick={confirmOccupy}>
                <CheckCircle2 size={13} /> Confirm Occupied
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Modal: occupied bay ── */}
      {spaceOp?.type === 'free' && (
        <div className="pm-overlay" onClick={() => setSpaceOp(null)}>
          <div className="pm-modal" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
            <div className="pm-modal-header">
              <span>Space {spaceOp.space.space_number} — Occupied</span>
              <button className="pm-modal-close" onClick={() => setSpaceOp(null)}><X size={16} /></button>
            </div>
            <div className="pm-modal-body">
              <BayOccupantDetails plate={spaceOp.space.occupied_by} />
            </div>
            <div className="pm-modal-footer">
              <button className="pm-btn pm-btn--outline" onClick={() => setSpaceOp(null)}>Cancel</button>
              <button className="pm-btn pm-btn--green" onClick={confirmFree}>
                <CheckCircle2 size={13} /> Mark Free
              </button>
            </div>
          </div>
        </div>
      )}
      {/* ── Confirmation Modal ── */}
      {confirmModal?.type === 'deleteZone' && (
        <div className="pm-overlay" onClick={() => setConfirmModal(null)}>
          <div className="pm-modal pm-modal--centered" onClick={e => e.stopPropagation()}>
            <button className="pm-modal-close" onClick={() => setConfirmModal(null)}><X size={16} /></button>
            <AlertTriangle size={32} className="pm-modal-warn-icon" />
            <h2 className="pm-modal-center-title">Delete Zone?</h2>
            <p className="pm-modal-center-body">
              This will permanently delete <strong>{selZone?.name}</strong> and all its spaces. This cannot be undone.
            </p>
            <div className="pm-modal-center-actions">
              <button className="pm-btn pm-btn--outline" onClick={() => setConfirmModal(null)}>Cancel</button>
              <button className="pm-btn pm-btn--danger" onClick={executeDeleteZone}>Delete Zone</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Confirmation Modal: capturing from another zone's camera ──
          Three ways out on purpose. "Capture anyway" is legitimate when the two
          cameras genuinely overlap; assigning is what the admin usually meant;
          cancel is what they meant when they simply had the wrong tab open. */}
      {confirmModal?.type === 'captureMismatch' && (
        <div className="pm-overlay" onClick={() => setConfirmModal(null)}>
          <div className="pm-modal pm-modal--centered" onClick={e => e.stopPropagation()}>
            <button className="pm-modal-close" onClick={() => setConfirmModal(null)}><X size={16} /></button>
            <AlertTriangle size={32} className="pm-modal-warn-icon" />
            <h2 className="pm-modal-center-title">Different Camera</h2>
            <p className="pm-modal-center-body">
              This frame comes from <strong>{activeDeviceCam?.name}</strong>, but{' '}
              <strong>{selZone?.name}</strong> is watched by{' '}
              <strong>{selZoneCamName ?? 'no camera'}</strong>. Bays drawn on it would not match
              what the detector reads for this zone.
            </p>
            <div className="pm-modal-center-actions">
              <button className="pm-btn pm-btn--outline" onClick={() => setConfirmModal(null)}>
                Cancel
              </button>
              <button
                className="pm-btn pm-btn--outline"
                disabled={assigning}
                onClick={async () => {
                  setConfirmModal(null)
                  if (await assignCamera(activeDeviceCam.id)) doCapture()
                }}
              >
                Assign {activeDeviceCam?.name} &amp; Capture
              </button>
              <button
                className="pm-btn pm-btn--primary"
                onClick={() => { setConfirmModal(null); doCapture() }}
              >
                Capture Anyway
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Result Modal ── */}
      {resultModal && (
        <div className="pm-overlay" onClick={() => setResultModal(null)}>
          <div className="pm-modal pm-modal--centered" onClick={e => e.stopPropagation()}>
            <button className="pm-modal-close" onClick={() => setResultModal(null)}><X size={16} /></button>
            {resultModal.type === 'success'
              ? <CheckCircle size={32} className="pm-modal-success-icon" />
              : <AlertTriangle size={32} className="pm-modal-error-icon" />}
            <h2 className="pm-modal-center-title">{resultModal.type === 'success' ? 'Success' : 'Error'}</h2>
            <p className="pm-modal-center-body">{resultModal.message}</p>
            <div className="pm-modal-center-actions">
              <button className="pm-btn pm-btn--primary" onClick={() => setResultModal(null)}>OK</button>
            </div>
          </div>
        </div>
      )}

    </Wrapper>
  )
}
