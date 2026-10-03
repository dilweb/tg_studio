// Сжатие фото на клиенте перед загрузкой.
//
// Параметры согласованы с лимитами vision-моделей (длинная сторона ~2048px):
// LLM-валидация результата ничего не теряет, а файл с телефона 4–8 МБ
// превращается в ~500 КБ — диск и мобильный трафик целы.

export const MAX_SIDE = 2000
export const JPEG_QUALITY = 0.85

/**
 * Ужать изображение до JPEG (длинная сторона ≤ maxSide, качество quality).
 * Если декодировать/сжать не удалось (HEIC вне Safari и т.п.) —
 * возвращаем оригинал: бэкенд сам отвалит не-изображения по mime.
 */
export async function compressImage(file, maxSide = MAX_SIDE, quality = JPEG_QUALITY) {
  let bitmap
  try {
    bitmap = await createImageBitmap(file)
  } catch {
    return file
  }

  try {
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height))
    const width = Math.max(1, Math.round(bitmap.width * scale))
    const height = Math.max(1, Math.round(bitmap.height * scale))

    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height)

    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality))
    if (!blob) return file
    const name = file.name.replace(/\.[^.]*$/, '') + '.jpg'
    return new File([blob], name, { type: 'image/jpeg' })
  } catch {
    return file
  } finally {
    bitmap.close()
  }
}