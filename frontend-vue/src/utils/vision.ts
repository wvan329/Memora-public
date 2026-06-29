// ========================= 图片工具 =========================
// 共享函数：文件选择器、压缩、上传。useClientAction 和 ToolCard 共用。
import { STORAGE_KEYS } from './storageKeys';

/** 打开文件选择器，返回选中的文件列表（取消返回 null） */
export function openFilePicker(): Promise<File[] | null> {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.multiple = true;
    input.style.display = 'none';
    document.body.appendChild(input);

    let resolved = false;
    function done(result: File[] | null) {
      if (resolved) return;
      resolved = true;
      input.remove();
      resolve(result);
    }

    input.onchange = () => {
      const cfiles = input.files;
      done(cfiles && cfiles.length > 0 ? Array.from(cfiles) : null);
    };

    const onFocus = () => {
      setTimeout(() => { if (!resolved) done(null); }, 500);
    };
    window.addEventListener('focus', onFocus, { once: true });

    input.click();
  });
}

/** 压缩图片为 JPEG Blob */
export function compressImage(file: File, maxSize: number, quality: number): Promise<Blob> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        let w = img.width, h = img.height;
        if (w > maxSize || h > maxSize) {
          if (w > h) { h = Math.round(h * maxSize / w); w = maxSize; }
          else { w = Math.round(w * maxSize / h); h = maxSize; }
        }
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext('2d')!;
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        canvas.toBlob((blob) => resolve(blob!), 'image/jpeg', quality);
      };
      img.src = e.target!.result as string;
    };
    reader.readAsDataURL(file);
  });
}

/** 上传 Blob 数组到云服务器，返回公网 URL 列表（不压缩，由调用方负责压缩） */
export async function uploadBlobs(blobs: Blob[]): Promise<string[]> {
  const password = localStorage.getItem(STORAGE_KEYS.AUTH_PASSWORD) || ''
  const urls: string[] = []
  for (let i = 0; i < blobs.length; i++) {
    const form = new FormData()
    form.append('files', blobs[i], 'img_' + Date.now() + '_' + i + '.jpg')
    const resp = await fetch('https://a.wgk-fun.top/upload', {
      method: 'POST',
      body: form,
      headers: { 'Authorization': 'Bearer ' + password },
    })
    if (resp.ok) {
      const data = await resp.json()
      if (data.urls?.[0]) urls.push(data.urls[0])
    }
  }
  return urls
}

/** 压缩并上传多张图片到云服务器，返回公网 URL 列表 */
export async function uploadImages(files: File[], maxSize: number, quality: number): Promise<string[]> {
  const blobs: Blob[] = []
  for (const file of files) {
    blobs.push(await compressImage(file, maxSize, quality))
  }
  return uploadBlobs(blobs)
}
