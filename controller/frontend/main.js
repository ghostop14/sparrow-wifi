const elements = {};

const API_BASE = '/api';
const state = {
    agentCache: new Map(),
    agentInterfaces: new Map(),
    agentMonitorMap: new Map(),
    agentMarkers: new Map(),
    falconData: new Map(),
    selectedAgentId: null,
    map: null,
    networkIndex: new Map(),
    networkList: [],
    networkMarkers: new Map(),
    bluetoothIndex: new Map(),
    bluetoothList: [],
    bluetoothMarkers: new Map(),
    continuousScans: [],
    showWifiLayers: true,
    showBluetoothLayers: true,
    showAgentLayers: true,
    networkPage: 1,
    bluetoothPage: 1,
    pageSize: 25,
    ws: null,
    falconNetworkPage: 1,
    falconClientPage: 1,
    falconPageSize: 15,
    falconNetworkTotalPages: 1,
    falconClientTotalPages: 1,
    falconPollHandle: null,
    falconPollingAgent: null,
    falconScanning: new Set(),
    falconScanInterfaces: new Map(),
    networkObservations: new Map(),
    bluetoothObservations: new Map(),
    spectrumPollHandle: null,
    spectrumChart: null,
    spectrumAgentId: null,
    spectrumBand: null,
    spectrumSnapshotting: false,
    monitorOverrides: new Map(),
};

const SPECTRUM_SNAPSHOT_DELAY_MS = 1200;
const LOCATION_WINDOW_MS = 24 * 60 * 60 * 1000; // keep location history for 24h

function bootstrap() {
    elements.agentList = document.getElementById('agent-list');
    elements.sidebar = document.getElementById('control-panel');
    elements.sidebarToggle = document.getElementById('sidebar-toggle');
    elements.tabsOverlay = document.getElementById('tabs-overlay');
    elements.tabsCollapse = document.getElementById('tabs-collapse');
    elements.scanForm = document.getElementById('scan-form');
    elements.scanAgentSelect = document.getElementById('scan-agent');
    elements.scanType = document.getElementById('scan-type');
    elements.scanContinuous = document.getElementById('scan-continuous');
    elements.scanInterval = document.getElementById('scan-interval');
    elements.continuousList = document.getElementById('continuous-list');
    elements.networkTableBody = document.querySelector('#network-table tbody');
    elements.wifiPrev = document.getElementById('wifi-prev');
    elements.wifiNext = document.getElementById('wifi-next');
    elements.wifiPage = document.getElementById('wifi-page');
    elements.wifiCount = document.getElementById('wifi-count');
    elements.bluetoothTableBody = document.querySelector('#bluetooth-table tbody');
    elements.bluetoothPrev = document.getElementById('bluetooth-prev');
    elements.bluetoothNext = document.getElementById('bluetooth-next');
    elements.bluetoothPage = document.getElementById('bluetooth-page');
    elements.bluetoothCount = document.getElementById('bluetooth-count');
    elements.toggleWifi = document.getElementById('toggle-wifi');
    elements.toggleBluetooth = document.getElementById('toggle-bluetooth');
    elements.toggleAgents = document.getElementById('toggle-agents');
    elements.scansTableBody = document.querySelector('#scans-table tbody');
    elements.falconStatusLog = document.getElementById('falcon-status-log');
    elements.falconAgentSelect = document.getElementById('falcon-agent');
    elements.falconMonitorForm = document.getElementById('falcon-monitor-form');
    elements.falconMonitorStop = document.getElementById('falcon-monitor-stop');
    elements.falconScanForm = document.getElementById('falcon-scan-form');
    elements.falconScanStop = document.getElementById('falcon-scan-stop');
    elements.falconScanStatus = document.getElementById('falcon-scan-status');
    elements.tabButtons = document.querySelectorAll('.tab-button');
    elements.tabPanels = document.querySelectorAll('.tab-panel');
    elements.sidebarTabButtons = document.querySelectorAll('.sidebar-tab-button');
    elements.sidebarTabPanels = document.querySelectorAll('.sidebar-tab-panel');
    elements.detailDrawer = document.getElementById('detail-drawer');
    elements.detailContent = document.getElementById('detail-content');
    elements.detailClose = document.getElementById('detail-close');
    elements.agentModal = document.getElementById('agent-modal');
    elements.agentForm = document.getElementById('agent-form');
    elements.openAgentModal = document.getElementById('open-agent-modal');
    elements.closeAgentModal = document.getElementById('close-agent-modal');
    elements.openSpectrumModal = document.getElementById('open-spectrum-modal');
    elements.spectrumModal = document.getElementById('spectrum-modal');
    elements.closeSpectrumModal = document.getElementById('close-spectrum-modal');
    elements.spectrumAgentSelect = document.getElementById('spectrum-agent');
    elements.spectrumStatus = document.getElementById('spectrum-status');
    elements.spectrumStart24 = document.getElementById('spectrum-start-24');
    elements.spectrumStart5 = document.getElementById('spectrum-start-5');
    elements.spectrumStop = document.getElementById('spectrum-stop');
    elements.spectrumSnapshot24 = document.getElementById('spectrum-snapshot-24');
    elements.spectrumSnapshot5 = document.getElementById('spectrum-snapshot-5');
    elements.spectrumChartCanvas = document.getElementById('spectrum-chart');
    initMap();
    initTabs();
    initModal();
    bindEvents();
    handleScanTypeChange();
    loadAgents();
    loadScans();
    setInterval(loadAgents, 30000);
    setInterval(loadScans, 10000);
    loadContinuousScans();
    setInterval(loadContinuousScans, 15000);
    initWebSocket();
    updateFalconIndicator();
}

function initMap() {
    state.map = L.map('map').setView([20, 0], 2);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19,
    }).addTo(state.map);
}

function initTabs() {
    elements.tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            elements.tabButtons.forEach(btn => btn.classList.remove('active'));
            elements.tabPanels.forEach(panel => panel.classList.remove('active'));
            button.classList.add('active');
            document.getElementById(button.dataset.tab).classList.add('active');
        });
    });

    elements.sidebarTabButtons.forEach(button => {
        button.addEventListener('click', () => {
            elements.sidebarTabButtons.forEach(btn => btn.classList.remove('active'));
            elements.sidebarTabPanels.forEach(panel => panel.classList.remove('active'));
            button.classList.add('active');
            document.getElementById(button.dataset.sidebarTab).classList.add('active');
        });
    });
}

function initModal() {
    elements.openAgentModal?.addEventListener('click', () => {
        elements.agentModal?.classList.remove('hidden');
    });
    elements.closeAgentModal?.addEventListener('click', () => {
        elements.agentModal?.classList.add('hidden');
    });
}

function bindEvents() {
    elements.scanForm?.addEventListener('submit', onQuickScanSubmit);
    elements.agentForm?.addEventListener('submit', onAgentFormSubmit);
    elements.sidebarToggle?.addEventListener('click', toggleSidebar);
    elements.tabsCollapse?.addEventListener('click', toggleTabsOverlay);
    elements.scanAgentSelect?.addEventListener('change', (event) => {
        const agentId = parseInt(event.target.value, 10);
        if (Number.isInteger(agentId)) {
            selectAgent(agentId);
        }
    });
    elements.scanType?.addEventListener('change', handleScanTypeChange);
    elements.falconAgentSelect?.addEventListener('change', (event) => {
        const agentId = parseInt(event.target.value, 10);
        if (Number.isInteger(agentId)) {
            selectAgent(agentId);
        }
    });
    elements.wifiPrev?.addEventListener('click', () => changeNetworkPage(-1));
    elements.wifiNext?.addEventListener('click', () => changeNetworkPage(1));
    elements.bluetoothPrev?.addEventListener('click', () => changeBluetoothPage(-1));
    elements.bluetoothNext?.addEventListener('click', () => changeBluetoothPage(1));
    elements.detailClose?.addEventListener('click', () => hideDetailDrawer());
    elements.falconMonitorForm?.addEventListener('submit', onFalconMonitorStart);
    elements.falconMonitorStop?.addEventListener('click', onFalconMonitorStop);
    elements.falconScanForm?.addEventListener('submit', onFalconScanStart);
    elements.falconScanStop?.addEventListener('click', onFalconScanStop);
    elements.falconScanStatus?.addEventListener('click', onFalconScanStatus);
    elements.toggleWifi?.addEventListener('change', (event) => {
        state.showWifiLayers = event.target.checked;
        updateLayerVisibility();
    });
    elements.toggleBluetooth?.addEventListener('change', (event) => {
        state.showBluetoothLayers = event.target.checked;
        updateLayerVisibility();
    });
    elements.toggleAgents?.addEventListener('change', (event) => {
        state.showAgentLayers = event.target.checked;
        updateLayerVisibility();
    });
    elements.continuousList?.addEventListener('click', (event) => {
        const button = event.target.closest('.btn-stop-continuous');
        if (!button) return;
        const agentId = parseInt(button.dataset.agentId, 10);
        const interfaceName = button.dataset.interface;
        const scanType = button.dataset.scanType;
        if (!Number.isInteger(agentId)) return;
        stopContinuousScan(agentId, interfaceName, scanType);
    });
    elements.openSpectrumModal?.addEventListener('click', openSpectrumModal);
    elements.closeSpectrumModal?.addEventListener('click', closeSpectrumModal);
elements.spectrumStart24?.addEventListener('click', () => startSpectrumScan('24'));
elements.spectrumStart5?.addEventListener('click', () => startSpectrumScan('5'));
elements.spectrumStop?.addEventListener('click', () => stopSpectrumScan());
elements.spectrumSnapshot24?.addEventListener('click', () => spectrumSnapshot('24'));
elements.spectrumSnapshot5?.addEventListener('click', () => spectrumSnapshot('5'));
    elements.spectrumAgentSelect?.addEventListener('change', (event) => {
        const id = parseInt(event.target.value, 10);
        state.spectrumAgentId = Number.isFinite(id) ? id : null;
        if (state.spectrumPollHandle && state.spectrumAgentId) {
            beginSpectrumPolling(state.spectrumAgentId);
        }
    });
}

