const $ = (id) => document.getElementById(id);

let lastExportVersion = null;

async function callApi(path, body) {
  const options = body === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

function fmt(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    : `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

function renderTimeline(timeline) {
  const host = $('timeline');
  host.innerHTML = '';
  if (!timeline) return;
  for (const seg of timeline.segments) {
    const bar = document.createElement('div');
    bar.className = `lt-segment lt-segment--${seg.status.toLowerCase()}`;
    bar.style.width = `${Math.max(0.05, (seg.duration / timeline.original_duration) * 100)}%`;
    bar.title = `${seg.status} · ${fmt(seg.start)}–${fmt(seg.end)}`;
    host.appendChild(bar);
  }
}

function render(s) {
  $('fileName').textContent = s.file_name || '';
  $('dropZone').hidden = s.stage !== 'empty';
  $('loaded').hidden = s.stage !== 'loaded' && s.stage !== 'ready';
  $('results').hidden = !s.timeline;
  $('errorPanel').hidden = !s.error;

  if (s.file_name) {
    $('loadedName').textContent = s.file_name;
    $('factDuration').textContent = fmt(s.duration);
    $('factResolution').textContent = s.width ? `${s.width}×${s.height}` : '—';
    $('factAudio').textContent = s.has_audio ? 'Yes' : 'No';
  }

  if (s.timeline) {
    renderTimeline(s.timeline);
    $('savedBadge').textContent = `${s.timeline.time_saved_pct.toFixed(0)}% shorter`;
    $('resultTitle').textContent = `${fmt(s.timeline.trimmed_duration)} instead of ${fmt(s.timeline.original_duration)}`;
    $('resultNote').textContent = s.result_note || '';
  }

  const busy = Boolean(s.job);
  $('progressPanel').hidden = !busy;
  $('analyzeBtn').disabled = busy;
  $('exportBtn').disabled = busy || !s.timeline;
  if (busy) {
    $('progressStage').textContent = s.job === 'analyze' ? 'Analyzing' : 'Exporting final video';
    $('progressPct').textContent = `${Math.round((s.progress || 0) * 100)}%`;
    $('progressFill').style.transform = `scaleX(${Math.max(0, Math.min(1, s.progress || 0))})`;
    $('progressMessage').textContent = s.message || '';
  }

  $('exportPanel').hidden = !s.export_ready;
  if (s.export_ready && s.export_version !== lastExportVersion) {
    lastExportVersion = s.export_version;
    $('exportVideo').src = `/api/video?v=${s.export_version}`;
  }

  const q = s.quality;
  if (s.export_ready && q && q.width) {
    $('qualityNote').textContent =
      `${q.width}×${q.height} · ${Number(q.fps).toFixed(0)} fps · ${q.encoder} · ${q.target}. ` +
      (q.matches_source ? 'Same dimensions as the source.' : 'Resolution differs from the source.');
  }
  $('savedPath').textContent = s.export_ready && s.saved_path ? `Saved to ${s.saved_path}` : '';

  if (s.error) $('errorMessage').textContent = s.error;
}

async function poll() {
  try {
    render(await callApi('/api/status'));
  } catch (err) {
    console.error(err);
  }
  setTimeout(poll, 400);
}

async function analyzeAnother() {
  try {
    const result = await callApi('/api/reset', {});
    if (!result.ok) throw new Error(result.error || 'Could not reset.');
  } catch (err) {
    alert(`Could not return to the home screen: ${err.message}`);
    return;
  }
  const video = $('exportVideo');
  video.pause();
  video.removeAttribute('src');
  video.load();
  lastExportVersion = null;
  try {
    render(await callApi('/api/status'));
  } catch (err) {
    console.error(err);
  }
}

async function startJob(name) {
  try {
    await callApi(`/api/${name}`, {});
  } catch (err) {
    console.error(err);
  }
}

async function exportToLocation() {
  let destination = '';
  try {
    if (window.pywebview && window.pywebview.api) {
      destination = await window.pywebview.api.choose_destination();
    } else {
      destination = prompt('Full path for the saved video');
    }
  } catch (err) {
    alert(`Could not open the save dialog: ${err.message}`);
    return;
  }
  if (!destination) return;
  try {
    await callApi('/api/export', { destination });
  } catch (err) {
    alert(`Could not start the export: ${err.message}`);
  }
}

$('chooseBtn').addEventListener('click', async () => {
  if (window.pywebview && window.pywebview.api) {
    await window.pywebview.api.open_video();
  } else {
    const path = prompt('Full path to a video file');
    if (path) await callApi('/api/load', { path });
  }
});

$('analyzeBtn').addEventListener('click', () => startJob('analyze'));
$('exportBtn').addEventListener('click', exportToLocation);
$('againBtn').addEventListener('click', analyzeAnother);

document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'o') {
    event.preventDefault();
    $('chooseBtn').click();
  }
});
$('retryBtn').addEventListener('click', () => startJob('analyze'));

document.addEventListener('dragover', (event) => {
  event.preventDefault();
  $('dropZone').classList.add('is-over');
});
document.addEventListener('dragleave', () => $('dropZone').classList.remove('is-over'));
document.addEventListener('drop', async (event) => {
  event.preventDefault();
  $('dropZone').classList.remove('is-over');
  const file = event.dataTransfer && event.dataTransfer.files[0];
  if (!file) return;
  if (window.pywebview && window.pywebview.api) {
    await window.pywebview.api.drop_by_name(file.name);
  } else {
    await callApi('/api/load', { path: file.path });
  }
});

poll();
