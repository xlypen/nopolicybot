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
    .then(function(r) {
      var ct = (r.headers.get('Content-Type') || '').toLowerCase();
      if (!ct.includes('application/json')) {
        return Promise.reject(new Error(r.status === 401 ? 'Нужна авторизация' : r.status === 404 ? 'Не найдено' : 'Ошибка ' + r.status));
      }
      return r.json();
    })
    .then(function(d) {
      if (d && !d.ok && d.error) {
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

function showModal(title, message, onConfirm, onCancel) {
  var existing = document.getElementById('adminModal');
  if (existing) existing.remove();
  var overlay = document.createElement('div');
  overlay.id = 'adminModal';
  overlay.className = 'modal-overlay active';
  overlay.innerHTML = '<div class="modal-box"><h3>' + (title || 'Подтверждение') + '</h3><p>' + (message || '') + '</p><div class="modal-actions"><button class="btn btn-secondary" id="modalCancel">Отмена</button><button class="btn btn-primary" id="modalConfirm">Подтвердить</button></div></div>';
  document.body.appendChild(overlay);
  document.getElementById('modalCancel').onclick = function() { overlay.remove(); if (onCancel) onCancel(); };
  document.getElementById('modalConfirm').onclick = function() { overlay.remove(); if (onConfirm) onConfirm(); };
  overlay.onclick = function(e) { if (e.target === overlay) { overlay.remove(); if (onCancel) onCancel(); } };
}

function showSkeleton(el, lines) {
  if (!el) return;
  lines = lines || 3;
  var html = '';
  for (var i = 0; i < lines; i++) html += '<div class="skeleton skeleton-line" style="width:' + (60 + Math.random() * 40) + '%"></div>';
  el.innerHTML = html;
}

function showRetry(el, message, retryFn) {
  if (!el) return;
  el.innerHTML = '<span style="color:#ef4444;">' + (message || 'Ошибка загрузки') + '</span> <button class="retry-btn" type="button">Повторить</button>';
  var btn = el.querySelector('.retry-btn');
  if (btn && retryFn) btn.onclick = retryFn;
}