function handleScanTypeChange() {
    const type = elements.scanType?.value || 'wifi';
    const allowContinuous = type === 'wifi';
    if (elements.scanContinuous) {
        elements.scanContinuous.disabled = !allowContinuous;
        if (!allowContinuous) {
            elements.scanContinuous.checked = false;
        }
    }
    if (elements.scanInterval) {
        elements.scanInterval.disabled = !allowContinuous;
    }
}

function handleScanTypeChange() {
    const type = elements.scanType?.value || 'wifi';
    const allowContinuous = type === 'wifi';
    if (elements.scanContinuous) {
        elements.scanContinuous.disabled = !allowContinuous;
        if (!allowContinuous) {
            elements.scanContinuous.checked = false;
        }
    }
    if (elements.scanInterval) {
        elements.scanInterval.disabled = !allowContinuous;
    }
}

async function fetchJSON(path, options = {}) {
    const response = await fetch(path, options);
    if (!response.ok) {
        const text = await response.text();
        const error = new Error(text || response.statusText);
        error.status = response.status;
        throw error;
    }
    if (response.status === 204) return null;
    return response.json();
}

async function postJSON(path, payload) {
    return fetchJSON(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function deleteJSON(path) {
    const response = await fetch(path, { method: 'DELETE' });
    if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
    }
    return null;
}

async function loadAgents() {
    const agents = await fetchJSON(`${API_BASE}/agents`);
    const availableIds = new Set(agents.map(agent => agent.id));
    Array.from(state.falconScanning).forEach(agentId => {
        if (!availableIds.has(agentId)) {
            markFalconScanInactive(agentId);
        }
    });
    Array.from(state.monitorOverrides.keys()).forEach(agentId => {
        if (!availableIds.has(agentId)) {
            state.monitorOverrides.delete(agentId);
        }
    });
    if (state.selectedAgentId && !availableIds.has(state.selectedAgentId)) {
        state.selectedAgentId = null;
        hideDetailDrawer();
    }
    updateAgentCache(agents);
    renderAgentList(agents);
    renderAgentSelects(agents);
    if (!state.selectedAgentId && agents.length) {
        selectAgent(agents[0].id);
    } else if (!agents.length) {
        stopFalconPolling();
    }
}

function updateAgentCache(agents) {
    state.agentCache.clear();
    state.agentInterfaces.clear();
    state.agentMonitorMap.clear();
    agents.forEach(agent => {
        state.agentCache.set(agent.id, agent);
        cacheAgentMetadata(agent);
        updateAgentMarker(agent);
    });
}

function cacheAgentMetadata(agent) {
    const ifaceDict = agent.interfaces || {};
    const baseInterfaces = Object.keys(ifaceDict);
    let monitorMap = agent.monitor_map || {};
    monitorMap = applyMonitorOverrides(agent.id, monitorMap);
    const interfaces = new Set(baseInterfaces);
    Object.keys(monitorMap).forEach(managed => interfaces.add(managed));
    state.agentInterfaces.set(agent.id, Array.from(interfaces));
    state.agentMonitorMap.set(agent.id, { ...monitorMap });
}

function applyMonitorOverrides(agentId, monitorMap) {
    const overrides = state.monitorOverrides.get(agentId);
    if (!overrides) return monitorMap;
    const updated = { ...monitorMap };
    const remaining = new Map();
    overrides.forEach((alias, managed) => {
        if (alias) {
            if (monitorMap[managed] !== alias) {
                updated[managed] = alias;
                remaining.set(managed, alias);
            }
        } else if (monitorMap[managed]) {
            delete updated[managed];
            remaining.set(managed, alias);
        }
    });
    if (remaining.size) {
        state.monitorOverrides.set(agentId, remaining);
    } else {
        state.monitorOverrides.delete(agentId);
    }
    return updated;
}

function updateLocalMonitorState(agentId, managed, alias) {
    if (!agentId || !managed) return;
    const overrides = new Map(state.monitorOverrides.get(agentId) || []);
    overrides.set(managed, alias || null);
    state.monitorOverrides.set(agentId, overrides);
    const monitorMap = { ...(state.agentMonitorMap.get(agentId) || {}) };
    const interfaces = new Set(state.agentInterfaces.get(agentId) || []);
    const previousAlias = monitorMap[managed];
    if (alias) {
        monitorMap[managed] = alias;
        interfaces.add(managed);
    } else {
        if (previousAlias) {
            interfaces.delete(previousAlias);
        }
        delete monitorMap[managed];
        interfaces.add(managed);
    }
    state.agentMonitorMap.set(agentId, monitorMap);
    state.agentInterfaces.set(agentId, Array.from(interfaces));
    updateInterfaceControls();
}

function renderAgentList(agents) {
    elements.agentList.innerHTML = '';
    if (!agents.length) {
        elements.agentList.innerHTML = '<p class="agent-card">No agents registered</p>';
        return;
    }
    agents.forEach(agent => {
        const card = document.createElement('div');
        card.className = 'agent-card' + (agent.id === state.selectedAgentId ? ' active' : '');
        card.innerHTML = `
            <div class="agent-name">${agent.name}</div>
            <div class="agent-url">${agent.base_url}</div>
            <div class="agent-capabilities">${agent.capabilities.join(', ') || 'No capabilities'}</div>
            <div class="agent-actions">
                <button type="button" class="agent-delete-btn" data-agent-id="${agent.id}">Delete</button>
            </div>
        `;
        card.addEventListener('click', () => selectAgent(agent.id));
        elements.agentList.appendChild(card);
    });
    elements.agentList.querySelectorAll('.agent-delete-btn').forEach(button => {
        button.addEventListener('click', event => {
            event.stopPropagation();
            const agentId = parseInt(button.dataset.agentId, 10);
            handleAgentDelete(agentId);
        });
    });
}

function renderAgentSelects(agents) {
    const selects = [elements.scanAgentSelect, elements.falconAgentSelect];
    selects.forEach(select => {
        if (!select) return;
        select.innerHTML = '';
        agents.forEach(agent => {
            const option = document.createElement('option');
            option.value = agent.id;
            option.textContent = agent.name;
            if (agent.id === state.selectedAgentId) option.selected = true;
            select.appendChild(option);
        });
    });
    updateInterfaceControls();
    syncSpectrumAgentOptions(agents);
}

function syncSpectrumAgentOptions(agents) {
    if (!elements.spectrumAgentSelect) return;
    const previous = state.spectrumAgentId;
    elements.spectrumAgentSelect.innerHTML = '';
    agents.forEach(agent => {
        const option = document.createElement('option');
        option.value = agent.id;
        option.textContent = agent.name;
        elements.spectrumAgentSelect.appendChild(option);
    });
    if (!agents.length) {
        state.spectrumAgentId = null;
        return;
    }
    const match = agents.find(agent => agent.id === previous);
    const selectedId = match ? match.id : agents[0].id;
    elements.spectrumAgentSelect.value = `${selectedId}`;
    state.spectrumAgentId = selectedId;
}

async function selectAgent(agentId) {
    if (!state.agentCache.has(agentId)) return;
    state.selectedAgentId = agentId;
    renderAgentList(Array.from(state.agentCache.values()));
    renderAgentSelects(Array.from(state.agentCache.values()));
    updateInterfaceControls();
    await showAgentDetail(agentId);
}

async function showAgentDetail(agentId) {
    try {
        const [agent, status] = await Promise.all([
            fetchJSON(`${API_BASE}/agents/${agentId}`),
            fetchJSON(`${API_BASE}/agents/${agentId}/status`),
        ]);
        state.agentCache.set(agentId, agent);
        cacheAgentMetadata(agent);
        updateInterfaceControls();
        updateAgentMarker(agent);
        const html = buildAgentDetailHtml(agent, status);
        showDetailDrawer(html);
        let falconResults = null;
        if (state.falconScanning.has(agentId)) {
            try {
                falconResults = await fetchJSON(`${API_BASE}/falcon/${agentId}/scan/results`);
            } catch (err) {
                console.warn('Falcon results unavailable', err);
                markFalconScanInactive(agentId);
            }
        } else {
            state.falconData.delete(agentId);
        }
        renderFalconTab(agentId, falconResults);
        attachFalconActionHandlers(agentId, falconResults);
        await syncFalconScanState(agentId);
        if (state.falconScanning.has(agentId)) {
            startFalconPolling(agentId);
        } else {
            stopFalconPolling();
        }
    } catch (err) {
        const cached = state.agentCache.get(agentId);
        const status = { interfaces: cached?.interfaces || {}, bluetooth: {} };
        const html = cached ? buildAgentDetailHtml(cached, status) : `<h3>Agent ${agentId}</h3>`;
        showDetailDrawer(`
            ${html}
            <div class="detail-content-section detail-error">
                <p>${formatAgentError(err)}</p>
            </div>
        `);
    }
}

function startFalconPolling(agentId) {
    if (!agentId || !state.falconScanning.has(agentId)) return;
    if (state.falconPollingAgent === agentId && state.falconPollHandle) return;
    stopFalconPolling();
    state.falconPollingAgent = agentId;
    updateFalconIndicator();
    const poll = async () => {
        if (!state.falconScanning.has(agentId)) {
            markFalconScanInactive(agentId);
            return;
        }
        if (state.selectedAgentId !== agentId) {
            return;
        }
        const alias = state.falconScanInterfaces.get(agentId);
        try {
            const statusPromise = alias ? fetchFalconStatus(agentId, alias) : Promise.resolve(null);
            const results = await fetchJSON(`${API_BASE}/falcon/${agentId}/scan/results`);
            const status = await statusPromise;
            state.falconData.set(agentId, results);
            renderFalconTab(agentId, results);
            attachFalconActionHandlers(agentId, results);
            if (status && !isFalconStatusRunning(status)) {
                markFalconScanInactive(agentId);
            }
        } catch (err) {
            console.warn('Falcon poll failed', err);
            if (err?.status === 404 || !state.agentCache.has(agentId)) {
                markFalconScanInactive(agentId);
            }
        }
    };
    (async () => {
        await poll();
        if (!state.falconScanning.has(agentId)) return;
        state.falconPollHandle = setInterval(poll, 5000);
    })();
}

function stopFalconPolling() {
    if (state.falconPollHandle) {
        clearInterval(state.falconPollHandle);
        state.falconPollHandle = null;
    }
    state.falconPollingAgent = null;
    updateFalconIndicator();
}

function updateFalconIndicator() {
    const indicator = document.getElementById('falcon-poll-indicator');
    if (!indicator) return;
    const active = Boolean(state.falconPollingAgent);
    indicator.textContent = active ? 'Auto-refreshing results…' : 'Idle';
    indicator.classList.toggle('active', active);
}

function formatAgentError(err) {
    const message = err?.message || String(err);
    if (message.includes('Unable to reach')) {
        return 'Agent is temporarily offline or unreachable. Check connectivity and ensure the agent service is running.';
    }
    return `Error loading agent status: ${message}`;
}

function updateAgentMarker(agent) {
    if (!state.map || !agent.gps || !agent.gps.gpspos) return;
    const gps = agent.gps.gpspos;
    if (gps.latitude === undefined || gps.longitude === undefined) return;
    const lat = parseFloat(gps.latitude);
    const lon = parseFloat(gps.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    let marker = state.agentMarkers.get(agent.id);
    if (!marker) {
        marker = L.circleMarker([lat, lon], {
            radius: 8,
            color: '#00c8d7',
            weight: 2,
            fillColor: '#00c8d7',
            fillOpacity: 0.8,
        });
        state.agentMarkers.set(agent.id, marker);
    } else {
        marker.setLatLng([lat, lon]);
    }
    marker.bindPopup(`<strong>${agent.name}</strong><br/>${agent.base_url}`);
    applyMarkerVisibility(marker, state.showAgentLayers);
}

function showDetailDrawer(html) {
    elements.detailContent.innerHTML = html;
    elements.detailDrawer.classList.add('open');
    document.body.classList.remove('detail-collapsed');
    requestMapResize();
}

function hideDetailDrawer() {
    elements.detailDrawer.classList.remove('open');
    document.body.classList.add('detail-collapsed');
    requestMapResize();
    stopFalconPolling();
}

function buildAgentDetailHtml(agent, status) {
    return `
        <h3>${agent.name}</h3>
        <div class="detail-content-section">
            <strong>Base URL:</strong> ${agent.base_url}<br>
            <strong>Capabilities:</strong> ${agent.capabilities.join(', ')}
        </div>
        <details class="detail-content-section" open>
            <summary>Interfaces</summary>
            <pre>${JSON.stringify(status.interfaces, null, 2)}</pre>
        </details>
        <details class="detail-content-section">
            <summary>Bluetooth</summary>
            <pre>${JSON.stringify(status.bluetooth, null, 2)}</pre>
        </details>
        <details class="detail-content-section">
            <summary>Monitor Map</summary>
            <pre>${JSON.stringify(agent.monitor_map || {}, null, 2)}</pre>
        </details>
    `;
}

async function handleAgentDelete(agentId) {
    if (!agentId) return;
    if (!confirm('Remove this agent from the controller?')) return;
    try {
        await fetch(`${API_BASE}/agents/${agentId}`, { method: 'DELETE' });
        const marker = state.agentMarkers.get(agentId);
        if (marker && state.map && state.map.hasLayer(marker)) {
            state.map.removeLayer(marker);
        }
        state.agentMarkers.delete(agentId);
        markFalconScanInactive(agentId);
        state.monitorOverrides.delete(agentId);
        state.falconData.delete(agentId);
        state.agentCache.delete(agentId);
        state.agentInterfaces.delete(agentId);
        state.agentMonitorMap.delete(agentId);
        if (state.selectedAgentId === agentId) {
            state.selectedAgentId = null;
        }
        hideDetailDrawer();
        await loadAgents();
    } catch (err) {
        alert(`Unable to delete agent: ${err.message}`);
    }
}

function renderFalconSection(falconResults) {
    if (!falconResults) {
        return '<p>No Falcon data available.</p>';
    }
    const networks = falconResults.networks || [];
    const clients = falconResults.clients || [];
    const netPageSize = state.falconPageSize;
    const clientPageSize = state.falconPageSize;
    const netTotalPages = Math.max(1, Math.ceil(networks.length / netPageSize));
    const clientTotalPages = Math.max(1, Math.ceil(clients.length / clientPageSize));
    const netStart = (state.falconNetworkPage - 1) * netPageSize;
    const clientStart = (state.falconClientPage - 1) * clientPageSize;
    const pagedNetworks = networks.slice(netStart, netStart + netPageSize);
    const pagedClients = clients.slice(clientStart, clientStart + clientPageSize);
    const hasData = networks.length || clients.length;
    if (!hasData) {
        return '<div class="detail-content-section"><strong>Falcon</strong><p>No Falcon scan results yet.</p></div>';
    }
    const clientCounts = clients.reduce((acc, client) => {
        const key = (client.apMacAddr || '').toLowerCase();
        acc[key] = (acc[key] || 0) + 1;
        return acc;
    }, {});
    const networkRows = pagedNetworks.map(net => {
        const key = (net.macAddr || '').toLowerCase();
        const hasClient = !!clientCounts[key];
        return `
            <tr>
                <td>${escapeHtml(net.ssid || '')}</td>
                <td>${net.macAddr || ''}</td>
                <td>${net.channel || ''}</td>
                <td>${net.signal || ''}</td>
                <td>${net.security || ''}</td>
                <td>
                    <button class="btn-falcon-capture" data-ap="${net.macAddr || ''}" data-ssid="${escapeHtml(net.ssid || '')}" data-channel="${net.channel || 0}" data-hasclient="${hasClient}">Start WPA Capture</button>
                    <button class="btn-falcon-deauth-ap" data-ap="${net.macAddr || ''}" data-channel="${net.channel || 0}">Deauth AP</button>
                </td>
            </tr>`;
    }).join('') || '<tr><td colspan="6">No networks</td></tr>';

    const clientRows = pagedClients.map(client => `
            <tr>
                <td>${client.macAddr || ''}</td>
                <td>${client.apMacAddr || ''}</td>
                <td>${client.channel || ''}</td>
                <td>${client.signal || ''}</td>
                <td><button class="btn-falcon-deauth" data-ap="${client.apMacAddr || ''}" data-client="${client.macAddr || ''}" data-channel="${client.channel || 0}">Deauth</button></td>
            </tr>`).join('') || '<tr><td colspan="5">No clients</td></tr>';

    return `
        <div class="falcon-results-panel">
            <div class="falcon-actions">
                <div class="falcon-buttons">
                    <button class="btn-falcon-refresh">Refresh Falcon Data</button>
                    <button class="btn-falcon-stop-all">Stop All Deauths</button>
                </div>
                <span class="falcon-note">Use monitor interface dropdown before launching actions.</span>
            </div>
            <h4>Access Points</h4>
            <table class="falcon-table">
                <thead>
                    <tr>
                        <th>SSID</th>
                        <th>BSSID</th>
                        <th>Ch</th>
                        <th>Signal</th>
                        <th>Security</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>${networkRows}</tbody>
            </table>
            <div class="falcon-pagination">
                <button id="falcon-net-prev">Prev</button>
                <span>Page ${state.falconNetworkPage} / ${netTotalPages}</span>
                <button id="falcon-net-next">Next</button>
            </div>
            <h4>Clients</h4>
            <table class="falcon-table">
                <thead>
                    <tr>
                        <th>Client</th>
                        <th>Access Point</th>
                        <th>Ch</th>
                        <th>Signal</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>${clientRows}</tbody>
            </table>
            <div class="falcon-pagination">
                <button id="falcon-client-prev">Prev</button>
                <span>Page ${state.falconClientPage} / ${clientTotalPages}</span>
                <button id="falcon-client-next">Next</button>
            </div>
        </div>
    `;
}

function renderFalconTab(agentId, falconResults) {
    const container = document.getElementById('falcon-results');
    if (!container) return;
    if (!falconResults) {
        container.innerHTML = '<p>No Falcon data available.</p>';
        return;
    }
    state.falconNetworkPage = 1;
    state.falconClientPage = 1;
    container.innerHTML = renderFalconSection(falconResults);
    attachFalconPaginationHandlers(falconResults);
}

function attachFalconActionHandlers(agentId, falconResults) {
    const root = elements.detailContent;
    if (!root) return;
    root.querySelector('.btn-falcon-refresh')?.addEventListener('click', () => showAgentDetail(agentId));
    root.querySelector('.btn-falcon-stop-all')?.addEventListener('click', () => stopAllFalconDeauths(agentId));
    root.querySelectorAll('.btn-falcon-deauth').forEach(button => {
        button.addEventListener('click', () => {
            const ap = button.dataset.ap;
            const client = button.dataset.client;
            const channel = parseInt(button.dataset.channel, 10) || 0;
            triggerFalconDeauth(agentId, ap, client, channel);
        });
    });
    root.querySelectorAll('.btn-falcon-capture').forEach(button => {
        button.addEventListener('click', () => {
            const ap = button.dataset.ap;
            const ssid = button.dataset.ssid || '';
            const channel = parseInt(button.dataset.channel, 10) || 0;
            const hasClient = button.dataset.hasclient === 'true';
            triggerFalconCapture(agentId, ap, ssid, channel, hasClient);
        });
    });
    root.querySelectorAll('.btn-falcon-deauth-ap').forEach(button => {
        button.addEventListener('click', () => {
            const ap = button.dataset.ap;
            const channel = parseInt(button.dataset.channel, 10) || 0;
            triggerFalconDeauth(agentId, ap, '', channel);
        });
    });
}

function attachFalconPaginationHandlers(falconResults) {
    const networkPrev = document.getElementById('falcon-net-prev');
    const networkNext = document.getElementById('falcon-net-next');
    const clientPrev = document.getElementById('falcon-client-prev');
    const clientNext = document.getElementById('falcon-client-next');
    networkPrev?.addEventListener('click', () => changeFalconPage('network', -1, falconResults));
    networkNext?.addEventListener('click', () => changeFalconPage('network', 1, falconResults));
    clientPrev?.addEventListener('click', () => changeFalconPage('client', -1, falconResults));
    clientNext?.addEventListener('click', () => changeFalconPage('client', 1, falconResults));
}

function changeFalconPage(type, delta, falconResults) {
    if (type === 'network') {
        const totalPages = Math.max(1, Math.ceil((falconResults.networks?.length || 0) / state.falconPageSize));
        state.falconNetworkPage = Math.min(totalPages, Math.max(1, state.falconNetworkPage + delta));
    } else {
        const totalPages = Math.max(1, Math.ceil((falconResults.clients?.length || 0) / state.falconPageSize));
        state.falconClientPage = Math.min(totalPages, Math.max(1, state.falconClientPage + delta));
    }
    const container = document.getElementById('falcon-results');
    if (!container) return;
    container.innerHTML = renderFalconSection(falconResults);
    attachFalconPaginationHandlers(falconResults);
    attachFalconActionHandlers(state.selectedAgentId, falconResults);
}

function onQuickScanSubmit(event) {
    event.preventDefault();
    try {
        const { body, continuous, interval_seconds } = buildScanPayload();
        if (continuous) {
            body.interval_seconds = interval_seconds;
            postJSON(`${API_BASE}/scans/continuous`, body)
                .then(() => loadContinuousScans())
                .catch(err => alert(`Failed to start continuous scan: ${err.message}`));
        } else {
            postJSON(`${API_BASE}/scans`, body)
                .then(loadScans)
                .catch(err => alert(`Failed to launch scan: ${err.message}`));
        }
    } catch (err) {
        alert(err.message);
    }
}

function buildScanPayload() {
    const channelsText = document.getElementById('scan-channels').value;
    const extrasText = document.getElementById('scan-extras').value;
    let channels = null;
    if (channelsText.trim()) {
        channels = channelsText.split(',').map(v => parseInt(v.trim(), 10)).filter(Number.isFinite);
    }
    let extras = null;
    if (extrasText.trim()) {
        try {
            extras = JSON.parse(extrasText);
        } catch (err) {
            throw new Error('Extras must be valid JSON');
        }
    }
    const body = {
        agent_id: parseInt(elements.scanAgentSelect.value, 10),
        scan_type: document.getElementById('scan-type').value,
        interface: document.getElementById('scan-interface').value || null,
        channels,
        extras,
    };
    const intervalInput = elements.scanInterval ? parseInt(elements.scanInterval.value, 10) : 10;
    const allowContinuous = body.scan_type === 'wifi';
    return {
        body,
        continuous: allowContinuous ? (elements.scanContinuous?.checked ?? false) : false,
        interval_seconds: Number.isFinite(intervalInput) ? Math.max(intervalInput, 2) : 10,
    };
}

function onAgentFormSubmit(event) {
    event.preventDefault();
    const payload = {
        name: document.getElementById('agent-name').value,
        base_url: document.getElementById('agent-url').value,
        description: document.getElementById('agent-description').value,
        capabilities: document.getElementById('agent-capabilities').value.split(',').map(c => c.trim()).filter(Boolean),
    };
    postJSON(`${API_BASE}/agents`, payload)
        .then(() => {
            elements.agentForm.reset();
            elements.agentModal.classList.add('hidden');
            return loadAgents();
        })
        .catch(err => alert(`Failed to register agent: ${err.message}`));
}

async function loadScans() {
    const scans = await fetchJSON(`${API_BASE}/scans?limit=20`);
    renderScans(scans);
    scans.forEach(scan => ingestScanPayload(scan.agent_id, scan.scan_type, scan.response_payload, scan.id));
}

async function loadContinuousScans() {
    try {
        const loops = await fetchJSON(`${API_BASE}/scans/continuous`);
        state.continuousScans = loops;
        renderContinuousList();
    } catch (err) {
        console.error('Unable to load continuous scans', err);
    }
}

function renderScans(scans) {
    elements.scansTableBody.innerHTML = '';
    scans.forEach(scan => {
        const row = document.createElement('tr');
        const agentName = state.agentCache.get(scan.agent_id)?.name || scan.agent_id;
        const statusBadge = `<span class="status-badge status-${scan.status}">${scan.status}</span>`;
        row.innerHTML = `
            <td>${scan.id}</td>
            <td>${agentName}</td>
            <td>${scan.scan_type}</td>
            <td>${statusBadge}</td>
            <td>${new Date(scan.created_at).toLocaleString()}</td>
            <td><pre>${summarizeScan(scan)}</pre></td>
        `;
        elements.scansTableBody.appendChild(row);
    });
}

function summarizeScan(scan) {
    if (scan.response_payload) {
        const payload = scan.response_payload;
        if (Array.isArray(payload.networks)) {
            return `${payload.networks.length} networks`;
        }
        if (Array.isArray(payload.clients)) {
            return `${payload.clients.length} clients`;
        }
        if (Array.isArray(payload.devices)) {
            return `${payload.devices.length} devices`;
        }
        return JSON.stringify(payload, null, 2);
    }
    if (scan.error) return scan.error;
    return '';
}

function ingestScanPayload(agentId, scanType, payload, scanId) {
    if (!payload) return;
    const scanCreatedAt = payload.created_at || payload.createdAt || null;
    if (Array.isArray(payload.networks)) {
        addOrUpdateNetworks(payload.networks, agentId, scanId, scanType, scanCreatedAt);
    }
    if (Array.isArray(payload.clients)) {
        addOrUpdateNetworks(payload.clients, agentId, scanId, scanType, scanCreatedAt, { label: 'client' });
    }
    if (Array.isArray(payload.devices)) {
        addOrUpdateBluetooth(payload.devices, agentId, scanId, scanType, scanCreatedAt);
    }
}

function addOrUpdateNetworks(items, agentId, scanId, scanType, scanCreatedAt = null, options = {}) {
    const agentName = state.agentCache.get(agentId)?.name || `Agent ${agentId}`;
    const agentGps = state.agentCache.get(agentId)?.gps?.gpspos || null;
    const parseTimestamp = (value) => {
        if (value instanceof Date) return value.getTime();
        if (typeof value === 'number' && Number.isFinite(value)) return value;
        if (typeof value === 'string' && value.length) {
            const parsed = Date.parse(value);
            if (!Number.isNaN(parsed)) return parsed;
        }
        return null;
    };
    const scanTs = parseTimestamp(scanCreatedAt) || Date.now();
    const clampTs = (ts) => {
        if (!ts) return scanTs;
        return ts > scanTs ? scanTs : ts;
    };
    items.forEach(item => {
        const mac = (item.macAddr || item.macaddr || '').toLowerCase();
        if (!mac) return;
        const rawLat = parseFloat(item.lat ?? item.latitude);
        const rawLon = parseFloat(item.lon ?? item.longitude);
        const gpsValid = interpretBool(item.gpsvalid ?? item.gpsValid ?? true);
        let sampleLat = Number.isFinite(rawLat) ? rawLat : null;
        let sampleLon = Number.isFinite(rawLon) ? rawLon : null;
        let locationSource = 'device';
        if (!gpsValid || sampleLat === null || sampleLon === null) {
            if (agentGps && Number.isFinite(parseFloat(agentGps.latitude)) && Number.isFinite(parseFloat(agentGps.longitude))) {
                sampleLat = parseFloat(agentGps.latitude);
                sampleLon = parseFloat(agentGps.longitude);
                locationSource = 'agent';
            } else {
                sampleLat = null;
                sampleLon = null;
                locationSource = 'none';
            }
        }
        const signal = item.signal ?? item.power ?? null;
        if (sampleLat !== null && sampleLon !== null) {
            recordNetworkSample(mac, {
                agentId,
                agentName,
                lat: sampleLat,
                lon: sampleLon,
                signal,
                source: locationSource,
                timestamp: clampTs(parseTimestamp(item.lastseen ?? item.lastSeen ?? item.firstseen ?? item.firstSeen)),
            });
        }
        const loc = computeNetworkLocation(mac);
        const prev = state.networkIndex.get(mac);
        const finalLat = loc.hasPosition ? loc.lat : prev?.lat ?? null;
        const finalLon = loc.hasPosition ? loc.lon : prev?.lon ?? null;
        const finalSource = loc.hasPosition ? loc.source : prev?.locationSource ?? 'none';
        const contributors = loc.hasPosition ? loc.contributors : prev?.contributors ?? 0;
        const seenTs = clampTs(parseTimestamp(item.lastseen ?? item.lastSeen ?? item.firstseen ?? item.firstSeen));
        const info = {
            mac,
            ssid: item.ssid || item.name || options.label || 'Unknown',
            channel: item.channel || '',
            signal: item.signal || item.power || '',
            lat: finalLat,
            lon: finalLon,
            hasGps: finalLat !== null && finalLon !== null,
            locationSource: finalSource,
            contributors,
            lastSeen: new Date(seenTs).toISOString(),
            scanCreatedAt: new Date(scanTs).toISOString(),
            agentId,
            agentName,
            scanId,
            scanType,
        };
        state.networkIndex.set(mac, info);
        if (info.hasGps) {
            upsertMarker(info);
        } else {
            removeNetworkMarker(mac);
        }
    });
    updateNetworkList();
}

function interpretBool(value) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') return value.toLowerCase() === 'true';
    return Boolean(value);
}

function upsertMarker(info) {
    if (!state.map) return;
    const existing = state.networkMarkers.get(info.mac);
    const popup = `
        <strong>${info.ssid}</strong><br/>
        MAC: ${info.mac}<br/>
        Agent: ${info.agentName}<br/>
        Channel: ${info.channel}<br/>
        Signal: ${info.signal}<br/>
        Last seen: ${new Date(info.lastSeen).toLocaleString()}`;
    if (existing) {
        existing.setLatLng([info.lat, info.lon]);
        existing.setPopupContent(popup);
        applyMarkerVisibility(existing, state.showWifiLayers);
    } else {
        const marker = L.marker([info.lat, info.lon]);
        marker.bindPopup(popup);
        state.networkMarkers.set(info.mac, marker);
        applyMarkerVisibility(marker, state.showWifiLayers);
    }
}

function removeNetworkMarker(mac) {
    if (!state.map) return;
    const marker = state.networkMarkers.get(mac);
    if (marker) {
        if (state.map.hasLayer(marker)) {
            state.map.removeLayer(marker);
        }
        state.networkMarkers.delete(mac);
    }
}

function upsertBluetoothMarker(info) {
    if (!state.map) return;
    if (!info.gpsValid || !Number.isFinite(info.lat) || !Number.isFinite(info.lon)) return;
    const existing = state.bluetoothMarkers.get(info.mac);
    const popup = `
        <strong>${info.name}</strong><br/>
        MAC: ${info.mac}<br/>
        Agent: ${info.agentName}<br/>
        RSSI: ${info.rssi}<br/>
        Last seen: ${new Date(info.lastSeen).toLocaleString()}`;
    if (existing) {
        existing.setLatLng([info.lat, info.lon]);
        existing.setPopupContent(popup);
        applyMarkerVisibility(existing, state.showBluetoothLayers);
    } else {
        const marker = L.circleMarker([info.lat, info.lon], {
            radius: 6,
            color: '#ff9d00',
            weight: 2,
            fillColor: '#ff9d00',
            fillOpacity: 0.8,
        });
        marker.bindPopup(popup);
        state.bluetoothMarkers.set(info.mac, marker);
        applyMarkerVisibility(marker, state.showBluetoothLayers);
    }
}

function updateNetworkList() {
    state.networkList = Array.from(state.networkIndex.values()).sort((a, b) => new Date(b.lastSeen) - new Date(a.lastSeen));
    elements.wifiCount.textContent = state.networkList.length;
    if ((state.networkPage - 1) * state.pageSize >= state.networkList.length) {
        state.networkPage = 1;
    }
    renderNetworkTable();
}

function addOrUpdateBluetooth(devices, agentId, scanId, scanType, scanCreatedAt = null) {
    const agentName = state.agentCache.get(agentId)?.name || `Agent ${agentId}`;
    const parseTimestamp = (value) => {
        if (value instanceof Date) return value.getTime();
        if (typeof value === 'number' && Number.isFinite(value)) return value;
        if (typeof value === 'string' && value.length) {
            const parsed = Date.parse(value);
            if (!Number.isNaN(parsed)) return parsed;
        }
        return null;
    };
    const scanTs = parseTimestamp(scanCreatedAt) || Date.now();
    const clampTs = (ts) => {
        if (!ts) return scanTs;
        return ts > scanTs ? scanTs : ts;
    };
    devices.forEach(device => {
        const mac = (device.mac || device.macAddr || '').toLowerCase();
        if (!mac) return;
        const sampleLat = parseFloat(device.lat ?? device.latitude);
        const sampleLon = parseFloat(device.lon ?? device.longitude);
        const gpsValid = interpretBool(device.gpsvalid ?? device.gpsValid ?? false);
        let finalLat = Number.isFinite(sampleLat) ? sampleLat : null;
        let finalLon = Number.isFinite(sampleLon) ? sampleLon : null;
        let source = gpsValid && finalLat !== null && finalLon !== null ? 'device' : 'none';
        const agentGps = state.agentCache.get(agentId)?.gps?.gpspos;
        if (source === 'none' && agentGps && Number.isFinite(parseFloat(agentGps.latitude)) && Number.isFinite(parseFloat(agentGps.longitude))) {
            finalLat = parseFloat(agentGps.latitude);
            finalLon = parseFloat(agentGps.longitude);
            source = 'agent';
        }
        if (finalLat !== null && finalLon !== null) {
            recordBluetoothSample(mac, {
                agentId,
                agentName,
                lat: finalLat,
                lon: finalLon,
                signal: device.rssi ?? device.signal ?? null,
                source,
                timestamp: clampTs(parseTimestamp(device.lastseen ?? device.lastSeen ?? device.firstseen ?? device.firstSeen)),
            });
        }
        const loc = computeBluetoothLocation(mac);
        const prev = state.bluetoothIndex.get(mac);
        const hasGps = loc.hasPosition || (prev?.lat !== undefined && prev.lat !== null);
        const seenTs = clampTs(parseTimestamp(device.lastseen ?? device.lastSeen ?? device.firstseen ?? device.firstSeen));
        const info = {
            mac,
            name: device.name || 'Unknown',
            rssi: device.rssi ?? device.signal ?? '',
            lastSeen: new Date(seenTs).toISOString(),
            scanCreatedAt: new Date(scanTs).toISOString(),
            agentId,
            agentName,
            scanId,
            scanType,
            lat: loc.hasPosition ? loc.lat : prev?.lat ?? null,
            lon: loc.hasPosition ? loc.lon : prev?.lon ?? null,
            gpsValid: hasGps,
            locationSource: loc.hasPosition ? loc.source : prev?.locationSource ?? 'none',
            contributors: loc.hasPosition ? loc.contributors : prev?.contributors ?? 0,
        };
        state.bluetoothIndex.set(mac, info);
        if (info.lat !== null && info.lon !== null) {
            upsertBluetoothMarker(info);
        }
    });
    updateBluetoothList();
}

function updateBluetoothList() {
    state.bluetoothList = Array.from(state.bluetoothIndex.values()).sort((a, b) => new Date(b.lastSeen) - new Date(a.lastSeen));
    elements.bluetoothCount.textContent = state.bluetoothList.length;
    if ((state.bluetoothPage - 1) * state.pageSize >= state.bluetoothList.length) {
        state.bluetoothPage = 1;
    }
    renderBluetoothTable();
}

function renderNetworkTable() {
    elements.networkTableBody.innerHTML = '';
    const start = (state.networkPage - 1) * state.pageSize;
    const rows = state.networkList.slice(start, start + state.pageSize);
    rows.forEach(net => {
        const row = document.createElement('tr');
        const locationLabel = net.hasGps ? formatLocationLabel(net.locationSource) : 'No';
        const scanTime = net.scanCreatedAt ? new Date(net.scanCreatedAt).toLocaleString() : 'n/a';
        const lastSeen = net.lastSeen ? new Date(net.lastSeen).toLocaleString() : 'n/a';
        row.innerHTML = `
            <td>${net.ssid}</td>
            <td>${net.mac}</td>
            <td>${net.agentName}</td>
            <td>${net.channel}</td>
            <td>${net.signal}</td>
            <td>${locationLabel}</td>
            <td title="Scan: ${scanTime}">${lastSeen}</td>`;
        row.addEventListener('click', () => showNetworkDetail(net));
        elements.networkTableBody.appendChild(row);
    });
    elements.wifiPage.textContent = `${state.networkPage}`;
}

function showNetworkDetail(net) {
    const coords = formatCoordinates(net.lat, net.lon);
    const scanTime = net.scanCreatedAt ? new Date(net.scanCreatedAt).toLocaleString() : 'n/a';
    const lastSeen = net.lastSeen ? new Date(net.lastSeen).toLocaleString() : 'n/a';
    const html = `
        <h3>${net.ssid}</h3>
        <div class="detail-content-section">
            <strong>MAC:</strong> ${net.mac}<br>
            <strong>Agent:</strong> ${net.agentName}<br>
            <strong>Signal:</strong> ${net.signal} dBm<br>
            <strong>Channel:</strong> ${net.channel}<br>
            <strong>Location Source:</strong> ${formatLocationLabel(net.locationSource)}<br>
            <strong>Contributors:</strong> ${net.contributors || 0}<br>
            <strong>Location:</strong> ${coords || 'N/A'}
        </div>
        <div class="detail-content-section">
            <strong>Last seen (agent):</strong> ${lastSeen}<br>
            <strong>Scan time (controller):</strong> ${scanTime}<br>
            <strong>Scan:</strong> #${net.scanId} (${net.scanType})
        </div>`;
    showDetailDrawer(html);
}

function changeNetworkPage(delta) {
    const maxPage = Math.max(1, Math.ceil(state.networkList.length / state.pageSize));
    state.networkPage = Math.min(maxPage, Math.max(1, state.networkPage + delta));
    renderNetworkTable();
}

function renderBluetoothTable() {
    elements.bluetoothTableBody.innerHTML = '';
    const start = (state.bluetoothPage - 1) * state.pageSize;
    const rows = state.bluetoothList.slice(start, start + state.pageSize);
    rows.forEach(device => {
        const row = document.createElement('tr');
        const locationLabel = device.gpsValid ? formatLocationLabel(device.locationSource) : 'No';
        const scanTime = device.scanCreatedAt ? new Date(device.scanCreatedAt).toLocaleString() : 'n/a';
        const lastSeen = device.lastSeen ? new Date(device.lastSeen).toLocaleString() : 'n/a';
        row.innerHTML = `
            <td>${device.name}</td>
            <td>${device.mac}</td>
            <td>${device.agentName}</td>
            <td>${device.rssi}</td>
            <td>${locationLabel}</td>
            <td title="Scan: ${scanTime}">${lastSeen}</td>`;
        row.addEventListener('click', () => showBluetoothDetail(device));
        elements.bluetoothTableBody.appendChild(row);
    });
    elements.bluetoothPage.textContent = `${state.bluetoothPage}`;
}

function showBluetoothDetail(device) {
    const coords = formatCoordinates(device.lat, device.lon);
    const scanTime = device.scanCreatedAt ? new Date(device.scanCreatedAt).toLocaleString() : 'n/a';
    const lastSeen = device.lastSeen ? new Date(device.lastSeen).toLocaleString() : 'n/a';
    const html = `
        <h3>${device.name}</h3>
        <div class="detail-content-section">
            <strong>MAC:</strong> ${device.mac}<br>
            <strong>Agent:</strong> ${device.agentName}<br>
            <strong>RSSI:</strong> ${device.rssi}<br>
            <strong>Location Source:</strong> ${formatLocationLabel(device.locationSource)}<br>
            <strong>Contributors:</strong> ${device.contributors || 0}<br>
            <strong>Location:</strong> ${coords || 'N/A'}
        </div>
        <div class="detail-content-section">
            <strong>Last seen (agent):</strong> ${lastSeen}<br>
            <strong>Scan time (controller):</strong> ${scanTime}<br>
            <strong>Scan:</strong> #${device.scanId} (${device.scanType})
        </div>`;
    showDetailDrawer(html);
}

function changeBluetoothPage(delta) {
    const maxPage = Math.max(1, Math.ceil(state.bluetoothList.length / state.pageSize));
    state.bluetoothPage = Math.min(maxPage, Math.max(1, state.bluetoothPage + delta));
    renderBluetoothTable();
}

function formatCoordinates(lat, lon) {
    if (lat === undefined || lon === undefined) return '';
    const parsedLat = parseFloat(lat);
    const parsedLon = parseFloat(lon);
    if (!Number.isFinite(parsedLat) || !Number.isFinite(parsedLon)) return '';
    return `${parsedLat.toFixed(5)}, ${parsedLon.toFixed(5)}`;
}

function formatLocationLabel(source) {
    switch (source) {
        case 'device':
            return 'Direct';
        case 'agent':
            return 'Agent GPS';
        case 'centroid':
            return 'Centroid';
        default:
            return 'No';
    }
}

function openSpectrumModal() {
    if (!elements.spectrumModal) return;
    const quickAgent = parseInt(elements.scanAgentSelect?.value ?? '', 10);
    if (Number.isInteger(quickAgent)) {
        elements.spectrumAgentSelect.value = `${quickAgent}`;
        state.spectrumAgentId = quickAgent;
    }
    elements.spectrumModal.classList.remove('hidden');
    ensureSpectrumChart();
    setSpectrumStatus('Idle');
    updateSpectrumControls(Boolean(state.spectrumBand));
}

function closeSpectrumModal() {
    if (!elements.spectrumModal) return;
    stopSpectrumPolling();
    state.spectrumBand = null;
    state.spectrumSnapshotting = false;
    updateSpectrumControls(false);
    elements.spectrumModal.classList.add('hidden');
}

function labelForBand(band) {
    return band === '5' ? '5 GHz' : band === '24' ? '2.4 GHz' : 'unknown band';
}

function setSpectrumStatus(message) {
    if (!elements.spectrumStatus) return;
    const bandLabel =
        state.spectrumBand === '24' ? '2.4 GHz' : state.spectrumBand === '5' ? '5 GHz' : 'band unset';
    elements.spectrumStatus.textContent =
        state.spectrumBand && message ? `${message} (${bandLabel})` : message || 'Idle';
}

function ensureSpectrumChart() {
    if (state.spectrumChart || !elements.spectrumChartCanvas) return;
    const ctx = elements.spectrumChartCanvas.getContext('2d');
    state.spectrumChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Power (dBm)',
                    data: [],
                    borderColor: '#00c8d7',
                    backgroundColor: 'rgba(0, 200, 215, 0.2)',
                    tension: 0.2,
                },
            ],
        },
        options: {
            animation: false,
            scales: {
                y: {
                    suggestedMin: -110,
                    suggestedMax: -20,
                    ticks: { color: '#9acfe1' },
                },
                x: {
                    ticks: { color: '#9acfe1' },
                },
            },
            plugins: {
                legend: { labels: { color: '#e0e6f3' } },
            },
        },
    });
}

