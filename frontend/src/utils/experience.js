// Only relative, same-app destinations may survive a login round trip.
export function safeReturnTo(value) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\x00-\x20]/.test(value)) return '/';
  try {
    const url = new URL(value, window.location.origin);
    if (url.origin !== window.location.origin || /^\/login\/?$/.test(url.pathname)) return '/';
    return url.pathname + url.search + url.hash;
  } catch { return '/'; }
}
export function readStoredUser() {
  try { return JSON.parse(localStorage.getItem('user')) || null; }
  catch { return null; }
}
export function errorText(error) {
  const message = error?.message || '请求未完成，请重试';
  return error?.requestId ? `${message}（请求编号：${error.requestId}）` : message;
}
