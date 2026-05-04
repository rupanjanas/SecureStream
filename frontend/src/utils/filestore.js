let _file = null;

export function storeFile(file) {
  _file = file;
}

export function retrieveFile() {
  const f = _file;
  _file = null;
  return f;
}