function updateSpectrumChart(channelData) {
    ensureSpectrumChart();
    if (!state.spectrumChart) return;
    const entries = Object.entries(channelData || {})
        .map(([channel, value]) => ({ channel: Number(channel), value }))
        .sort((a, b) => a.channel - b.channel);
    state.spectrumChart.data.labels = entries.map(entry => entry.channel);
    state.spectrumChart.data.datasets[0].data = entries.map(entry => entry.value);
    state.spectrumChart.update('none');
}

function getSpectrumAgentId() {
    const id = parseInt(elements.spectrumAgentSelect?.value ?? '', 10);
    if (Number.isInteger(id)) return id;
    if (Number.isInteger(state.spectrumAgentId)) return state.spectrumAgentId;
    return null;
}

function startSpectrumScan(band) {
    const agentId = getSpectrumAgentId();
    if (!agentId) {
        return alert('Select an agent first');
    }
    if (state.spectrumSnapshotting) {
        return alert('Please wait for the current snapshot to finish.');
    }
    postJSON(`${API_BASE}/spectrum/${agentId}/start?band=${band}`)
        .then(() => {
            state.spectrumBand = band;
            state.spectrumAgentId = agentId;
            setSpectrumStatus('Scan running');
            beginSpectrumPolling(agentId);
            updateSpectrumControls(true);
        })
        .catch(err => alert(`Unable to start spectrum scan: ${err.message}`));
}

