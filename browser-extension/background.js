// RAGFlow Web Clipper — Background Service Worker
// Handles API requests and KB list caching.

const CACHE_KEY = 'kb_list_cache';
const CACHE_TIME_KEY = 'kb_list_cache_time';
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

// Listen for messages from popup
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'FETCH_KB_LIST':
      fetchKbList(message.serverUrl, message.apiKey)
        .then(sendResponse)
        .catch(err => sendResponse({ error: err.message }));
      return true; // keep channel open for async response

    case 'CLIP_TO_KB':
      clipToKb(message.serverUrl, message.apiKey, message.kbId, message.payload)
        .then(sendResponse)
        .catch(err => sendResponse({ error: err.message }));
      return true;

    case 'EXTRACT_CONTENT':
      extractContentFromTab(sender.tab?.id)
        .then(sendResponse)
        .catch(err => sendResponse({ error: err.message }));
      return true;

    default:
      return false;
  }
});

async function fetchKbList(serverUrl, apiKey) {
  // Return cached data if still fresh
  const cached = await getCachedKbList();
  if (cached) {
    return { data: cached, cached: true };
  }

  const url = `${serverUrl}/api/v1/kb_list`;
  const resp = await fetch(url, {
    headers: { 'Authorization': apiKey },
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
  }
  const result = await resp.json();
  if (result.code !== 0) {
    throw new Error(result.message || 'Unknown error');
  }

  // Cache the result
  await chrome.storage.local.set({
    [CACHE_KEY]: result.data,
    [CACHE_TIME_KEY]: Date.now(),
  });

  return { data: result.data };
}

async function clipToKb(serverUrl, apiKey, kbId, payload) {
  const url = `${serverUrl}/api/v1/kb/${kbId}/clip`;
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': apiKey,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
  }
  const result = await resp.json();
  if (result.code !== 0) {
    throw new Error(result.message || 'Upload failed');
  }
  return { docId: result.data.doc_id, status: result.data.status };
}

async function extractContentFromTab(tabId) {
  if (!tabId) {
    throw new Error('No active tab');
  }

  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: extractPageContent,
    });
    return results[0]?.result || {};
  } catch (e) {
    throw new Error(`Cannot access this page: ${e.message}`);
  }
}

// Injected into the page to extract content
function extractPageContent() {
  // Remove unwanted elements
  const removeSelectors = [
    'script', 'style', 'noscript', 'iframe', 'nav', 'footer',
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '.sidebar', '.nav', '.footer', '.header', '.menu', '.advertisement',
    '.cookie-banner', '.cookie-consent', '.popup', '.modal',
    '#sidebar', '#footer', '#header', '#menu', '#nav',
  ];

  const doc = document.cloneNode(true);
  removeSelectors.forEach(sel => {
    doc.querySelectorAll(sel).forEach(el => el.remove());
  });

  // Try semantic elements first
  let main = doc.querySelector('article') ||
             doc.querySelector('main') ||
             doc.querySelector('[role="main"]') ||
             doc.querySelector('.post-content') ||
             doc.querySelector('.article-content') ||
             doc.querySelector('.entry-content') ||
             doc.querySelector('#content') ||
             doc.querySelector('.content');

  const html = (main || doc.body).innerHTML;
  const text = (main || doc.body).innerText || '';

  return {
    title: document.title || '',
    url: document.location.href,
    html: html,
    content: text.substring(0, 100000),
  };
}

// Check cached KB list on startup
async function getCachedKbList() {
  const cache = await chrome.storage.local.get([CACHE_KEY, CACHE_TIME_KEY]);
  if (cache[CACHE_KEY] && cache[CACHE_TIME_KEY]) {
    const age = Date.now() - cache[CACHE_TIME_KEY];
    if (age < CACHE_TTL) {
      return cache[CACHE_KEY];
    }
  }
  return null;
}
