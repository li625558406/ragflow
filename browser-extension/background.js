// RAGFlow Web Clipper — Background Service Worker
// Handles content extraction only. API calls are made directly from the popup.

// Listen for messages from popup
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'EXTRACT_CONTENT':
      extractContentFromTab(message.tabId || sender.tab?.id)
        .then(sendResponse)
        .catch(err => sendResponse({ error: err.message }));
      return true; // keep channel open for async response

    default:
      return false;
  }
});

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