async function sendSpectrumStop(agentId, silent = false) {
    try {
        await postJSON(`${API_BASE}/spectrum/${agentId}/stop`, {});
        return true;
    } catch (err) {
        if (!silent) {
            throw err;
        }
        console.warn('Unable to stop spectrum scan', err);
        return false;
    }
}

async function stopSpectrumScan(manual = true) {
    const agentId = getSpectrumAgentId();
    if (!agentId) return;
    try {
        await sendSpectrumStop(agentId);
        if (manual) {
            setSpectrumStatus('Scan stopped');
        }
    } catch (err) {
        alert(`Unable to stop spectrum scan: ${err.message}`);
        return;
    } finally {
        state.spectrumBand = null;
        stopSpectrumPolling();
        updateSpectrumControls(false);
    }
}

async function spectrumSnapshot(band) {
    const agentId = getSpectrumAgentId();
    if (!agentId) return alert('Select an agent first');
    if (state.spectrumSnapshotting) return;
    const bandLabel = labelForBand(band);
    stopSpectrumPolling();
    state.spectrumSnapshotting = true;
    setSpectrumStatus(`Capturing ${bandLabel} snapshot...`);
    updateSpectrumControls(Boolean(state.spectrumBand));
    try {
        if (state.spectrumBand) {
            await sendSpectrumStop(agentId, true);
            state.spectrumBand = null;
            await delay(250);
        }
        state.spectrumBand = band;
        await postJSON(`${API_BASE}/spectrum/${agentId}/start?band=${band}`);
        await delay(SPECTRUM_SNAPSHOT_DELAY_MS);
        await fetchSpectrumChannels(agentId, true);
    } catch (err) {
        setSpectrumStatus(`Snapshot failed: ${err.message}`);
    } finally {
        await sendSpectrumStop(agentId, true);
        state.spectrumBand = null;
        state.spectrumSnapshotting = false;
        updateSpectrumControls(false);
    }
}

