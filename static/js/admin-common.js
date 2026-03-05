/* Common JS utilities for admin panel */

function getInitials(name) {
  var s = (name || '').trim();
  if (!s) return '?';
  var parts = s.split(/\s+/).filter(Boolean);
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return (parts[0].slice(0, 1) + parts[1].slice(0, 1)).toUpperCase();
}

function initAvatarFallbacks(selector) {
  document.querySelectorAll(selector || '.avatar-wrap').forEach(function(wrap) {
    var img = wrap.querySelector('img');
    var fb = wrap.querySelector('.avatar-fallback');
    if (!img || !fb) return;
    img.onerror = function() {
      wrap.classList.add('no-img');
    };
    img.onload = function() {
      wrap.classList.remove('no-img');
    };
    if (img.complete && img.naturalWidth === 0) {
      wrap.classList.add('no-img');
    }
  });
}

function apiFetch(url, opts) {
  opts = opts || {};
  var method = opts.method || 'GET';
  var body = opts.body;
  var fetchOpts = { method: method, headers: {} };
  if (body) {
    fetchOpts.headers['Content-Type'] = 'application/json';
    fetchOpts.body = JSON.stringify(body);
  }
  return fetch(url, fetchOpts)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d.ok && d.error) {
        return Promise.reject(new Error(d.error));
      }
      return d;
    });
}

function setButtonLoading(btn, loading) {
  if (!btn) return;
  btn.disabled = !!loading;
}

function showStatus(el, text, type) {
  if (!el) return;
  el.textContent = text;
  el.style.color = type === 'success' ? 'var(--color-success, #4ade80)' :
                   type === 'error' ? 'var(--color-danger, #e94560)' :
                   'var(--text-secondary, #9bb0cf)';
}

function initSectionTabs(containerSelector) {
  var container = document.querySelector(containerSelector || '.section-tabs');
  if (!container) return;
  container.querySelectorAll('.section-tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
      container.querySelectorAll('.section-tab').forEach(function(t) { t.classList.remove('active'); });
      tab.classList.add('active');
      var target = tab.getAttribute('data-section');
      document.querySelectorAll('.section-panel').forEach(function(p) { p.classList.remove('active'); });
      var panel = document.getElementById(target);
      if (panel) panel.classList.add('active');
    });
  });
}

document.addEventListener('DOMContentLoaded', function() {
  initAvatarFallbacks();
});
