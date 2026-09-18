/* Shrink a photographed document before it is uploaded.

   Every file the registration flow collects — the driver's license, an
   assessment form, an Official Receipt — is a phone camera photo, which means
   3-8MB of 12-megapixel JPEG or HEIC. Five of those (a fetcher with three
   students) is 25MB going up a mobile connection, and that upload *is* the wait
   the applicant experiences when they press Submit. Re-encoded at a size CDSO
   can still read a licence number off, the same photo is 200-400KB.

   Three rules keep this from ever making things worse:

     - It fails open. HEIC does not decode in a canvas outside Safari, and a
       corrupt or enormous image can fail to decode anywhere. Every failure path
       returns the original file, so the upload still happens — just slowly, the
       way it always did.
     - It never grows a file. If the re-encode comes back larger (already-
       optimised images, flat scans that JPEG handles worse than PNG), the
       original is kept.
     - It leaves non-images alone. Assessment forms and receipts are often the
       PDF straight from a portal, and those must arrive byte-for-byte. */

/* 1600px on the long edge. A licence number, an OR number and the text on an
   assessment form are all comfortably legible at that size — the reviewer is
   reading a few large fields, not scrutinising fine print. */
const MAX_DIMENSION = 1600

/* Quality 0.82 is where JPEG stops visibly degrading text at this scale. */
const QUALITY = 0.82

/* Below this a re-encode is not worth the risk of a wash: the transfer is
   already fast, and a second JPEG pass only loses detail. */
const SKIP_BELOW_BYTES = 400 * 1024

/** Decode to a bitmap, preferring createImageBitmap and falling back to an
 *  <img>. Safari only grew createImageBitmap(Blob) support recently, and it is
 *  the one browser that *can* decode HEIC — so the fallback is what lets an
 *  iPhone's default photo format get compressed at all. */
async function decode(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file)
    } catch {
      // Fall through — an unsupported format here is not fatal.
    }
  }

  const url = URL.createObjectURL(file)
  try {
    return await new Promise((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('decode failed'))
      img.src = url
    })
  } finally {
    URL.revokeObjectURL(url)
  }
}

/** The original name with a .jpg extension, since the bytes are now JPEG.
 *  The backend validates on extension, so `licence.heic` holding JPEG data
 *  would be a file that lies about itself. */
function asJpegName(name) {
  return name.replace(/\.[^.]+$/, '') + '.jpg'
}

/**
 * Re-encode an image file smaller. Returns the original file unchanged for
 * PDFs, for already-small images, and for anything that fails to decode or
 * re-encode — callers can treat the result as "the file to upload" and never
 * need to handle a failure.
 *
 * @param {File} file
 * @returns {Promise<File>}
 */
export async function compressImage(file) {
  if (!file || !file.type?.startsWith('image/')) return file
  if (file.size <= SKIP_BELOW_BYTES) return file

  try {
    const bitmap = await decode(file)
    const width = bitmap.width || bitmap.naturalWidth
    const height = bitmap.height || bitmap.naturalHeight
    if (!width || !height) return file

    const scale = Math.min(1, MAX_DIMENSION / Math.max(width, height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(width * scale)
    canvas.height = Math.round(height * scale)

    const ctx = canvas.getContext('2d')
    if (!ctx) return file
    // White ground: a transparent PNG would otherwise flatten to black, which
    // is exactly the case where the document becomes unreadable.
    ctx.fillStyle = '#FFFFFF'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    bitmap.close?.()

    const blob = await new Promise(resolve =>
      canvas.toBlob(resolve, 'image/jpeg', QUALITY))
    if (!blob || blob.size >= file.size) return file

    return new File([blob], asJpegName(file.name), {
      type: 'image/jpeg',
      lastModified: file.lastModified,
    })
  } catch {
    return file
  }
}
