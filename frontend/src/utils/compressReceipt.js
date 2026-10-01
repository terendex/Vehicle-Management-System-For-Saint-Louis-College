/* Shrinks a receipt photo before it is uploaded.

   A phone camera produces 5-8 MB, which on slow mobile data is minutes of
   uploading against a 3-day deadline, and a connection that drops halfway
   loses the lot. Scaled so the long side is at most MAX_SIDE and re-encoded as
   JPEG, a receipt is a few hundred KB and the printed OR number stays sharp.

   Never in the way: a PDF, a HEIC the browser cannot decode, an image already
   small, or any failure at all returns the original file untouched, and the
   server's own size limit still applies to it. */

const MAX_SIDE = 2000          // px on the long side; the OR number stays readable well below this
const QUALITY = 0.82
const SKIP_BELOW = 700 * 1024  // already small enough to send as it is

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => { URL.revokeObjectURL(url); resolve(img) }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('undecodable image')) }
    img.src = url   // browsers apply the photo's EXIF rotation when drawing an <img>
  })
}

export default async function compressReceipt(file) {
  if (!file || file.size <= SKIP_BELOW || !/^image\/(jpeg|png|webp)$/i.test(file.type)) return file
  try {
    const img = await loadImage(file)
    const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
    const ctx = canvas.getContext('2d')
    if (!ctx) return file
    ctx.fillStyle = '#fff'     // a transparent PNG would otherwise turn black as JPEG
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', QUALITY))
    if (!blob || blob.size >= file.size) return file
    const name = file.name.replace(/\.[^.]+$/, '') + '.jpg'
    return new File([blob], name, { type: 'image/jpeg', lastModified: Date.now() })
  } catch {
    return file
  }
}