function beginSpectrumPolling(agentId) {
    stopSpectrumPolling();
    state.spectrumPollHandle = setInterval(() => fetchSpectrumChannels(agentId, false), 2000);
    fetchSpectrumChannels(agentId, false);
}

function stopSpectrumPolling() {
    if (state.spectrumPollHandle) {
        clearInterval(state.spectrumPollHandle);
        state.spectrumPollHandle = null;
    }
}

async function fetchSpectrumChannels(agentId, snapshot) {
    try {
        const data = await fetchJSON(`${API_BASE}/spectrum/${agentId}/channels`);
        const channelData = data?.channeldata || {};
        updateSpectrumChart(channelData);
        const running = Boolean(data?.scanrunning);
        if (running && !state.spectrumBand) {
            state.spectrumBand = 'unknown';
        }
        if (!running && state.spectrumBand) {
            setSpectrumStatus('Scan idle');
            state.spectrumBand = null;
            updateSpectrumControls(false);
        } else if (running) {
            setSpectrumStatus(snapshot ? 'Snapshot captured' : 'Scan running');
            updateSpectrumControls(true);
        } else if (snapshot) {
            setSpectrumStatus('Snapshot captured (no active scan)');
            updateSpectrumControls(false);
        }
    } catch (err) {
        setSpectrumStatus(`Error: ${err.message}`);
        if (!snapshot) {
            stopSpectrumPolling();
            state.spectrumBand = null;
            updateSpectrumControls(false);
        }
    }
}

