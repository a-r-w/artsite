// Progressive enhancement for the piece page's "← All pieces" link.
//
// When the visitor arrived from one of this site's gallery listing pages
// (the main gallery, By Artist, By Location, Untagged), turn the link into a
// true Back: history.back() restores their exact scroll position — and the
// paginated page they were on — instead of dumping them at the top of the
// main gallery. The plain href="/" remains the fallback for direct loads,
// new tabs, and external or unknown referrers (no usable history to go back
// to).
(function () {
  var link = document.querySelector('[data-back-link]');
  if (!link || !document.referrer || history.length <= 1) {
    return;
  }
  var referrer;
  try {
    referrer = new URL(document.referrer);
  } catch (e) {
    return; // malformed referrer: keep the plain link
  }
  var listingPaths = (link.dataset.listingPaths || '').split(' ');
  if (referrer.origin !== location.origin || listingPaths.indexOf(referrer.pathname) === -1) {
    return;
  }
  link.textContent = '← Back';
  link.addEventListener('click', function (e) {
    e.preventDefault();
    history.back();
  });
})();
