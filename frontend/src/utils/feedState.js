// What a live feed from CameraContext is doing right now, in the four words
// every camera screen uses — so the guard's entry screen, the guard's parking
// screen and the Operations Center cannot call the same camera three things.
//
//   live        frames are arriving
//   connecting  the socket is open (or about to be) and the stream is starting
//   offline     the socket closed or the backend reported an error; a retry is
//               scheduled and statusMsg says when
//   none        there is no feed to speak of
//
// A closed socket with no message has simply not opened yet — the first
// render after addCamera — which is still "connecting", not an outage.
export function feedState(cam) {
  if (!cam) return 'none'
  if (cam.streamConnected) return 'live'
  if (cam.wsActive) return 'connecting'
  const msg = (cam.statusMsg || '').trim()
  return msg && !/^connecting/i.test(msg) ? 'offline' : 'connecting'
}

export const FEED_LABEL = {
  live: 'Live',
  connecting: 'Connecting…',
  offline: 'Offline',
  none: 'No camera',
}

// Class for the small status dot (.cm-dot) beside a camera's name.
export const FEED_DOT = {
  live: 'live',
  connecting: 'wait',
  offline: 'off',
  none: 'idle',
}