function updateSpectrumControls(isRunning) {
    const running = Boolean(isRunning);
    const busy = state.spectrumSnapshotting;
    [elements.spectrumStart24, elements.spectrumStart5].forEach(button => {
        if (!button) return;
        button.disabled = running || busy;
    });
    if (elements.spectrumStop) {
        elements.spectrumStop.disabled = !running || busy;
    }
    [elements.spectrumSnapshot24, elements.spectrumSnapshot5].forEach(button => {
        if (!button) return;
        button.disabled = running || busy;
    });
}

function recordNetworkSample(mac, sample) {
    const existing = state.networkObservations.get(mac) || [];
    const cutoff = Date.now() - LOCATION_WINDOW_MS;
    const filtered = existing.filter(entry => entry.timestamp >= cutoff);
    filtered.push(sample);
    state.networkObservations.set(mac, filtered);
}

function computeNetworkLocation(mac) {
    const samples = state.networkObservations.get(mac) || [];
    const cutoff = Date.now() - LOCATION_WINDOW_MS;
    const recent = samples.filter(entry => entry.timestamp >= cutoff && Number.isFinite(entry.lat) && Number.isFinite(entry.lon));
    state.networkObservations.set(mac, recent);
    if (!recent.length) {
        return { hasPosition: false, contributors: 0 };
    }
    const latestPerAgent = new Map();
    recent.forEach(sample => {
        const existing = latestPerAgent.get(sample.agentId);
        if (!existing || sample.timestamp > existing.timestamp) {
            latestPerAgent.set(sample.agentId, sample);
        }
    });
    const values = Array.from(latestPerAgent.values());
    if (!values.length) {
        return { hasPosition: false, contributors: 0 };
    }
    if (values.length === 1) {
        const single = values[0];
        return {
            hasPosition: true,
            lat: single.lat,
            lon: single.lon,
            source: single.source,
            contributors: 1,
        };
    }
    let weightedLat = 0;
    let weightedLon = 0;
    let totalWeight = 0;
    values.forEach(sample => {
        const signal = Number(sample.signal);
        const weight = Number.isFinite(signal) ? Math.max(0.1, (120 + signal) / 60) : 1;
        weightedLat += sample.lat * weight;
        weightedLon += sample.lon * weight;
        totalWeight += weight;
    });
    if (!totalWeight) {
        return { hasPosition: false, contributors: values.length };
    }
    return {
        hasPosition: true,
        lat: weightedLat / totalWeight,
        lon: weightedLon / totalWeight,
        source: 'centroid',
        contributors: values.length,
    };
}

