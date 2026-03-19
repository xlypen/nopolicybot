// UI bootstrap for compact conflicts panel
async function fetchConnections(){
  // Call the in-process Python bridge exposed via datahub? Here we mimic with a window.pyBridge if available
  try {
    if (window.datahub && typeof window.datahub.get_connections === 'function'){
      return await window.datahub.get_connections();
    }
  } catch(e){ console.error(e); }
  // Fallback: empty
  return [];
}

function renderConflicts(conflicts){
  const panel = document.getElementById('conflict-cards');
  const countBadge = document.getElementById('conflicts-count');
  panel.innerHTML = '';
  countBadge.textContent = conflicts.length;
  for (const c of conflicts){
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="level">${c.level || 'unknown'}</div>
      <div class="desc">${c.description || ''}</div>
      <div class="meta">${c.count || 0} events</div>
    `;
    panel.appendChild(card);
  }
}

async function initConflictsPanel(){
  const conflicts = await fetchConnections();
  renderConflicts(conflicts);
  const btn = document.getElementById('recalc-conflicts');
  if (btn){
    btn.addEventListener('click', async ()=>{
      const updated = await fetchConnections();
      renderConflicts(updated);
    });
  }
}

// Expose for template inline script call
window.initConflictsPanel = initConflictsPanel;
