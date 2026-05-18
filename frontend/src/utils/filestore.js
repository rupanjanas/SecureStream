const FILE_KEY = "stored_pdf";

export function storeFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        localStorage.setItem(FILE_KEY, JSON.stringify({
          name: file.name,
          type: file.type,
          data: reader.result  // base64 data URL
        }));
        resolve();
      } catch {
        reject(new Error("Storage full"));
      }
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export function retrieveFile() {
  try {
    const stored = localStorage.getItem(FILE_KEY);
    if (!stored) return null;
    const { name, type, data } = JSON.parse(stored);
    const byteString = atob(data.split(",")[1]);
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i++) {
      ia[i] = byteString.charCodeAt(i);
    }
    const blob = new Blob([ab], { type });
    return new File([blob], name, { type });
  } catch {
    return null;
  }
}

export function clearFile() {
  localStorage.removeItem(FILE_KEY);
}