function recordBluetoothSample(mac, sample) {
    const existing = state.bluetoothObservations.get(mac) || [];
    const cutoff = Date.now() - LOCATION_WINDOW_MS;
    const filtered = existing.filter(entry => entry.timestamp >= cutoff);
    filtered.push(sample);
    state.bluetoothObservations.set(mac, filtered);
}

function computeBluetoothLocation(mac) {
    const samples = state.bluetoothObservations.get(mac) || [];
    const cutoff = Date.now() - LOCATION_WINDOW_MS;
    const recent = samples.filter(entry => entry.timestamp >= cutoff && Number.isFinite(entry.lat) && Number.isFinite(entry.lon));
    state.bluetoothObservations.set(mac, recent);
    if (!recent.length) {
        return { hasPosition: false, contributors: 0 };
    }
    const latestPerAgent = new Map();
    recent.forEach(sample => {
        const existing = latestPerAgent.get(sample.agentId);
        if (!existing || sample.timestamp > existing.timestamp) {
            latestPerAgent.set(sample.agentId, sample);
        }
    });
    const values = Array.from(latestPerAgent.values());
    if (!values.length) return { hasPosition: false, contributors: 0 };
    if (values.length === 1) {
        const single = values[0];
        return {
            hasPosition: true,
            lat: single.lat,
            lon: single.lon,
            source: single.source,
            contributors: 1,
        };
    }
    let weightedLat = 0;
    let weightedLon = 0;
    let totalWeight = 0;
    values.forEach(sample => {
        const rssi = Number(sample.signal);
        const weight = Number.isFinite(rssi) ? Math.max(0.1, (100 + rssi) / 60) : 1;
        weightedLat += sample.lat * weight;
        weightedLon += sample.lon * weight;
        totalWeight += weight;
    });
    if (!totalWeight) return { hasPosition: false, contributors: values.length };
    return {
        hasPosition: true,
        lat: weightedLat / totalWeight,
        lon: weightedLon / totalWeight,
        source: 'centroid',
        contributors: values.length,
    };
}

function escapeHtml(value) {
    if (!value) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function renderContinuousList() {
    if (!elements.continuousList) return;
    if (!state.continuousScans.length) {
        elements.continuousList.innerHTML = '<p>No continuous scans running.</p>';
        return;
    }
    const rows = state.continuousScans
        .map(item => {
            const agentName = state.agentCache.get(item.agent_id)?.name || item.agent_id;
            return `<div class="continuous-row">
                <div>
                    <strong>${agentName}</strong><br/>
                    ${item.scan_type.toUpperCase()} on ${item.interface} every ${item.interval_seconds}s
                </div>
                <button class="btn-stop-continuous" data-agent-id="${item.agent_id}" data-interface="${item.interface}" data-scan-type="${item.scan_type}">Stop</button>
            </div>`;
        })
        .join('');
    elements.continuousList.innerHTML = rows;
}

async function stopContinuousScan(agentId, interfaceName, scanType) {
    try {
        await postJSON(`${API_BASE}/scans/continuous/stop`, {
            agent_id: agentId,
            interface: interfaceName,
            scan_type: scanType,
        });
        loadContinuousScans();
    } catch (err) {
        alert(`Unable to stop continuous scan: ${err.message}`);
    }
}

function applyMarkerVisibility(marker, shouldShow) {
    if (!state.map || !marker) return;
    if (shouldShow) {
        if (!state.map.hasLayer(marker)) {
            marker.addTo(state.map);
        }
    } else if (state.map.hasLayer(marker)) {
        state.map.removeLayer(marker);
    }
}

function updateLayerVisibility() {
    state.networkMarkers.forEach(marker => applyMarkerVisibility(marker, state.showWifiLayers));
    state.bluetoothMarkers.forEach(marker => applyMarkerVisibility(marker, state.showBluetoothLayers));
    state.agentMarkers.forEach(marker => applyMarkerVisibility(marker, state.showAgentLayers));
}

function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${protocol}://${window.location.host}/ws/scans`;
    state.ws = new WebSocket(wsUrl);
    state.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleScanEvent(data);
        } catch (err) {
            console.error('invalid scan event', err);
        }
    };
    state.ws.onclose = () => setTimeout(initWebSocket, 3000);
}

function handleScanEvent(event) {
    if (event.event === 'scan.progress' && event.update?.snapshot) {
        ingestScanPayload(event.agent_id, event.scan_type, event.update.snapshot, event.scan_id);
    } else if (event.event === 'scan.completed') {
        ingestScanPayload(event.agent_id, event.scan_type, event.response, event.scan_id);
        loadScans().catch(err => console.error(err));
    } else if (event.event === 'scan.started' || event.event === 'scan.failed') {
        loadScans().catch(err => console.error(err));
    }
}

function logFalcon(message) {
    const timestamp = new Date().toLocaleTimeString();
    elements.falconStatusLog.textContent = `[${timestamp}] ${message}\n` + elements.falconStatusLog.textContent;
}

function getMonitorAliases(agentId) {
    const monitorMap = state.agentMonitorMap.get(agentId) || {};
    return Object.values(monitorMap).filter(Boolean);
}

function isFalconStatusRunning(status) {
    if (!status || typeof status !== 'object') return false;
    if (typeof status.running === 'boolean') return status.running;
    if (typeof status.running === 'string') {
        const normalized = status.running.toLowerCase();
        if (normalized === 'true' || normalized === 'running' || normalized === '1') return true;
        if (normalized === 'false' || normalized === 'stopped' || normalized === '0' || normalized === 'idle') return false;
    }
    const stateValue = status.state ?? status.status ?? status.scanstate ?? status.scanState;
    if (typeof stateValue === 'string') {
        const normalized = stateValue.toLowerCase();
        if (normalized === 'running' || normalized === 'active') return true;
        if (normalized === 'stopped' || normalized === 'idle' || normalized === 'complete') return false;
    }
    return false;
}

async function fetchFalconStatus(agentId, iface) {
    if (!agentId || !iface) return null;
    try {
        return await fetchJSON(`${API_BASE}/falcon/${agentId}/scan/status?interface=${encodeURIComponent(iface)}`);
    } catch (err) {
        console.warn('Falcon status request failed', err);
        return null;
    }
}

function markFalconScanActive(agentId, alias) {
    if (!agentId || !alias) return;
    state.falconScanning.add(agentId);
    state.falconScanInterfaces.set(agentId, alias);
    updateFalconButtons(agentId);
}

function markFalconScanInactive(agentId) {
    if (!agentId) return;
    state.falconScanning.delete(agentId);
    state.falconScanInterfaces.delete(agentId);
    updateFalconButtons(agentId);
    if (state.falconPollingAgent === agentId) {
        stopFalconPolling();
    }
}

async function syncFalconScanState(agentId) {
    if (!agentId) return false;
    const aliases = getMonitorAliases(agentId);
    if (!aliases.length) {
        markFalconScanInactive(agentId);
        return false;
    }
    let observedStatus = false;
    for (const alias of aliases) {
        const status = await fetchFalconStatus(agentId, alias);
        if (!status) {
            continue;
        }
        observedStatus = true;
        if (isFalconStatusRunning(status)) {
            markFalconScanActive(agentId, alias);
            return true;
        }
    }
    if (!observedStatus) {
        return state.falconScanning.has(agentId);
    }
    markFalconScanInactive(agentId);
    return false;
}

