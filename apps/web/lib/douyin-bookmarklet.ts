/** Standalone browser code: no bundler helpers, credentials, or network calls. */
const captureSource = String.raw`(() => {
  const host = location.hostname.toLowerCase();
  const douyin = host === 'www.douyin.com' || host === 'douyin.com';
  const tiktok = host === 'www.tiktok.com' || host === 'tiktok.com';
  if (!douyin && !tiktok) {
    alert('Open a Douyin or TikTok channel first.');
    return;
  }
  const profile = douyin
    ? location.pathname.match(/^\/user\/([^/]+)\/?$/)
    : location.pathname.match(/^\/@([\w.-]+)\/?$/);
  if (!profile) { alert('Open the Douyin or TikTok channel first.'); return; }
  const root = douyin
    ? document.getElementById('user_detail_element') || document.querySelector('[data-e2e="user-post-list"]')
    : document.body;
  if (!root) { alert('The profile posts have not loaded. Try again once the post grid appears.'); return; }
  const normalize = value => {
    if (typeof value !== 'string') return null;
    try {
      const url = new URL(value, location.href);
      const post = douyin
        ? url.pathname.match(/^\/video\/(\d{6,})\/?$/)
        : url.pathname.match(/^\/@[\w.-]+\/(?:video|photo)\/(\d{6,})\/?$/i);
      const allowed = douyin
        ? (url.hostname === 'www.douyin.com' || url.hostname === 'douyin.com')
        : (url.hostname === 'www.tiktok.com' || url.hostname === 'tiktok.com');
      return url.protocol === 'https:' && allowed && post
        ? (douyin ? 'https://www.douyin.com/video/' + post[1] : 'https://www.tiktok.com' + url.pathname) : null;
    } catch { return null; }
  };
  // v2 intentionally does not reuse the old whole-page capture, which could
  // include unrelated links from Douyin's footer.
  const key = 'trendrelay:media-channel-links:v3:' + host + ':' + profile[1];
  let previous = [];
  try {
    const saved = JSON.parse(localStorage.getItem(key) || '[]');
    if (Array.isArray(saved)) previous = saved.map(normalize).filter(Boolean);
  } catch {}
  const found = [...root.querySelectorAll('a[href]')]
    .filter(anchor => anchor.getClientRects().length && !anchor.closest('footer,[role="contentinfo"],[hidden],[aria-hidden="true"]'))
    .map(anchor => normalize(anchor.href)).filter(Boolean);
  const all = [...new Set([...previous, ...found])];
  if (!all.length) {
    alert('No loaded profile videos found. Scroll to load posts, then click the bookmark again.');
    return;
  }
  let persisted = true;
  try { localStorage.setItem(key, JSON.stringify(all)); } catch { persisted = false; }
  const text = all.join('\n');
  const count = all.length + ' links (' + (all.length - new Set(previous).size) + ' new)';
  // No ceiling here: the capture's job is to hold everything the page loaded,
  // and TrendRelay splits an oversize paste into download batches itself.
  const batches = all.length > 400 ? ' TrendRelay will import these as ' + Math.ceil(all.length / 400) + ' batches.' : '';
  const notice = batches + (persisted ? '' : ' Browser storage is unavailable; keep this copied list before leaving.');
  const done = () => alert('Copied ' + count + '.' + notice);
  const manualCopy = () => {
    document.getElementById('trendrelay-link-copy')?.remove();
    const panel = document.createElement('div');
    panel.id = 'trendrelay-link-copy';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Copy media links');
    panel.style.cssText = 'position:fixed;inset:12px 12px auto auto;z-index:2147483647;width:min(420px,calc(100vw - 24px));padding:16px;box-sizing:border-box;background:#fff;color:#17232d;border:1px solid #9aa6b2;border-radius:8px;font:14px/1.4 sans-serif;box-shadow:0 8px 32px #0004';
    const label = document.createElement('p');
    label.textContent = 'Copy ' + count + ' below, then paste into TrendRelay.' + notice;
    const area = document.createElement('textarea');
    area.value = text;
    area.readOnly = true;
    area.setAttribute('aria-label', 'Collected video links');
    area.style.cssText = 'width:100%;height:min(220px,45vh);box-sizing:border-box;color:#17232d;background:#fff;font:12px/1.4 monospace';
    const close = document.createElement('button');
    close.textContent = 'Close';
    close.style.cssText = 'min-height:36px;margin-top:8px;padding:6px 14px;color:#17232d;background:#eee;border:1px solid #9aa6b2;border-radius:4px';
    close.onclick = () => panel.remove();
    panel.append(label, area, close);
    document.body.append(panel);
    area.focus();
    area.select();
  };
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, manualCopy);
    else manualCopy();
  } catch { manualCopy(); }
})();`;

// Encode newlines so browser bookmark editors retain the entire program.
export const DOUYIN_BOOKMARKLET = `javascript:${encodeURIComponent(captureSource)}`;