function getSelectedAgentId() {
    const raw = elements.falconAgentSelect.value || elements.scanAgentSelect.value || `${state.selectedAgentId || ''}`;
    const id = parseInt(raw, 10);
    if (Number.isNaN(id)) {
        throw new Error('Select an agent first');
    }
    return id;
}

function getMonitorInterfaceValue() {
    const select = document.getElementById('falcon-scan-interface');
    if (!select || !select.value) {
        throw new Error('Select a monitor interface first');
    }
    return select.value;
}

function onFalconMonitorStart(event) {
    event.preventDefault();
    try {
        const agentId = getSelectedAgentId();
        const iface = document.getElementById('falcon-monitor-interface').value.trim();
        if (!iface) return alert('Enter a managed interface');
        const monitorMap = state.agentMonitorMap.get(agentId) || {};
        if (monitorMap[iface]) {
            return alert(`${iface} is already in monitor mode. Stop it before starting again.`);
        }
        postJSON(`${API_BASE}/falcon/${agentId}/monitor/start`, { interface: iface })
            .then(resp => {
                logFalcon(`Monitor start ${iface} on ${agentId}: ${JSON.stringify(resp)}`);
                const alias = resp?.interface || resp?.monitorinterface || resp?.monitorInterface || '';
                if (alias) {
                    updateLocalMonitorState(agentId, iface, alias);
                    const monitorSelect = document.getElementById('falcon-scan-interface');
                    if (monitorSelect) monitorSelect.value = alias;
                }
                return loadAgents();
            })
            .catch(err => alert(`Unable to start monitor mode: ${err.message}`));
    } catch (err) {
        alert(err.message);
    }
}

function onFalconMonitorStop() {
    try {
        const agentId = getSelectedAgentId();
        if (state.falconScanning.has(agentId)) {
            alert('Stop the Falcon scan before exiting monitor mode.');
            return;
        }
        const managed = document.getElementById('falcon-monitor-interface').value.trim();
        if (!managed) return alert('Enter interface');
        const monitorMap = state.agentMonitorMap.get(agentId) || {};
        const alias = monitorMap[managed] || document.getElementById('falcon-scan-interface')?.value.trim() || managed;
        postJSON(`${API_BASE}/falcon/${agentId}/monitor/stop`, { interface: alias })
            .then(resp => {
                logFalcon(`Monitor stop ${alias} on ${agentId}: ${JSON.stringify(resp)}`);
                updateLocalMonitorState(agentId, managed, null);
                const monitorSelect = document.getElementById('falcon-scan-interface');
                if (monitorSelect) monitorSelect.value = '';
                return loadAgents();
            })
            .catch(err => alert(`Unable to stop monitor mode: ${err.message}`));
    } catch (err) {
        alert(err.message);
    }
}

function onFalconScanStart(event) {
    event.preventDefault();
    try {
        const agentId = getSelectedAgentId();
        const iface = document.getElementById('falcon-scan-interface').value.trim();
        if (!iface) return alert('Enter monitor interface');
        postJSON(`${API_BASE}/falcon/${agentId}/scan/start`, { interface: iface })
            .then(resp => {
                logFalcon(`Falcon scan start ${iface} on ${agentId}: ${JSON.stringify(resp)}`);
                markFalconScanActive(agentId, iface);
                startFalconPolling(agentId);
                return Promise.all([loadScans(), loadAgents()]);
            })
            .catch(err => alert(`Unable to start Falcon scan: ${err.message}`));
    } catch (err) {
        alert(err.message);
    }
}

function onFalconScanStop() {
    try {
        const agentId = getSelectedAgentId();
        const iface = document.getElementById('falcon-scan-interface').value.trim();
        if (!iface) return alert('Enter monitor interface');
        postJSON(`${API_BASE}/falcon/${agentId}/scan/stop`, { interface: iface })
            .then(resp => {
                logFalcon(`Falcon scan stop ${iface} on ${agentId}: ${JSON.stringify(resp)}`);
                markFalconScanInactive(agentId);
                return Promise.all([loadScans(), loadAgents()]);
            })
            .catch(err => alert(`Unable to stop Falcon scan: ${err.message}`));
    } catch (err) {
        alert(err.message);
    }
}

function onFalconScanStatus() {
    try {
        const agentId = getSelectedAgentId();
        const iface = document.getElementById('falcon-scan-interface').value.trim();
        if (!iface) return alert('Enter monitor interface');
        fetchJSON(`${API_BASE}/falcon/${agentId}/scan/status?interface=${encodeURIComponent(iface)}`)
            .then(resp => logFalcon(`Falcon status ${iface} on ${agentId}: ${JSON.stringify(resp)}`))
            .catch(err => alert(`Unable to fetch status: ${err.message}`));
    } catch (err) {
        alert(err.message);
    }
}

async function triggerFalconDeauth(agentId, apMac, clientMac, channel) {
    try {
        const iface = getMonitorInterfaceValue();
        const payload = {
            interface: iface,
            apmacaddr: apMac,
            stationmacaddr: clientMac || '',
            channel,
            continuous: true,
        };
        const resp = await postJSON(`${API_BASE}/falcon/${agentId}/deauth`, payload);
        logFalcon(`Deauth ${apMac}/${clientMac || 'broadcast'} on ${agentId}: ${JSON.stringify(resp)}`);
    } catch (err) {
        alert(`Unable to start deauth: ${err.message}`);
    }
}

async function triggerFalconCapture(agentId, apMac, ssid, channel, hasClient) {
    try {
        const iface = getMonitorInterfaceValue();
        const payload = {
            interface: iface,
            apmacaddr: apMac,
            ssid,
            channel,
            cracktype: 'wpapsk',
            hasclient: hasClient,
        };
        const resp = await postJSON(`${API_BASE}/falcon/${agentId}/crack`, payload);
        logFalcon(`Capture ${ssid || apMac} on ${agentId}: ${JSON.stringify(resp)}`);
    } catch (err) {
        alert(`Unable to start capture: ${err.message}`);
    }
}

async function stopAllFalconDeauths(agentId) {
    try {
        const iface = getMonitorInterfaceValue();
        const resp = await postJSON(`${API_BASE}/falcon/${agentId}/deauth/stopall`, { interface: iface });
        logFalcon(`Stop all deauths (${iface}) on ${agentId}: ${JSON.stringify(resp)}`);
    } catch (err) {
        alert(`Unable to stop deauths: ${err.message}`);
    }
}

window.addEventListener('load', bootstrap, { once: true });

function updateInterfaceControls() {
    const agentId = state.selectedAgentId;
    const interfaces = state.agentInterfaces.get(agentId) || [];
    const monitorMap = state.agentMonitorMap.get(agentId) || {};
    populateSelect(document.getElementById('scan-interface'), interfaces, 'Select interface');
    const aliasSet = new Set(Object.values(monitorMap).filter(Boolean));
    const managedOptions = Array.from(new Set([
        ...interfaces.filter(name => !aliasSet.has(name)),
        ...Object.keys(monitorMap),
    ]));
    populateSelect(document.getElementById('falcon-monitor-interface'), managedOptions, 'Select managed interface');
    const monitorInterfaces = Object.values(monitorMap).filter(Boolean);
    populateSelect(document.getElementById('falcon-scan-interface'), monitorInterfaces, 'Select monitor interface');
    setFalconInterfaceDefaults(agentId);
}

function populateSelect(select, options, placeholder) {
    if (!select) return;
    const previous = select.value;
    select.innerHTML = '';
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = placeholder || 'Select';
    select.appendChild(defaultOption);
    options.forEach(opt => {
        const option = document.createElement('option');
        option.value = opt;
        option.textContent = opt;
        select.appendChild(option);
    });
    if (options.includes(previous)) {
        select.value = previous;
    } else if (options.length) {
        select.value = options[0];
    } else {
        select.value = '';
    }
}

function setFalconInterfaceDefaults(agentId) {
    const monitorMap = state.agentMonitorMap.get(agentId) || {};
    const alias = Object.values(monitorMap).find(Boolean) || '';
    const managedSelect = document.getElementById('falcon-monitor-interface');
    if (managedSelect) {
        if (alias && managedSelect.querySelector(`option[value="${alias}"]`)) {
            managedSelect.value = alias;
        } else if (!managedSelect.value && managedSelect.options.length > 1) {
            managedSelect.value = managedSelect.options[1].value;
        }
    }
    const monitorSelect = document.getElementById('falcon-scan-interface');
    if (monitorSelect) {
        if (alias && monitorSelect.querySelector(`option[value="${alias}"]`)) {
            monitorSelect.value = alias;
        } else {
            monitorSelect.value = '';
        }
    }
}

function updateFalconButtons(agentId) {
    const stopDisabled = state.falconScanning.has(agentId);
    if (elements.falconMonitorStop) {
        elements.falconMonitorStop.disabled = stopDisabled;
        elements.falconMonitorStop.title = stopDisabled ? 'Stop the Falcon scan before exiting monitor mode.' : '';
    }
}

function toggleSidebar() {
    if (!elements.sidebarToggle) return;
    const willCollapse = !document.body.classList.contains('sidebar-collapsed');
    document.body.classList.toggle('sidebar-collapsed', willCollapse);
    elements.sidebarToggle.textContent = willCollapse ? 'Show Panel' : 'Hide Panel';
    requestMapResize();
}

function toggleTabsOverlay() {
    if (!elements.tabsOverlay) return;
    elements.tabsOverlay.classList.toggle('collapsed');
    const collapsed = elements.tabsOverlay.classList.contains('collapsed');
    document.body.classList.toggle('tabs-collapsed', collapsed);
    elements.tabsCollapse.textContent = collapsed ? 'Expand' : 'Collapse';
    requestMapResize();
}

function requestMapResize() {
    if (!state.map) return;
    setTimeout(() => {
        state.map.invalidateSize();
    }, 150);
}
