// ServDock Advanced Frontend Controller - Full Stack Workstation
let currentServerId = null;
let currentConnectedHost = null;
let isConnected = false;
let autoRefreshTimer = null;
let currentViewingContainerId = null;
let cachedContainers = [];
let currentContainerFilter = 'all';
let currentRemotePath = '/home';

window.addEventListener('pywebviewready', () => {
    console.log("ServDock Backend Bridge Ready");
    initApp();
});

document.addEventListener('DOMContentLoaded', () => {
    if (!window.pywebview) {
        console.warn("PyWebView not detected, fallback browser mode");
        setTimeout(initApp, 350);
    }
});

async function callApi(method, ...args) {
    if (window.pywebview && window.pywebview.api && window.pywebview.api[method]) {
        try {
            return await window.pywebview.api[method](...args);
        } catch (e) {
            console.error(`API Error calling ${method}:`, e);
            if (method === 'get_servers') return [];
            if (method === 'is_connected') return false;
            return { success: false, error: e.message || String(e) };
        }
    } else {
        return mockApiResponse(method, args);
    }
}

async function initApp() {
    await loadServersList();
    await checkInitialConnection();
    startAutoRefresh();
    ensureDockerDownloadsBackgroundPolling();
}

// ----------------- TOAST NOTIFICATIONS -----------------
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast-item toast-${type}`;

    let icon = 'ℹ️';
    if (type === 'success') icon = '✅';
    if (type === 'error') icon = '❌';

    toast.innerHTML = `
        <span class="text-base">${icon}</span>
        <div class="flex-1">${escapeHtml(message)}</div>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 200);
    }, 3000);
}

// ----------------- MODALS -----------------
function openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
}

function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
    if (id === 'modal-docker-downloads') {
        stopDockerDownloadsFastPolling();
    }
}

// ----------------- TABS -----------------
function switchTab(tabName) {
    const tabs = ['dashboard', 'containers', 'files', 'terminal', 'rdp', 'power', 'webserver', 'tailscale', 'servers'];
    tabs.forEach(t => {
        const el = document.getElementById(`tab-${t}`);
        const nav = document.getElementById(`nav-${t}`);
        if (el) {
            if (t === tabName) el.classList.remove('hidden');
            else el.classList.add('hidden');
        }
        if (nav) {
            if (t === tabName) {
                nav.className = "nav-item active";
            } else {
                nav.className = "nav-item";
            }
        }
    });

    if (tabName === 'containers') applyContainersFilter();
    if (tabName === 'servers') loadServersManagerCards();
    if (tabName === 'files') refreshFileManager();
    if (tabName === 'terminal') {
        const input = document.getElementById('term-input');
        if (input) setTimeout(() => input.focus(), 100);
    }
    if (tabName === 'rdp') checkRdpStatus();
    if (tabName === 'tailscale') refreshTailscaleStatus();
    if (tabName === 'webserver') refreshWebServerData();
}

// ----------------- SERVERS & DROPDOWN -----------------
async function loadServersList() {
    const rawServers = await callApi('get_servers');
    const servers = Array.isArray(rawServers) ? rawServers : [];
    const select = document.getElementById('server-select');
    if (!select) return;
    select.innerHTML = '<option value="">-- سروری انتخاب نشده --</option>';

    servers.forEach(s => {
        if (!s || !s.id) return;
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = `${s.name || s.host} (${s.username}@${s.host})`;
        select.appendChild(opt);
    });

    const badgeTotal = document.getElementById('badge-total-servers');
    if (badgeTotal) badgeTotal.textContent = servers.length;

    if (servers.length > 0 && !currentServerId) {
        select.value = servers[0].id;
        currentServerId = servers[0].id;
    }

    updateQuickActionButtonsState();
}

function onServerSelectChanged() {
    const select = document.getElementById('server-select');
    currentServerId = select.value;
    updateQuickActionButtonsState();
    syncPowerTabMac();
}

function updateQuickActionButtonsState() {
    const btnEdit = document.getElementById('btn-quick-edit-server');
    const btnDel = document.getElementById('btn-quick-delete-server');
    const hasSelection = !!currentServerId;
    if (btnEdit) btnEdit.style.opacity = hasSelection ? '1' : '0.4';
    if (btnDel) btnDel.style.opacity = hasSelection ? '1' : '0.4';
}

async function checkInitialConnection() {
    const info = await callApi('get_connection_info');
    if (info && info.connected) {
        currentConnectedHost = info.server ? info.server.host : null;
        updateConnectionState(true, info.server, info.agent);
        fetchDashboardData();
    } else {
        updateConnectionState(false);
    }
}

async function toggleConnectServer() {
    if (isConnected) {
        await callApi('disconnect_server');
        showToast("اتصال از سرور قطع شد", "info");
        updateConnectionState(false);
        return;
    }

    if (!currentServerId) {
        showToast("لطفاً ابتدا یک سرور را انتخاب کنید یا سرور جدید اضافه نمایید", "error");
        return;
    }

    const btn = document.getElementById('btn-connect-toggle');
    const btnText = document.getElementById('btn-connect-text');
    btnText.textContent = "در حال اتصال...";
    btn.disabled = true;

    try {
        const res = await callApi('connect_server', currentServerId);
        if (res && res.success) {
            currentConnectedHost = res.host;
            updateConnectionState(true, { id: currentServerId, name: res.server_name, host: res.host }, res.agent, res.latency_ms);
            showToast(`با موفقیت به ${res.server_name || res.host} متصل شدید`, "success");
            await fetchDashboardData();
            syncPowerTabMac();
        } else {
            showToast(res.message || "خطا در اتصال SSH", "error");
            updateConnectionState(false);
        }
    } catch (e) {
        showToast("خطا در برقراری اتصال: " + e.message, "error");
        updateConnectionState(false);
    } finally {
        btn.disabled = false;
    }
}

function updateConnectionState(connected, server = null, agent = null, latency = null) {
    isConnected = connected;
    const btnText = document.getElementById('btn-connect-text');
    const btn = document.getElementById('btn-connect-toggle');
    const statusText = document.getElementById('sidebar-conn-status');
    const srvName = document.getElementById('sidebar-server-name');
    const agentMini = document.getElementById('agent-mini-status');
    const agentHeaderPill = document.getElementById('agent-header-pill');
    const agentInstallBanner = document.getElementById('agent-install-banner');
    const bannerDisconnected = document.getElementById('banner-disconnected');
    const dashboardContent = document.getElementById('dashboard-active-content');
    const latencyRow = document.getElementById('sidebar-latency-row');
    const latencyVal = document.getElementById('sidebar-latency-val');

    if (connected) {
        btnText.textContent = "قطع اتصال";
        btn.className = "btn btn-danger text-xs px-3.5 py-1.5 shadow-md";
        statusText.innerHTML = '<span class="status-beacon online"></span> متصل';
        statusText.className = "flex items-center gap-1.5 font-medium text-xs text-emerald-400";
        const srvDisplayName = (server && (server.name || server.host)) 
            ? (server.name || server.host) 
            : (currentConnectedHost || 'سرور متصل');
        srvName.textContent = srvDisplayName;
        
        bannerDisconnected.classList.add('hidden');
        dashboardContent.classList.remove('hidden');

        if (latency) {
            latencyRow.classList.remove('hidden');
            latencyVal.textContent = `${latency} ms`;
        }

        if (agent && agent.active) {
            agentMini.textContent = `فعال v${agent.version || '1.0'}`;
            agentMini.className = "text-emerald-400 font-mono";
            agentHeaderPill.className = "flex items-center gap-2 px-3 py-1 rounded-full text-xs border font-medium bg-emerald-500/10 text-emerald-400 border-emerald-500/20";
            agentHeaderPill.innerHTML = `<span class="status-beacon online !w-2 !h-2"></span><span>ایجنت فعال (v${agent.version || '1.0'})</span>`;
            agentInstallBanner.classList.add('hidden');
        } else {
            agentMini.textContent = "مستقیم SSH";
            agentMini.className = "text-amber-400 font-mono";
            agentHeaderPill.className = "flex items-center gap-2 px-3 py-1 rounded-full text-xs border font-medium bg-amber-500/10 text-amber-400 border-amber-500/20";
            agentHeaderPill.innerHTML = `
                <span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
                <span>حالت مستقیم SSH</span>
                <button onclick="promptInstallAgent()" class="text-xs bg-amber-500/20 hover:bg-amber-500/30 px-2 py-0.5 rounded text-amber-200 mr-1 border border-amber-500/30 font-sans">
                  ⚡ نصب ایجنت
                </button>
            `;
            agentInstallBanner.classList.remove('hidden');
        }

        const topBeacon = document.getElementById('top-server-beacon');
        if (topBeacon) topBeacon.style.display = 'inline-block';
    } else {
        btnText.textContent = "اتصال SSH";
        btn.className = "btn btn-primary text-xs px-3.5 py-1.5 shadow-md";
        statusText.innerHTML = '<span class="status-beacon offline"></span> قطع';
        statusText.className = "flex items-center gap-1.5 font-medium text-xs text-red-400";
        srvName.textContent = 'هیچ سروری انتخاب نشده';
        agentMini.textContent = "غیرفعال";
        agentMini.className = "text-slate-500 font-mono";
        agentHeaderPill.classList.add('hidden');
        agentInstallBanner.classList.add('hidden');
        latencyRow.classList.add('hidden');

        bannerDisconnected.classList.remove('hidden');
        dashboardContent.classList.add('hidden');

        const topBeacon = document.getElementById('top-server-beacon');
        if (topBeacon) topBeacon.style.display = 'none';
    }
}

let isFetchingDashboard = false;

// ----------------- DASHBOARD METRICS -----------------
async function fetchDashboardData(manual = false) {
    if (!isConnected || isFetchingDashboard) return;

    isFetchingDashboard = true;
    const refreshIcon = document.getElementById('refresh-icon');
    if (refreshIcon) refreshIcon.classList.add('animate-spin');

    try {
        const data = await callApi('get_dashboard_data');
        if (data && !data.error) {
            renderDashboard(data);
            if (data.roundtrip_ms) {
                const latencyRow = document.getElementById('sidebar-latency-row');
                const latencyVal = document.getElementById('sidebar-latency-val');
                latencyRow.classList.remove('hidden');
                latencyVal.textContent = `${data.roundtrip_ms} ms`;
            }
        }
    } catch (e) {
        console.error("Dashboard error:", e);
    } finally {
        isFetchingDashboard = false;
        if (refreshIcon) {
            setTimeout(() => refreshIcon.classList.remove('animate-spin'), 300);
        }
    }
}

function renderDashboard(data) {
    const sys = data.system || {};
    const dck = data.docker || {};
    const web = data.webservers || {};

    // CPU
    const cpu = sys.cpu || {};
    const cpuPct = Math.round(Number(cpu.usage_percent) || 0);
    const cpuElem = document.getElementById('metric-cpu-percent');
    if (cpuElem) cpuElem.textContent = `${cpuPct}%`;
    const cpuProg = document.getElementById('prog-cpu');
    if (cpuProg) {
        cpuProg.style.width = `${Math.min(100, Math.max(0, cpuPct))}%`;
        if (cpuPct > 80) cpuProg.className = "progress-fill bg-red-500";
        else if (cpuPct > 55) cpuProg.className = "progress-fill bg-amber-500";
        else cpuProg.className = "progress-fill bg-blue-500";
    }
    const cpuCores = document.getElementById('metric-cpu-cores');
    if (cpuCores) cpuCores.textContent = `${cpu.cores || 1} Cores`;
    const cpuModel = document.getElementById('metric-cpu-model');
    if (cpuModel) {
        cpuModel.textContent = cpu.model || 'CPU';
        cpuModel.title = cpu.model || '';
    }

    // RAM
    const mem = sys.memory || {};
    const memPct = Math.round(Number(mem.percent) || 0);
    const memUsedBytes = Number(mem.used_bytes) || 0;
    const memTotalBytes = Number(mem.total_bytes) || 0;
    const memFreeBytes = Number(mem.free_bytes) || 0;
    const memUsedGB = (memUsedBytes / (1024 ** 3)).toFixed(1);
    const memTotalGB = (memTotalBytes / (1024 ** 3)).toFixed(1);
    const memFreeMB = Math.round(memFreeBytes / (1024 ** 2));

    const ramPctElem = document.getElementById('metric-ram-percent');
    if (ramPctElem) ramPctElem.textContent = `${memPct}%`;
    const ramProg = document.getElementById('prog-ram');
    if (ramProg) ramProg.style.width = `${Math.min(100, Math.max(0, memPct))}%`;
    const ramUsedElem = document.getElementById('metric-ram-used-total');
    if (ramUsedElem) ramUsedElem.textContent = `${memUsedGB} / ${memTotalGB} GB`;
    const ramFreeElem = document.getElementById('metric-ram-free');
    if (ramFreeElem) ramFreeElem.textContent = `${memFreeMB} MB آزاد`;

    // Disk
    const disks = Array.isArray(sys.disks) ? sys.disks : [];
    const rootDisk = disks.find(d => d && d.mount === '/') || disks[0] || {};
    const diskPct = Math.round(Number(rootDisk.percent) || 0);
    const diskUsedBytes = Number(rootDisk.used_bytes) || 0;
    const diskTotalBytes = Number(rootDisk.total_bytes) || 0;
    const diskFreeBytes = Number(rootDisk.free_bytes) || 0;
    const diskUsedGB = (diskUsedBytes / (1024 ** 3)).toFixed(1);
    const diskTotalGB = (diskTotalBytes / (1024 ** 3)).toFixed(1);
    const diskFreeGB = (diskFreeBytes / (1024 ** 3)).toFixed(1);

    const diskPctElem = document.getElementById('metric-disk-percent');
    if (diskPctElem) diskPctElem.textContent = `${diskPct}%`;
    const diskProg = document.getElementById('prog-disk');
    if (diskProg) diskProg.style.width = `${Math.min(100, Math.max(0, diskPct))}%`;
    const diskUsedElem = document.getElementById('metric-disk-used-total');
    if (diskUsedElem) diskUsedElem.textContent = `${diskUsedGB} / ${diskTotalGB} GB`;
    const diskFreeElem = document.getElementById('metric-disk-free');
    if (diskFreeElem) diskFreeElem.textContent = `${diskFreeGB} GB باقی‌مانده`;

    // Host
    const host = sys.host || {};
    document.getElementById('metric-host-os').textContent = host.os_name || 'Linux';
    document.getElementById('metric-host-kernel').textContent = `Kernel: ${host.kernel || '-'}`;
    document.getElementById('metric-host-uptime').textContent = formatUptime(host.uptime_seconds || 0);
    const load = host.load_avg || [0, 0, 0];
    document.getElementById('metric-host-load').textContent = `Load: ${load[0]}, ${load[1]}, ${load[2]}`;

    // Docker
    const badgeDocker = document.getElementById('badge-docker-status');
    if (dck.running) {
        badgeDocker.textContent = `فعال (v${dck.server_version || ''})`;
        badgeDocker.className = "text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono";
    } else if (dck.installed) {
        badgeDocker.textContent = "متوقف";
        badgeDocker.className = "text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400 font-mono";
    } else {
        badgeDocker.textContent = "داکر نصب نیست";
        badgeDocker.className = "text-[10px] px-2 py-0.5 rounded-full bg-red-500/20 text-red-400 font-mono";
    }

    document.getElementById('stat-total-containers').textContent = dck.containers_count || (dck.containers ? dck.containers.length : 0);
    document.getElementById('stat-running-containers').textContent = dck.running_count || (dck.containers ? dck.containers.filter(c => c.state === 'running').length : 0);
    document.getElementById('stat-stopped-containers').textContent = dck.stopped_count || 0;
    document.getElementById('stat-images-count').textContent = dck.images_count || 0;
    document.getElementById('badge-running-count').textContent = dck.running_count || 0;

    cachedContainers = dck.containers || [];
    renderQuickContainersTable(cachedContainers);
    applyContainersFilter();
}

function formatUptime(seconds) {
    if (!seconds) return "0m";
    const d = Math.floor(seconds / (3600 * 24));
    const h = Math.floor((seconds % (3600 * 24)) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
}

function formatPortsHtml(portsStr) {
    if (!portsStr) return '<span class="text-slate-500">-</span>';
    const parts = portsStr.split(',').map(p => p.trim());
    return parts.map(part => {
        const match = part.match(/(?:0\.0\.0\.0:|:::)?(\d+)->/);
        if (match && currentConnectedHost) {
            const hostPort = match[1];
            return `<button onclick="openPortInBrowser('${hostPort}')" class="port-link-badge" title="باز کردن در مرورگر (http://${currentConnectedHost}:${hostPort})">🌐 ${escapeHtml(part)}</button>`;
        }
        return `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono text-[10px]">${escapeHtml(part)}</span>`;
    }).join(' ');
}

function openPortInBrowser(port) {
    if (!currentConnectedHost) return;
    const url = `http://${currentConnectedHost}:${port}`;
    callApi('open_external_url', url);
    showToast(`در حال باز کردن ${url} در مرورگر...`, "info");
}

function renderQuickContainersTable(containers) {
    const tbody = document.getElementById('quick-containers-tbody');
    if (!containers || containers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-6 text-center text-slate-500 font-sans">هیچ کانتینری یافت نشد.</td></tr>';
        return;
    }

    const top = containers.slice(0, 6);
    tbody.innerHTML = top.map(c => {
        const isUp = c.state === 'running' || c.status.toLowerCase().includes('up');
        const statusBadge = isUp 
            ? `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><span class="status-beacon online !w-1.5 !h-1.5"></span>${escapeHtml(c.status)}</span>`
            : `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-slate-800 text-slate-400">${escapeHtml(c.status)}</span>`;

        return `
            <tr class="hover:bg-white/[0.02] transition">
                <td class="py-2.5 px-3 font-semibold text-white font-mono">${escapeHtml(c.names || c.id)}</td>
                <td class="py-2.5 px-3 text-slate-300 font-mono text-[11px] truncate max-w-[160px]" title="${escapeHtml(c.image)}">${escapeHtml(c.image)}</td>
                <td class="py-2.5 px-3">${statusBadge}</td>
                <td class="py-2.5 px-3">${formatPortsHtml(c.ports)}</td>
                <td class="py-2.5 px-3 text-center">
                    <div class="inline-flex items-center gap-1">
                        ${isUp ? `
                            <button onclick="handleContainerAction('${c.id}', 'restart')" class="p-1.5 rounded-lg hover:bg-slate-700/80 text-slate-300 transition" title="ریستارت">🔄</button>
                            <button onclick="handleContainerAction('${c.id}', 'stop')" class="p-1.5 rounded-lg hover:bg-red-500/20 text-red-400 transition" title="توقف">⏹</button>
                        ` : `
                            <button onclick="handleContainerAction('${c.id}', 'start')" class="p-1.5 rounded-lg hover:bg-emerald-500/20 text-emerald-400 transition" title="شروع">▶</button>
                        `}
                        <button onclick="viewContainerLogs('${c.id}', '${escapeHtml(c.names || c.id)}')" class="p-1.5 rounded-lg hover:bg-blue-500/20 text-blue-400 transition" title="مشاهده لاگ">📋</button>
                        <button onclick="openEditContainerModal('${c.id}')" class="p-1.5 rounded-lg hover:bg-amber-500/20 text-amber-400 transition" title="ویرایش کانتینر">✏️</button>
                        <button onclick="triggerDeleteContainer('${c.id}', '${escapeHtml(c.names || c.id)}')" class="p-1.5 rounded-lg hover:bg-rose-500/20 text-rose-400 transition" title="حذف کانتینر">🗑️</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

// ----------------- CONTAINERS TAB -----------------
function setContainerFilter(filter) {
    currentContainerFilter = filter;
    ['all', 'running', 'stopped'].forEach(f => {
        const btn = document.getElementById(`filter-btn-${f}`);
        if (btn) {
            if (f === filter) btn.className = "px-3 py-1 rounded-lg font-medium transition bg-blue-600 text-white";
            else btn.className = "px-3 py-1 rounded-lg font-medium transition text-slate-400 hover:text-white";
        }
    });
    applyContainersFilter();
}

function applyContainersFilter() {
    let list = cachedContainers || [];
    if (currentContainerFilter === 'running') {
        list = list.filter(c => c.state === 'running' || c.status.toLowerCase().includes('up'));
    } else if (currentContainerFilter === 'stopped') {
        list = list.filter(c => c.state !== 'running' && !c.status.toLowerCase().includes('up'));
    }

    const q = (document.getElementById('search-containers')?.value || '').toLowerCase().trim();
    if (q) {
        list = list.filter(c => 
            (c.names && c.names.toLowerCase().includes(q)) ||
            (c.image && c.image.toLowerCase().includes(q)) ||
            (c.id && c.id.toLowerCase().includes(q))
        );
    }
    renderAllContainersTable(list);
}

function filterContainersList() {
    applyContainersFilter();
}

function renderAllContainersTable(containers) {
    const tbody = document.getElementById('all-containers-tbody');
    if (!tbody) return;

    if (!containers || containers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-500 font-sans">هیچ کانتینری مطابق با جستجو یا فیلتر یافت نشد.</td></tr>';
        return;
    }

    tbody.innerHTML = containers.map(c => {
        const isUp = c.state === 'running' || c.status.toLowerCase().includes('up');
        const statusBadge = isUp 
            ? `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><span class="status-beacon online !w-1.5 !h-1.5"></span>${escapeHtml(c.status)}</span>`
            : `<span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[10px] font-semibold bg-slate-800 text-slate-400">${escapeHtml(c.status)}</span>`;

        const pol = c.restart_policy || 'unless-stopped';
        const isAuto = pol === 'always' || pol === 'unless-stopped';
        const bootBadge = isAuto
            ? `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400 font-mono font-bold" title="${escapeHtml(pol)}">⚡ ${escapeHtml(pol)}</span>`
            : `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400 font-mono" title="${escapeHtml(pol)}">⛔ ${escapeHtml(pol)}</span>`;

        return `
            <tr class="hover:bg-white/[0.02] transition">
                <td class="py-3 px-3">
                    <div class="font-bold text-white font-mono">${escapeHtml(c.names)}</div>
                    <div class="text-[10px] text-slate-500 font-mono">${escapeHtml(c.id)}</div>
                </td>
                <td class="py-3 px-3 font-mono text-slate-300 text-[11px]">${escapeHtml(c.image)}</td>
                <td class="py-3 px-3">${statusBadge}</td>
                <td class="py-3 px-3">
                    <div class="inline-flex items-center gap-1">
                        ${bootBadge}
                        <button onclick="openDockerStartupModal()" class="text-[11px] text-slate-400 hover:text-amber-400 p-0.5" title="تغییر در مدیریت استارت‌آپ">⚙️</button>
                    </div>
                </td>
                <td class="py-3 px-3">${formatPortsHtml(c.ports)}</td>
                <td class="py-3 px-3 font-mono text-slate-400 text-[11px]">${escapeHtml(c.created || '-')}</td>
                <td class="py-3 px-3 text-center">
                    <div class="inline-flex items-center gap-1.5">
                        ${isUp ? `
                            <button onclick="handleContainerAction('${c.id}', 'restart')" class="btn btn-secondary text-xs px-2.5 py-1" title="ریستارت">🔄 ریستارت</button>
                            <button onclick="handleContainerAction('${c.id}', 'stop')" class="btn btn-danger text-xs px-2.5 py-1" title="توقف">⏹ توقف</button>
                        ` : `
                            <button onclick="handleContainerAction('${c.id}', 'start')" class="btn btn-success text-xs px-2.5 py-1" title="اجرا">▶ اجرا</button>
                        `}
                        <button onclick="viewContainerLogs('${c.id}', '${escapeHtml(c.names || c.id)}')" class="btn btn-outline text-xs px-2.5 py-1" title="مشاهده لاگ">📋 لاگ</button>
                        <button onclick="openEditContainerModal('${c.id}')" class="btn btn-secondary text-xs px-2.5 py-1 text-amber-300 hover:text-white" title="ویرایش پورت‌ها و نام کانتینر">✏️ ویرایش</button>
                        <button onclick="triggerDeleteContainer('${c.id}', '${escapeHtml(c.names || c.id)}')" class="btn btn-outline text-xs px-2.5 py-1 text-rose-400 border-rose-500/30 hover:bg-rose-500/10" title="حذف کانتینر">🗑️ حذف</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

async function handleContainerAction(containerId, action) {
    showToast(`در حال انجام عملیات ${action}...`, "info");
    try {
        const res = await callApi('docker_action', containerId, action);
        if (res && res.success) {
            showToast(`عملیات ${action} با موفقیت انجام شد`, "success");
            await fetchDashboardData(true);
        } else {
            showToast(`خطا در ${action}: ` + (res.stderr || res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function triggerDeleteContainer(containerId, containerName) {
    if (!confirm(`آیا از حذف کامل کانتینر "${containerName}" اطمینان دارید؟\n(در صورت در حال اجرا بودن، کانتینر متوقف و سپس حذف خواهد شد)`)) {
        return;
    }
    showToast(`در حال حذف کانتینر ${containerName}...`, "info");
    try {
        const res = await callApi('docker_action', containerId, 'rm_force');
        if (res && res.success) {
            showToast(`کانتینر "${containerName}" با موفقیت حذف شد`, "success");
            await fetchDashboardData(true);
        } else {
            showToast("خطا در حذف کانتینر: " + (res.stderr || res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

// ==========================================
// DOCKER CONTAINER EDIT & RECREATE
// ==========================================
async function openEditContainerModal(containerId) {
    if (!isConnected) {
        showToast("لطفاً ابتدا به سرور متصل شوید", "error");
        return;
    }

    showToast("در حال دریافت مشخصات کانتینر...", "info");
    try {
        const res = await callApi('docker_get_container_details', containerId);
        if (!res || !res.success || !res.container) {
            showToast("خطا در دریافت مشخصات کانتینر: " + (res ? (res.error || res.stderr) : 'ناشناخته'), "error");
            return;
        }

        const c = res.container;
        const idInput = document.getElementById('edit-container-id');
        const nameInput = document.getElementById('edit-container-name');
        const imgBadge = document.getElementById('edit-container-image-badge');
        const statusBadge = document.getElementById('edit-container-status-badge');
        const envInput = document.getElementById('edit-container-env');
        const restartSelect = document.getElementById('edit-container-restart');

        if (idInput) idInput.value = c.id;
        if (nameInput) nameInput.value = c.name;
        if (imgBadge) imgBadge.textContent = c.image;
        if (statusBadge) {
            const isRunning = c.state === 'running';
            statusBadge.className = isRunning
                ? "text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-bold"
                : "text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-bold";
            statusBadge.textContent = isRunning ? "در حال اجرا" : "متوقف";
        }
        if (envInput) envInput.value = c.env_vars || '';
        if (restartSelect) restartSelect.value = c.restart_policy || 'unless-stopped';

        setEditPortMappings(c.ports || []);
        openModal('modal-edit-container');
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

function addEditPortMappingRow(hostPort = '', containerPort = '', protocol = 'tcp') {
    const container = document.getElementById('edit-port-mappings-list');
    if (!container) return;

    const row = document.createElement('div');
    row.className = "edit-port-mapping-row flex items-center gap-2 bg-slate-800/80 p-2.5 rounded-xl border border-white/5";
    row.innerHTML = `
        <div class="flex-1">
            <label class="block text-[10px] text-slate-400 mb-0.5">پورت دستگاه (Host / Server)</label>
            <input type="number" class="host-port-input w-full bg-slate-900 border border-white/10 rounded-lg px-2.5 py-1.5 text-white font-mono text-xs outline-none focus:border-amber-500" placeholder="مثلاً: 8080" min="1" max="65535" value="${escapeHtml(String(hostPort || ''))}">
        </div>

        <div class="text-slate-500 pt-3 flex flex-col items-center">
            <span class="text-xs">➔</span>
        </div>

        <div class="flex-1">
            <label class="block text-[10px] text-slate-400 mb-0.5">پورت کانتینر (Container)</label>
            <input type="number" class="container-port-input w-full bg-slate-900 border border-white/10 rounded-lg px-2.5 py-1.5 text-white font-mono text-xs outline-none focus:border-amber-500" placeholder="مثلاً: 80" min="1" max="65535" value="${escapeHtml(String(containerPort || ''))}">
        </div>

        <div class="w-20">
            <label class="block text-[10px] text-slate-400 mb-0.5">پروتکل</label>
            <select class="protocol-select w-full bg-slate-900 border border-white/10 rounded-lg px-2 py-1.5 text-slate-300 font-mono text-xs outline-none">
                <option value="tcp" ${(protocol || 'tcp').toLowerCase() === 'tcp' ? 'selected' : ''}>TCP</option>
                <option value="udp" ${(protocol || '').toLowerCase() === 'udp' ? 'selected' : ''}>UDP</option>
            </select>
        </div>

        <button type="button" onclick="removeEditPortMappingRow(this)" class="text-slate-500 hover:text-rose-400 p-1 pt-3.5" title="حذف این پورت">✕</button>
    `;
    container.appendChild(row);
}

function removeEditPortMappingRow(btn) {
    const container = document.getElementById('edit-port-mappings-list');
    if (!container) return;
    const rows = container.querySelectorAll('.edit-port-mapping-row');
    if (rows.length > 1) {
        const row = btn.closest('.edit-port-mapping-row');
        if (row) row.remove();
    } else {
        const row = rows[0];
        const h = row.querySelector('.host-port-input');
        const c = row.querySelector('.container-port-input');
        if (h) h.value = '';
        if (c) c.value = '';
    }
}

function setQuickEditPortMapping(hostPort, containerPort, protocol = 'tcp') {
    const container = document.getElementById('edit-port-mappings-list');
    if (!container) return;
    const rows = container.querySelectorAll('.edit-port-mapping-row');
    if (rows.length === 1) {
        const firstRow = rows[0];
        const hInput = firstRow.querySelector('.host-port-input');
        const cInput = firstRow.querySelector('.container-port-input');
        const pSelect = firstRow.querySelector('.protocol-select');
        if (!hInput.value && !cInput.value) {
            hInput.value = hostPort;
            cInput.value = containerPort;
            if (pSelect) pSelect.value = protocol;
            return;
        }
    }
    addEditPortMappingRow(hostPort, containerPort, protocol);
}

function setEditPortMappings(ports) {
    const container = document.getElementById('edit-port-mappings-list');
    if (!container) return;
    container.innerHTML = '';

    if (!ports || ports.length === 0) {
        addEditPortMappingRow('', '', 'tcp');
        return;
    }

    ports.forEach(p => {
        addEditPortMappingRow(p.host_port || '', p.container_port || '', p.protocol || 'tcp');
    });
}

function collectEditPortMappings() {
    const container = document.getElementById('edit-port-mappings-list');
    if (!container) return '';
    const rows = container.querySelectorAll('.edit-port-mapping-row');
    const portParts = [];

    rows.forEach(row => {
        const hInput = row.querySelector('.host-port-input');
        const cInput = row.querySelector('.container-port-input');
        const pSelect = row.querySelector('.protocol-select');

        const host = hInput ? hInput.value.trim() : '';
        const cont = cInput ? cInput.value.trim() : '';
        const proto = (pSelect ? pSelect.value : 'tcp').toLowerCase();

        if (cont) {
            const protoSuffix = (proto && proto !== 'tcp') ? `/${proto}` : '';
            if (host) {
                portParts.push(`${host}:${cont}${protoSuffix}`);
            } else {
                portParts.push(`${cont}${protoSuffix}`);
            }
        }
    });

    return portParts.join(' ');
}

async function submitEditContainer() {
    const idInput = document.getElementById('edit-container-id');
    const containerId = idInput ? idInput.value.trim() : '';
    if (!containerId) {
        showToast("شناسه کانتینر مشخص نیست", "error");
        return;
    }

    const nameInput = document.getElementById('edit-container-name');
    const newName = nameInput ? nameInput.value.trim() : '';

    const ports = collectEditPortMappings();
    const envInput = document.getElementById('edit-container-env');
    const env = envInput ? envInput.value.trim() : '';

    const restartSelect = document.getElementById('edit-container-restart');
    const restart = restartSelect ? restartSelect.value : 'unless-stopped';

    const btn = document.getElementById('btn-submit-edit-container');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال اعمال تغییرات...";
    }

    try {
        const res = await callApi('docker_edit_container', containerId, newName, ports, env, restart);
        if (res && res.success) {
            showToast(res.message || "کانتینر با موفقیت بروزرسانی شد", "success");
            closeModal('modal-edit-container');
            await fetchDashboardData(true);
        } else {
            showToast("خطا در ویرایش کانتینر: " + (res ? (res.error || res.stderr || 'ناشناخته') : 'خطای ارتباط'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "💾 ذخیره و اعمال تغییرات";
        }
    }
}

// ====================================================================
// ================= DOCKER STARTUP & HUB MANAGEMENT ==================
// ====================================================================

let cachedStartupData = null;
let activeMirrorsList = [];

// ----------------- DOCKER STARTUP -----------------
async function openDockerStartupModal() {
    openModal('modal-docker-startup');
    await refreshDockerStartupStatus();
}

async function refreshDockerStartupStatus() {
    const tbody = document.getElementById('docker-startup-tbody');
    const bootStatusElem = document.getElementById('docker-service-boot-status');
    const toggleBtn = document.getElementById('btn-toggle-docker-service-boot');
    const countElem = document.getElementById('docker-startup-count');
    const totalElem = document.getElementById('docker-startup-total');

    if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-500 font-sans">در حال دریافت وضعیت استارت‌آپ کانتینرها...</td></tr>';

    try {
        const res = await callApi('docker_get_startup_status');
        if (res && res.success) {
            cachedStartupData = res;
            if (bootStatusElem) {
                if (res.service_enabled) {
                    bootStatusElem.textContent = "فعال (Enabled)";
                    bootStatusElem.className = "text-xs font-bold text-emerald-400 font-mono mt-0.5";
                } else {
                    bootStatusElem.textContent = "غیرفعال (Disabled)";
                    bootStatusElem.className = "text-xs font-bold text-rose-400 font-mono mt-0.5";
                }
            }
            if (toggleBtn) {
                toggleBtn.textContent = res.service_enabled ? "غیرفعال‌سازی" : "فعال‌سازی";
                toggleBtn.className = res.service_enabled 
                    ? "btn btn-secondary text-xs px-2.5 py-1 text-rose-400 hover:bg-rose-500/10 font-bold" 
                    : "btn btn-secondary text-xs px-2.5 py-1 text-emerald-400 hover:bg-emerald-500/10 font-bold";
            }
            if (countElem) countElem.textContent = res.autostart_count || 0;
            if (totalElem) totalElem.textContent = res.total_count || 0;

            renderDockerStartupTable(res.containers || []);
        } else {
            if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-rose-400 font-sans">خطا: ${escapeHtml(res ? res.error : 'عدم دریافت اطلاعات')}</td></tr>`;
        }
    } catch (e) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-rose-400 font-sans">خطا: ${escapeHtml(e.message)}</td></tr>`;
    }
}

function renderDockerStartupTable(containers) {
    const tbody = document.getElementById('docker-startup-tbody');
    if (!tbody) return;

    if (!containers || containers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-500 font-sans">هیچ کانتینری یافت نشد.</td></tr>';
        return;
    }

    tbody.innerHTML = containers.map(c => {
        const isUp = c.status.toLowerCase().includes('up') || c.status === 'running';
        const isAuto = c.is_autostart;
        const autoBadge = isAuto
            ? `<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">✅ بله (خودکار)</span>`
            : `<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400">❌ خیر (دستی)</span>`;

        const statusBadge = isUp 
            ? `<span class="text-emerald-400 font-sans text-xs">🟢 در حال اجرا</span>` 
            : `<span class="text-slate-500 font-sans text-xs">⚪ متوقف</span>`;

        return `
            <tr class="hover:bg-white/[0.02] transition">
                <td class="py-3 px-3">
                    <div class="font-bold text-white text-xs">${escapeHtml(c.name)}</div>
                    <div class="text-[10px] text-slate-500">${escapeHtml(c.id)}</div>
                </td>
                <td class="py-3 px-3 text-slate-300 text-xs">${escapeHtml(c.image)}</td>
                <td class="py-3 px-3">${statusBadge}</td>
                <td class="py-3 px-3">${autoBadge}</td>
                <td class="py-3 px-3 text-center">
                    <select onchange="changeContainerRestartPolicy('${c.id}', this.value)" class="bg-[#0e1422] border border-white/10 rounded-lg px-2.5 py-1 text-xs font-mono text-slate-200 outline-none focus:border-amber-400 cursor-pointer">
                        <option value="unless-stopped" ${c.restart_policy === 'unless-stopped' ? 'selected' : ''}>unless-stopped (خودکار)</option>
                        <option value="always" ${c.restart_policy === 'always' ? 'selected' : ''}>always (همیشه)</option>
                        <option value="on-failure" ${c.restart_policy === 'on-failure' ? 'selected' : ''}>on-failure (فقط در صورت خطا)</option>
                        <option value="no" ${c.restart_policy === 'no' ? 'selected' : ''}>no (غیرفعال / دستی)</option>
                    </select>
                </td>
            </tr>
        `;
    }).join('');
}

async function changeContainerRestartPolicy(containerId, policy) {
    showToast(`در حال تنظیم سیاست استارت‌آپ به ${policy}...`, "info");
    try {
        const res = await callApi('docker_set_container_restart_policy', containerId, policy);
        if (res && res.success) {
            showToast("سیاست استارت‌آپ کانتینر با موفقیت بروزرسانی شد", "success");
            await refreshDockerStartupStatus();
            await fetchDashboardData(true);
        } else {
            showToast("خطا در بروزرسانی سیاست: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function toggleDockerServiceBoot() {
    if (!cachedStartupData) return;
    const currentEnabled = cachedStartupData.service_enabled;
    const newTarget = !currentEnabled;
    const actText = newTarget ? "فعال‌سازی در بوت سیستم" : "غیرفعال‌سازی در بوت سیستم";

    if (!confirm(`آیا مطمئن هستید که می‌خواهید سرویس داکر را در بوت سیستم لینوکس ${newTarget ? 'فعال' : 'غیرفعال'} کنید؟`)) {
        return;
    }

    showToast(`در حال ${actText}...`, "info");
    try {
        const res = await callApi('docker_set_service_startup', newTarget);
        if (res && res.success) {
            showToast(res.message || "وضعیت سرویس با موفقیت تغییر یافت", "success");
            await refreshDockerStartupStatus();
        } else {
            showToast("خطا: " + (res.error || 'عملیات ناموفق'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function setAllContainersStartupPolicy(policy) {
    if (!confirm(`آیا از تغییر سیاست استارت‌آپ همه کانتینرها به "${policy}" اطمینان دارید؟`)) {
        return;
    }

    showToast(`در حال اعمال "${policy}" روی تمام کانتینرها...`, "info");
    try {
        const res = await callApi('docker_set_batch_containers_startup', policy);
        if (res && res.success) {
            showToast(res.message || "تمام کانتینرها با موفقیت بروزرسانی شدند", "success");
            await refreshDockerStartupStatus();
            await fetchDashboardData(true);
        } else {
            showToast("خطا: " + (res.error || 'ناموفق'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

// ----------------- DOCKER HUB & REGISTRY MIRRORS -----------------
function openDockerHubModal() {
    openModal('modal-docker-hub');
    switchDockerHubSubTab('search');
    refreshDockerMirrorsList();
}

function switchDockerHubSubTab(tabName) {
    const searchTab = document.getElementById('hub-subtab-search');
    const mirrorsTab = document.getElementById('hub-subtab-mirrors');
    const btnSearch = document.getElementById('hub-tab-btn-search');
    const btnMirrors = document.getElementById('hub-tab-btn-mirrors');

    if (tabName === 'search') {
        if (searchTab) searchTab.classList.remove('hidden');
        if (mirrorsTab) mirrorsTab.classList.add('hidden');
        if (btnSearch) { btnSearch.className = "px-4 py-1.5 rounded-xl font-bold bg-blue-600 text-white transition"; }
        if (btnMirrors) { btnMirrors.className = "px-4 py-1.5 rounded-xl font-medium text-slate-400 hover:text-white transition"; }
    } else {
        if (searchTab) searchTab.classList.add('hidden');
        if (mirrorsTab) mirrorsTab.classList.remove('hidden');
        if (btnSearch) { btnSearch.className = "px-4 py-1.5 rounded-xl font-medium text-slate-400 hover:text-white transition"; }
        if (btnMirrors) { btnMirrors.className = "px-4 py-1.5 rounded-xl font-bold bg-blue-600 text-white transition"; }
        refreshDockerMirrorsList();
    }
}

async function executeDockerHubSearch() {
    const input = document.getElementById('docker-hub-search-input');
    const term = input ? input.value.trim() : '';
    const tbody = document.getElementById('docker-hub-results-tbody');
    const meta = document.getElementById('hub-search-meta');
    const btn = document.getElementById('btn-docker-hub-search');

    if (!term) {
        showToast("لطفاً عبارتی برای جستجو وارد کنید", "error");
        return;
    }

    if (btn) { btn.disabled = true; btn.textContent = "⏳ در حال جستجو..."; }
    if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-500 font-sans">در حال جستجو در مخزن Docker Hub...</td></tr>';
    if (meta) meta.textContent = '';

    try {
        const res = await callApi('docker_search_hub', term, 25);
        if (res && res.success) {
            if (meta) meta.textContent = `${res.count || 0} نتیجه برای "${term}"`;
            renderDockerHubResults(res.results || []);
        } else {
            if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-rose-400 font-sans">خطا در جستجو: ${escapeHtml(res ? res.error : 'خطای ارتباط')}</td></tr>`;
        }
    } catch (e) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-rose-400 font-sans">خطا: ${escapeHtml(e.message)}</td></tr>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "جستجو در هاب"; }
    }
}

function renderDockerHubResults(results) {
    const tbody = document.getElementById('docker-hub-results-tbody');
    if (!tbody) return;

    if (!results || results.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-500 font-sans">هیچ نتیجه‌ای یافت نشد.</td></tr>';
        return;
    }

    tbody.innerHTML = results.map(r => {
        return `
            <tr class="hover:bg-white/[0.02] transition">
                <td class="py-2.5 px-3 font-mono font-bold text-white text-xs">
                    ${escapeHtml(r.name)}
                </td>
                <td class="py-2.5 px-3 font-mono text-amber-400 text-xs">
                    ⭐ ${escapeHtml(r.stars)}
                </td>
                <td class="py-2.5 px-3">
                    ${r.is_official ? '<span class="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono text-[10px] font-bold">Official [OK]</span>' : '<span class="text-slate-500 text-[10px] font-mono">Community</span>'}
                </td>
                <td class="py-2.5 px-3 text-slate-300 text-xs line-clamp-2" title="${escapeHtml(r.description)}">
                    ${escapeHtml(r.description || '-')}
                </td>
                <td class="py-2.5 px-3 text-center">
                    <button onclick="quickPullImage('${escapeHtml(r.name)}')" class="btn btn-secondary text-xs px-3 py-1 text-sky-400 hover:text-white font-bold">
                        ⬇️ Pull
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

async function quickPullImage(imageName) {
    if (!imageName) return;
    await enqueueDockerPull(imageName);
}

function quickRunPreset(imageName, defaultName, portMapping, envVars = '') {
    closeModal('modal-docker-hub');
    openRunContainerModal();
    const imgInput = document.getElementById('run-container-image');
    const nameInput = document.getElementById('run-container-name');
    const envInput = document.getElementById('run-container-env');
    const restartSelect = document.getElementById('run-container-restart');
    const replaceCheck = document.getElementById('run-container-replace-existing');

    if (imgInput) imgInput.value = imageName;
    if (nameInput) nameInput.value = `${defaultName}-${Math.floor(Math.random() * 900 + 100)}`;
    if (envInput) envInput.value = envVars;
    if (restartSelect) restartSelect.value = 'unless-stopped';
    if (replaceCheck) replaceCheck.checked = true;
    if (portMapping) {
        setPortMappingsFromStr(portMapping);
    }
}

// ----------------- DOCKER DOWNLOAD MANAGER & QUEUE -----------------
let dockerDownloadsPollTimer = null;
let dockerDownloadsSlowTimer = null;
let lastKnownDownloadingId = null;

function openDockerDownloadsModal() {
    openModal('modal-docker-downloads');
    refreshDockerDownloads();
    startDockerDownloadsFastPolling();
}

function startDockerDownloadsFastPolling() {
    stopDockerDownloadsFastPolling();
    dockerDownloadsPollTimer = setInterval(refreshDockerDownloads, 1200);
}

function stopDockerDownloadsFastPolling() {
    if (dockerDownloadsPollTimer) {
        clearInterval(dockerDownloadsPollTimer);
        dockerDownloadsPollTimer = null;
    }
}

function ensureDockerDownloadsBackgroundPolling() {
    if (!dockerDownloadsSlowTimer) {
        dockerDownloadsSlowTimer = setInterval(async () => {
            const modal = document.getElementById('modal-docker-downloads');
            if (modal && modal.classList.contains('active')) return;
            try {
                const res = await callApi('docker_get_download_queue');
                if (res && res.success) {
                    updateDockerDownloadsBadge(res.queued_count || 0, res.downloading_count || 0);
                    if (lastKnownDownloadingId && !res.active_task) {
                        lastKnownDownloadingId = null;
                        showToast("یک یا چند ایمیج داکر با موفقیت دانلود و ذخیره شدند!", "success");
                        refreshDockerImagesList();
                        fetchDashboardData(true);
                    } else if (res.active_task) {
                        lastKnownDownloadingId = res.active_task.id;
                    }
                }
            } catch (e) {
                // Ignore silent background poll error
            }
        }, 4000);
    }
}

function updateDockerDownloadsBadge(queuedCount, downloadingCount) {
    const badge = document.getElementById('badge-docker-downloads-count');
    if (!badge) return;
    const totalActive = (queuedCount || 0) + (downloadingCount || 0);
    if (totalActive > 0) {
        badge.classList.remove('hidden');
        badge.textContent = totalActive;
        if (downloadingCount > 0) {
            badge.className = "px-1.5 py-0.5 text-[10px] rounded-full bg-emerald-500 text-slate-900 font-bold font-mono animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.7)]";
        } else {
            badge.className = "px-1.5 py-0.5 text-[10px] rounded-full bg-amber-500 text-slate-900 font-bold font-mono";
        }
    } else {
        badge.classList.add('hidden');
    }
}

function fillQuickDownloadImage(imageName) {
    const input = document.getElementById('input-new-download-image');
    if (input) {
        input.value = imageName;
        input.focus();
    }
}

async function addCustomImageToDownloadQueue() {
    const input = document.getElementById('input-new-download-image');
    const imageName = input ? input.value.trim() : '';
    if (!imageName) {
        showToast("لطفاً نام یا تگ ایمیج داکر را وارد کنید", "error");
        return;
    }
    await enqueueDockerPull(imageName);
    if (input) input.value = '';
}

async function enqueueDockerPull(imageName) {
    if (!imageName) return;
    try {
        const res = await callApi('docker_queue_pull', imageName);
        if (res && res.success) {
            showToast(res.message || `ایمیج ${imageName} به صف دانلود لینوکس اضافه شد`, "info");
            openDockerDownloadsModal();
            refreshDockerDownloads();
        } else {
            showToast("خطا در افزودن به صف دانلود: " + (res.error || 'خطای نامشخص'), "error");
        }
    } catch (e) {
        showToast("خطا در ارتباط: " + e.message, "error");
    }
}

async function cancelDockerDownload(taskId) {
    if (!confirm("آیا از متوقف کردن و لغو این فرآیند دانلود اطمینان دارید؟")) return;
    try {
        const res = await callApi('docker_cancel_download', taskId);
        if (res && res.success) {
            showToast(res.message || "دانلود لغو گردید", "info");
            refreshDockerDownloads();
        } else {
            showToast("خطا در لغو دانلود: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function retryDockerDownload(taskId) {
    try {
        const res = await callApi('docker_retry_download', taskId);
        if (res && res.success) {
            showToast(res.message || "مجدداً در صف دانلود قرار گرفت", "info");
            refreshDockerDownloads();
        } else {
            showToast("خطا در شروع مجدد: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function removeDockerDownload(taskId) {
    try {
        const res = await callApi('docker_remove_download', taskId);
        if (res && res.success) {
            refreshDockerDownloads();
        } else {
            showToast("خطا در حذف: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function clearCompletedDockerDownloads() {
    try {
        const res = await callApi('docker_clear_completed_downloads');
        if (res && res.success) {
            showToast(res.message || "موارد تکمیل شده پاکسازی شدند", "info");
            refreshDockerDownloads();
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function refreshDockerDownloads() {
    try {
        const res = await callApi('docker_get_download_queue');
        if (!res || !res.success) return;

        const dlEl = document.getElementById('dl-stat-downloading');
        const qEl = document.getElementById('dl-stat-queued');
        const cEl = document.getElementById('dl-stat-completed');
        const fEl = document.getElementById('dl-stat-failed');
        const metaEl = document.getElementById('dl-queue-total-meta');

        if (dlEl) dlEl.textContent = res.downloading_count || 0;
        if (qEl) qEl.textContent = res.queued_count || 0;
        if (cEl) cEl.textContent = res.completed_count || 0;
        if (fEl) fEl.textContent = res.failed_count || 0;
        if (metaEl) metaEl.textContent = `${res.total_count || 0} مورد در صف و سابقه`;

        updateDockerDownloadsBadge(res.queued_count || 0, res.downloading_count || 0);

        if (lastKnownDownloadingId && !res.active_task) {
            lastKnownDownloadingId = null;
            refreshDockerImagesList();
            fetchDashboardData(true);
        } else if (res.active_task) {
            lastKnownDownloadingId = res.active_task.id;
        }

        renderActiveDownloadCard(res.active_task);
        renderDownloadsQueueTable(res.tasks || [], res.active_task);
    } catch (e) {
        console.error("Error refreshing docker downloads:", e);
    }
}

function renderActiveDownloadCard(activeTask) {
    const container = document.getElementById('dl-active-container');
    if (!container) return;

    if (!activeTask) {
        container.innerHTML = '';
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    const pct = Math.max(0, Math.min(100, activeTask.progress || 0));
    const layersTotal = activeTask.total_layers || 0;
    const layersDone = activeTask.completed_layers || 0;
    const sizeInfo = activeTask.size_info ? `(${escapeHtml(activeTask.size_info)})` : '';
    const logsHtml = (activeTask.logs && activeTask.logs.length > 0)
        ? activeTask.logs.map(l => `<div>${escapeHtml(l)}</div>`).join('')
        : '<div class="text-slate-500">در حال دریافت اولین بسته‌های خروجی داکر...</div>';

    container.innerHTML = `
        <div class="glass-panel p-4 bg-gradient-to-br from-slate-900/90 via-emerald-950/20 to-slate-900/90 border border-emerald-500/30 rounded-2xl shadow-[0_0_20px_rgba(16,185,129,0.15)] flex flex-col space-y-3">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-2.5">
                    <span class="relative flex h-3 w-3">
                        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                        <span class="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                    </span>
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-mono font-bold text-white text-sm">${escapeHtml(activeTask.image)}</span>
                            <span class="text-[10px] px-2 py-0.5 rounded-full bg-sky-500/20 text-sky-400 font-medium">دانلود مستقیم روی سرور لینوکس</span>
                        </div>
                        <p class="text-xs text-emerald-300 font-medium mt-0.5">${escapeHtml(activeTask.current_step || 'در حال انجام...')}</p>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="cancelDockerDownload('${escapeHtml(activeTask.id)}')" class="btn btn-outline text-xs px-3 py-1.5 border-rose-500/30 text-rose-400 hover:bg-rose-500/10">
                        🛑 لغو دانلود
                    </button>
                </div>
            </div>

            <!-- Progress Bar -->
            <div>
                <div class="flex items-center justify-between text-xs mb-1.5 font-mono">
                    <span class="text-slate-300 font-medium font-sans">
                        ${layersTotal > 0 ? `لایه‌ها: ${layersDone} از ${layersTotal} لایه تکمیل شد` : 'دریافت مانیفست لایه‌ها'}
                        <span class="text-slate-400 text-[11px] font-mono mr-1">${sizeInfo}</span>
                    </span>
                    <span class="text-emerald-400 font-bold text-sm">${pct}%</span>
                </div>
                <div class="w-full bg-slate-950/80 rounded-full h-3.5 overflow-hidden p-0.5 border border-white/10 shadow-inner">
                    <div class="bg-gradient-to-r from-emerald-500 via-teal-400 to-sky-400 h-full rounded-full transition-all duration-300 shadow-[0_0_12px_rgba(16,185,129,0.6)]" style="width: ${pct}%;"></div>
                </div>
            </div>

            <!-- Live Terminal Output Preview -->
            <details class="text-xs group" open>
                <summary class="cursor-pointer text-slate-400 hover:text-white select-none text-[11px] font-mono flex items-center justify-between py-1">
                    <span>📺 لاگ زنده دریافت لایه‌ها (docker pull output):</span>
                    <span class="group-open:rotate-180 transition">▼</span>
                </summary>
                <div id="active-dl-logs-box" class="mt-1 bg-black/80 rounded-xl p-3 border border-white/10 font-mono text-[11px] text-emerald-400/90 max-h-36 overflow-y-auto leading-relaxed space-y-0.5">
                    ${logsHtml}
                </div>
            </details>
        </div>
    `;

    const logsBox = document.getElementById('active-dl-logs-box');
    if (logsBox) logsBox.scrollTop = logsBox.scrollHeight;
}

function renderDownloadsQueueTable(tasks, activeTask) {
    const tbody = document.getElementById('docker-downloads-tbody');
    if (!tbody) return;

    if (!tasks || tasks.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-500 font-sans">هیچ دانلودی در صف یا تاریخچه وجود ندارد.</td></tr>';
        return;
    }

    tbody.innerHTML = tasks.slice().reverse().map(t => {
        let statusBadge = '';
        let actionButtons = '';

        if (t.status === 'downloading') {
            statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] bg-sky-500/20 text-sky-400 font-bold font-sans">⚡ در حال دانلود</span>';
            actionButtons = `<button onclick="cancelDockerDownload('${escapeHtml(t.id)}')" class="btn btn-outline text-[10px] px-2 py-1 text-rose-400 border-rose-500/30 hover:bg-rose-500/10" title="لغو دانلود">لغو</button>`;
        } else if (t.status === 'queued') {
            statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] bg-amber-500/20 text-amber-300 font-bold font-sans">⏳ در صف انتظار</span>';
            actionButtons = `<button onclick="cancelDockerDownload('${escapeHtml(t.id)}')" class="btn btn-outline text-[10px] px-2 py-1 text-rose-400 border-rose-500/30 hover:bg-rose-500/10" title="لغو از صف">لغو</button>`;
        } else if (t.status === 'completed') {
            statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] bg-emerald-500/20 text-emerald-400 font-bold font-sans">✅ تکمیل شده</span>';
            actionButtons = `<button onclick="removeDockerDownload('${escapeHtml(t.id)}')" class="text-slate-400 hover:text-rose-400 text-xs px-2 py-1" title="حذف از تاریخچه">🗑️</button>`;
        } else if (t.status === 'cancelled') {
            statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] bg-slate-700/60 text-slate-300 font-sans">🛑 لغو شده</span>';
            actionButtons = `
                <button onclick="retryDockerDownload('${escapeHtml(t.id)}')" class="btn btn-secondary text-[10px] px-2 py-1 text-sky-400 hover:text-white" title="تلاش مجدد">🔄 مجدد</button>
                <button onclick="removeDockerDownload('${escapeHtml(t.id)}')" class="text-slate-400 hover:text-rose-400 text-xs px-1.5 py-1" title="حذف">🗑️</button>
            `;
        } else {
            statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] bg-rose-500/20 text-rose-400 font-bold font-sans">❌ ناموفق</span>';
            actionButtons = `
                <button onclick="retryDockerDownload('${escapeHtml(t.id)}')" class="btn btn-secondary text-[10px] px-2 py-1 text-sky-400 hover:text-white" title="تلاش مجدد">🔄 مجدد</button>
                <button onclick="removeDockerDownload('${escapeHtml(t.id)}')" class="text-slate-400 hover:text-rose-400 text-xs px-1.5 py-1" title="حذف">🗑️</button>
            `;
        }

        const pct = Math.max(0, Math.min(100, t.progress || 0));
        const progressHtml = t.status === 'downloading'
            ? `<div class="flex items-center gap-1.5"><div class="w-16 bg-slate-800 rounded-full h-1.5 overflow-hidden"><div class="bg-emerald-500 h-full" style="width: ${pct}%"></div></div><span class="text-emerald-400 font-bold text-[11px]">${pct}%</span></div>`
            : (t.status === 'completed' ? '<span class="text-emerald-400 font-bold">100%</span>' : `<span class="text-slate-500">${pct}%</span>`);

        return `
            <tr class="hover:bg-white/[0.02] transition">
                <td class="py-2.5 px-3">
                    <div class="font-bold text-white text-xs">${escapeHtml(t.image)}</div>
                    ${t.error ? `<div class="text-[10px] text-rose-400/90 truncate max-w-xs font-sans mt-0.5" title="${escapeHtml(t.error)}">${escapeHtml(t.error)}</div>` : ''}
                </td>
                <td class="py-2.5 px-3 text-[11px] text-slate-400">
                    <div>${escapeHtml(t.created_at || '-')}</div>
                    ${t.completed_at ? `<div class="text-[10px] text-slate-500">پایان: ${escapeHtml(t.completed_at)}</div>` : ''}
                </td>
                <td class="py-2.5 px-3">
                    ${statusBadge}
                </td>
                <td class="py-2.5 px-3">
                    ${progressHtml}
                </td>
                <td class="py-2.5 px-3 text-center">
                    <div class="flex items-center justify-center gap-1">
                        ${actionButtons}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}


// ----------------- REGISTRY MIRRORS MANAGEMENT -----------------
async function refreshDockerMirrorsList() {
    const listContainer = document.getElementById('docker-mirrors-list');
    if (!listContainer) return;

    try {
        const res = await callApi('docker_get_registry_mirrors');
        if (res && res.success) {
            activeMirrorsList = res.mirrors || [];
            renderDockerMirrorsList(activeMirrorsList, res.presets || []);
        } else {
            listContainer.innerHTML = `<div class="text-rose-400 text-xs">خطا در دریافت لیست میرورها: ${escapeHtml(res ? res.error : '')}</div>`;
        }
    } catch (e) {
        listContainer.innerHTML = `<div class="text-rose-400 text-xs">خطا: ${escapeHtml(e.message)}</div>`;
    }
}

function renderDockerMirrorsList(activeMirrors, presets) {
    const listContainer = document.getElementById('docker-mirrors-list');
    if (!listContainer) return;

    const allItems = [...presets];
    activeMirrors.forEach(m => {
        if (!allItems.some(p => p.url === m)) {
            allItems.push({
                id: 'custom-' + Math.random().toString(36).substring(7),
                name: 'میرور سفارشی',
                url: m,
                country: 'Custom',
                desc: 'میرور تنظیم شده توسط کاربر'
            });
        }
    });

    listContainer.innerHTML = allItems.map(item => {
        const isActive = activeMirrors.includes(item.url);
        const b64 = btoa(unescape(encodeURIComponent(item.url))).replace(/=/g, '');
        return `
            <div class="glass-panel p-3 bg-slate-800/40 border-white/5 flex items-center justify-between gap-3 hover:border-white/10 transition">
                <div class="flex items-start gap-3 flex-1">
                    <input type="checkbox" onchange="toggleMirrorSelection('${escapeHtml(item.url)}', this.checked)" ${isActive ? 'checked' : ''} class="w-4 h-4 rounded mt-0.5 text-blue-600 focus:ring-0 cursor-pointer">
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-bold text-white text-xs">${escapeHtml(item.name)}</span>
                            <span class="text-[9px] px-1.5 py-0.2 rounded font-mono ${item.country === 'IR' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-blue-500/20 text-blue-400'}">${escapeHtml(item.country)}</span>
                            ${isActive ? '<span class="text-[10px] text-emerald-400 font-mono font-bold">● فعال</span>' : ''}
                        </div>
                        <div class="font-mono text-slate-400 text-[11px] mt-0.5">${escapeHtml(item.url)}</div>
                        <div class="text-slate-500 text-[10px] mt-0.5">${escapeHtml(item.desc || '')}</div>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <span id="latency-${b64}" class="text-[10px] font-mono text-slate-400"></span>
                    <button onclick="testMirrorLatency('${escapeHtml(item.url)}', this)" class="btn btn-outline text-[11px] px-2.5 py-1 text-sky-400 hover:text-white font-mono">
                        ⚡ تست پینگ
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function toggleMirrorSelection(url, isChecked) {
    if (isChecked) {
        if (!activeMirrorsList.includes(url)) activeMirrorsList.push(url);
    } else {
        activeMirrorsList = activeMirrorsList.filter(m => m !== url);
    }
}

async function testMirrorLatency(url, btnElement) {
    const b64 = btoa(unescape(encodeURIComponent(url))).replace(/=/g, '');
    const span = document.getElementById(`latency-${b64}`);
    if (span) span.textContent = '⏳ در حال تست...';
    if (btnElement) btnElement.disabled = true;

    try {
        const res = await callApi('docker_test_registry_mirror', url);
        if (res && res.success && res.reachable) {
            const ms = res.latency_ms;
            let color = 'text-emerald-400';
            if (ms > 300) color = 'text-amber-400';
            if (ms > 800) color = 'text-rose-400';
            if (span) span.innerHTML = `<span class="${color} font-bold">${ms}ms (${res.status_code})</span>`;
        } else {
            if (span) span.innerHTML = `<span class="text-rose-400">ناموفق (${res ? res.status_code || 'Err' : 'Err'})</span>`;
        }
    } catch (e) {
        if (span) span.innerHTML = `<span class="text-rose-400">خطا</span>`;
    } finally {
        if (btnElement) btnElement.disabled = false;
    }
}

async function testAllMirrorsLatency() {
    const buttons = document.querySelectorAll('#docker-mirrors-list button');
    buttons.forEach(b => b.click());
}

function addCustomMirrorRow() {
    const input = document.getElementById('custom-mirror-url');
    let url = input ? input.value.trim() : '';
    if (!url) {
        showToast("لطفاً آدرس میرور را وارد کنید", "error");
        return;
    }
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        url = 'https://' + url;
    }
    url = url.replace(/\/+$/, '');

    if (!activeMirrorsList.includes(url)) {
        activeMirrorsList.push(url);
        if (input) input.value = '';
        showToast(`میرور ${url} افزوده شد. برای اعمال، دکمه «ذخیره و اعمال میرورها» را بزنید.`, "info");
        refreshDockerMirrorsList();
    } else {
        showToast("این میرور در لیست وجود دارد", "warning");
    }
}

async function saveRegistryMirrorsConfig() {
    const btn = document.getElementById('btn-save-mirrors');
    if (btn) { btn.disabled = true; btn.textContent = "⏳ در حال اعمال و ری‌استارت داکر..."; }

    showToast("در حال بروزرسانی /etc/docker/daemon.json و ری‌استارت داکر...", "info");
    try {
        const res = await callApi('docker_set_registry_mirrors', activeMirrorsList);
        if (res && res.success) {
            showToast("میرورهای داکر با موفقیت ذخیره و داکر بازنشانی شد!", "success");
            await refreshDockerMirrorsList();
            await fetchDashboardData(true);
        } else {
            showToast("خطا در اعمال میرورها: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "💾 ذخیره و اعمال میرورها"; }
    }
}

// ----------------- REMOTE FILE MANAGER (TAB 4) -----------------
async function refreshFileManager() {
    if (!isConnected) return;
    await loadRemoteDirectory(currentRemotePath);
}

async function loadRemoteDirectory(path) {
    if (!isConnected) {
        showToast("ابتدا به سرور متصل شوید", "error");
        return;
    }

    const tbody = document.getElementById('files-list-tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-500 font-sans">در حال دریافت فایل‌ها...</td></tr>';

    try {
        const res = await callApi('file_list', path);
        if (res && res.success) {
            currentRemotePath = res.current_path;
            document.getElementById('file-current-path').value = currentRemotePath;
            renderFilesTable(res.items);
        } else {
            tbody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-red-400 font-sans">خطا در خواندن پوشه: ${res.error || 'ناشناخته'}</td></tr>`;
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-red-400 font-sans">خطا: ${e.message}</td></tr>`;
    }
}

function renderFilesTable(items) {
    const tbody = document.getElementById('files-list-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-500 font-sans">این پوشه خالی است.</td></tr>';
        return;
    }

    tbody.innerHTML = items.map(item => {
        const isDir = item.is_dir;
        const isDockerArchive = !isDir && (/\.(iso|tar|tar\.gz|tgz|img|tar\.xz)$/i.test(item.name));
        let icon = '📄';
        if (isDir) icon = '📁';
        else if (/\.iso$/i.test(item.name)) icon = '💿';
        else if (/\.(tar|tar\.gz|tgz)$/i.test(item.name)) icon = '📦';
        else if (item.name.endsWith('.py')) icon = '🐍';
        else if (item.name.endsWith('.sh')) icon = '⚙️';

        const itemFullPath = currentRemotePath.replace(/\/+$/, '') + '/' + item.name;

        return `
            <tr class="hover:bg-white/[0.02] transition">
                <td class="py-2.5 px-3">
                    <div class="flex items-center gap-2">
                        <span class="text-base">${icon}</span>
                        ${isDir ? `
                            <button onclick="loadRemoteDirectory('${escapeHtml(itemFullPath)}')" class="font-bold text-white hover:text-blue-400 transition font-mono text-xs text-right">
                                ${escapeHtml(item.name)}/
                            </button>
                        ` : `
                            <span class="font-mono ${isDockerArchive ? 'text-blue-300 font-bold' : 'text-slate-200'} text-xs">${escapeHtml(item.name)}</span>
                            ${isDockerArchive ? '<span class="text-[9px] bg-blue-500/20 text-blue-300 px-1.5 py-0.5 rounded font-mono border border-blue-500/30">Docker Image</span>' : ''}
                        `}
                    </div>
                </td>
                <td class="py-2.5 px-3 font-mono text-slate-400 text-xs">${item.size_formatted}</td>
                <td class="py-2.5 px-3 font-mono text-slate-400 text-xs">${item.mtime_formatted}</td>
                <td class="py-2.5 px-3 font-mono text-slate-500 text-[11px]">${item.permissions}</td>
                <td class="py-2.5 px-3 text-center">
                    <div class="inline-flex items-center gap-1.5">
                        ${!isDir ? `
                            ${isDockerArchive ? `
                                <button onclick="loadDockerImageFromFile('${escapeHtml(itemFullPath)}')" class="btn btn-primary text-xs px-2.5 py-1 bg-blue-600 hover:bg-blue-500 font-bold" title="بارگذاری مستقیم این فایل در داکر (docker load / import)">🐳 لود در داکر</button>
                            ` : `
                                <button onclick="loadDockerImageFromFile('${escapeHtml(itemFullPath)}')" class="btn btn-outline text-xs px-2 py-1 text-slate-400 hover:text-blue-400" title="تلاش برای لود در داکر">🐳</button>
                            `}
                            <button onclick="openRemoteFileForEdit('${escapeHtml(itemFullPath)}')" class="btn btn-secondary text-xs px-2.5 py-1" title="ویرایش متن">✏️ ویرایش</button>
                            <button onclick="downloadRemoteFile('${escapeHtml(itemFullPath)}')" class="btn btn-outline text-xs px-2.5 py-1 text-emerald-400 hover:text-emerald-300" title="دانلود به کامپیوتر">⬇️ دانلود</button>
                        ` : ''}
                        <button onclick="triggerDeleteRemoteItem('${escapeHtml(itemFullPath)}', ${isDir})" class="btn btn-danger text-xs px-2 py-1" title="حذف">🗑️</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function navigateFileUp() {
    if (!currentRemotePath || currentRemotePath === '/' || currentRemotePath === '.') return;
    const parts = currentRemotePath.replace(/\/+$/, '').split('/');
    parts.pop();
    const parent = parts.join('/') || '/';
    loadRemoteDirectory(parent);
}

function navigateFileGo() {
    const p = document.getElementById('file-current-path').value.trim();
    if (p) loadRemoteDirectory(p);
}

function handleFilePathKey(e) {
    if (e.key === 'Enter') navigateFileGo();
}

async function triggerUploadFileInteractive() {
    if (!isConnected) return;
    showToast("در حال انتخاب فایل از کامپیوتر شما...", "info");
    try {
        const res = await callApi('file_upload_interactive', currentRemotePath);
        if (res && res.success) {
            showToast(res.message || "فایل با موفقیت آپلود شد", "success");
            await refreshFileManager();
        } else if (res && res.cancelled) {
            // Cancelled dialog
        } else {
            showToast("خطا در آپلود فایل: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا در آپلود: " + e.message, "error");
    }
}

async function downloadRemoteFile(remotePath) {
    if (!isConnected) return;
    showToast("مسیر ذخیره فایل روی کامپیوتر را انتخاب کنید...", "info");
    try {
        const res = await callApi('file_download_interactive', remotePath);
        if (res && res.success) {
            showToast(res.message || "فایل با موفقیت دانلود و ذخیره شد", "success");
        } else if (res && res.cancelled) {
            // Cancelled dialog
        } else {
            showToast("خطا در دانلود: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function openRemoteFileForEdit(remotePath) {
    showToast("در حال باز کردن فایل...", "info");
    try {
        const res = await callApi('file_read', remotePath);
        if (res && res.success) {
            document.getElementById('file-editor-title').textContent = `ویرایش: ${remotePath}`;
            document.getElementById('file-editor-subtitle').textContent = `حجم: ${res.size} بایت`;
            document.getElementById('file-editor-current-path').value = remotePath;
            document.getElementById('file-editor-content').value = res.content || "";
            openModal('modal-file-editor');
        } else {
            showToast("خطا در باز کردن فایل: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function saveCurrentEditingFile() {
    const path = document.getElementById('file-editor-current-path').value;
    const content = document.getElementById('file-editor-content').value;
    const btn = document.getElementById('btn-save-file');

    btn.disabled = true;
    btn.textContent = "در حال ذخیره...";

    try {
        const res = await callApi('file_save', path, content);
        if (res && res.success) {
            showToast("تغییرات فایل ذخیره شد", "success");
            closeModal('modal-file-editor');
            await refreshFileManager();
        } else {
            showToast("خطا در ذخیره فایل: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "💾 ذخیره فایل (Save)";
    }
}

async function promptCreateNewItem(type) {
    const name = prompt(type === 'folder' ? "نام پوشه جدید را وارد کنید:" : "نام فایل جدید را وارد کنید:");
    if (!name || !name.trim()) return;

    const fullPath = currentRemotePath.replace(/\/+$/, '') + '/' + name.trim();
    const res = await callApi('file_create', fullPath, type);
    if (res && res.success) {
        showToast("با موفقیت ساخته شد", "success");
        await refreshFileManager();
    } else {
        showToast("خطا در ایجاد: " + (res.error || 'ناشناخته'), "error");
    }
}

function triggerDeleteRemoteItem(path, isDir) {
    document.getElementById('delete-target-id').value = path;
    document.getElementById('delete-target-type').value = isDir ? 'dir' : 'file';
    document.getElementById('confirm-delete-msg').textContent = `آیا از حذف «${path}» اطمینان دارید؟`;
    openModal('modal-confirm-delete');
}

// ----------------- POWER MANAGEMENT & WAKE-ON-LAN (TAB 5) -----------------
function syncPowerTabMac() {
    if (!currentServerId) return;
    callApi('get_server_details', currentServerId).then(s => {
        if (s && s.mac_address) {
            const input = document.getElementById('wol-mac-input');
            if (input) input.value = s.mac_address;
        }
    });
}

async function detectServerMacAddress() {
    if (!isConnected) {
        showToast("ابتدا باید به سرور متصل باشید تا مک‌آدرس خوانده شود", "error");
        return;
    }
    showToast("در حال خواندن مک‌آدرس کارت شبکه سرور...", "info");
    const mac = await callApi('get_server_mac_address');
    if (mac) {
        document.getElementById('wol-mac-input').value = mac;
        showToast(`مک‌آدرس شناسایی شد: ${mac}`, "success");
        // Save to active server
        if (currentServerId) {
            const s = await callApi('get_server_details', currentServerId);
            if (s) {
                s.mac_address = mac;
                await callApi('save_server', s);
            }
        }
    } else {
        showToast("مک‌آدرس فیزیکی یافت نشد. می‌توانید دستی وارد کنید.", "error");
    }
}

async function sendWakeOnLanPacket() {
    const mac = document.getElementById('wol-mac-input').value.trim();
    const bcast = document.getElementById('wol-bcast-input').value.trim();

    if (!mac) {
        showToast("لطفاً مک‌آدرس کارت شبکه را وارد کنید (مثال: AA:BB:CC:DD:EE:FF)", "error");
        return;
    }

    showToast("در حال ارسال سیگنال Wake-on-LAN Magic Packet...", "info");
    try {
        const res = await callApi('power_wake_on_lan', mac, bcast);
        if (res && res.success) {
            showToast(res.message, "success");
        } else {
            showToast("خطا در ارسال پکت: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function triggerPowerAction(action) {
    if (!isConnected) {
        showToast("ابتدا به سرور متصل شوید", "error");
        return;
    }

    const labels = {
        'reboot': 'راه‌اندازی مجدد (Reboot)',
        'shutdown': 'خاموش کردن کامل (Power Off)',
        'suspend': 'حالت تعلیق (Suspend / Sleep)'
    };

    if (!confirm(`آیا از ${labels[action]} سرور اطمینان دارید؟`)) return;

    showToast(`در حال ارسال دستور ${labels[action]}...`, "info");
    try {
        let res;
        if (action === 'reboot') res = await callApi('power_reboot');
        else if (action === 'shutdown') res = await callApi('power_shutdown');
        else if (action === 'suspend') res = await callApi('power_suspend');

        if (res && res.success) {
            showToast(res.message, "success");
            updateConnectionState(false);
        } else {
            showToast("خطا در ارسال دستور: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

// ----------------- TERMINAL (SSH) -----------------
let terminalHistory = [];
let terminalHistoryIndex = -1;

function clearTerminal() {
    const term = document.getElementById('term-output');
    if (term) term.textContent = 'کنسول پاکسازی شد.\n';
}

function runQuickCommand(cmd) {
    const input = document.getElementById('term-input');
    if (input) {
        input.value = cmd;
        executeTerminalCommand();
    }
}

function handleTerminalKey(event) {
    if (event.key === 'Enter') {
        executeTerminalCommand();
    } else if (event.key === 'ArrowUp') {
        if (terminalHistory.length > 0 && terminalHistoryIndex < terminalHistory.length - 1) {
            terminalHistoryIndex++;
            event.target.value = terminalHistory[terminalHistory.length - 1 - terminalHistoryIndex];
        }
    } else if (event.key === 'ArrowDown') {
        if (terminalHistoryIndex > 0) {
            terminalHistoryIndex--;
            event.target.value = terminalHistory[terminalHistory.length - 1 - terminalHistoryIndex];
        } else if (terminalHistoryIndex === 0) {
            terminalHistoryIndex = -1;
            event.target.value = '';
        }
    }
}

async function executeTerminalCommand() {
    if (!isConnected) {
        showToast("ابتدا به سرور متصل شوید", "error");
        return;
    }
    const input = document.getElementById('term-input');
    const term = document.getElementById('term-output');
    if (!input || !term) return;

    const cmd = input.value.trim();
    if (!cmd) return;

    terminalHistory.push(cmd);
    terminalHistoryIndex = -1;
    input.value = '';

    term.textContent += `\n$ ${cmd}\n`;
    term.scrollTop = term.scrollHeight;

    const btn = document.getElementById('btn-term-send');
    if (btn) btn.disabled = true;

    try {
        const res = await callApi('execute_raw_command', cmd);
        if (res) {
            if (res.stdout) term.textContent += res.stdout;
            if (res.stderr) term.textContent += (res.stdout ? '\n' : '') + `[STDERR]: ${res.stderr}`;
            if (!res.stdout && !res.stderr) term.textContent += '(دستور بدون خروجی متنی اجرا شد)\n';
        }
    } catch (e) {
        term.textContent += `[خطا در ارسال دستور]: ${e.message}\n`;
    } finally {
        if (btn) btn.disabled = false;
        term.scrollTop = term.scrollHeight;
    }
}

// ----------------- DOCKER LOAD FILE / ISO -----------------
async function loadDockerImageFromFile(filePath) {
    if (!isConnected) {
        showToast("ابتدا به سرور متصل شوید", "error");
        return;
    }

    const filename = filePath.split('/').pop();
    const isIso = /\.iso$/i.test(filename);
    const promptMsg = isIso
        ? `آیا مایل به بارگذاری فایل ایزو «${filename}» در داکر هستید؟`
        : `آیا مایل به بارگذاری فایل «${filename}» در داکر هستید (docker load / import)؟`;

    if (!confirm(promptMsg)) return;

    showToast(`در حال پردازش و بارگذاری «${filename}» در داکر...`, "info");
    try {
        const res = await callApi('docker_load_file', filePath);
        if (res && res.success) {
            showToast(res.output || "ایمیج با موفقیت در داکر ثبت شد", "success");
            await fetchDashboardData();
            const modalImages = document.getElementById('modal-docker-images');
            if (modalImages && modalImages.classList.contains('active')) {
                await refreshDockerImagesList();
            }
        } else {
            showToast("خطا در بارگذاری ایمیج: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا در ارتباط با داکر: " + e.message, "error");
    }
}

async function promptLoadFileIntoDocker() {
    if (!isConnected) {
        showToast("ابتدا به سرور متصل شوید", "error");
        return;
    }

    const defaultPath = currentRemotePath ? currentRemotePath.replace(/\/+$/, '') + '/' : '/home/';
    const filePath = prompt("مسیر فایل ایزو یا فایل داکر (.iso, .tar, .tar.gz) روی سرور لینوکس را وارد کنید:", defaultPath);
    if (!filePath || !filePath.trim()) return;

    await loadDockerImageFromFile(filePath.trim());
}

// ----------------- AGENT INSTALLATION -----------------
function promptInstallAgent() {
    if (!isConnected) {
        showToast("ابتدا باید به سرور متصل شوید", "error");
        return;
    }
    document.getElementById('agent-install-status-box').classList.add('hidden');
    document.getElementById('btn-start-agent-install').disabled = false;
    openModal('modal-agent');
}

async function startAgentInstallation() {
    const btn = document.getElementById('btn-start-agent-install');
    const statusBox = document.getElementById('agent-install-status-box');
    const statusText = document.getElementById('agent-install-status-text');
    const sudoPass = document.getElementById('agent-sudo-pass').value;

    btn.disabled = true;
    statusBox.classList.remove('hidden');
    statusText.textContent = "در حال استقرار فایل‌ها و اجرای سرویس روی سرور لینوکس...";

    try {
        const res = await callApi('install_agent', sudoPass);
        if (res && res.success) {
            statusText.textContent = res.message;
            statusText.className = "text-emerald-400";
            showToast("ایجنت با موفقیت نصب و فعال شد!", "success");
            setTimeout(async () => {
                closeModal('modal-agent');
                // Refresh connection and agent status
                const info = await callApi('get_connection_info');
                if (info && info.connected) {
                    updateConnectionState(true, info.server, info.agent);
                }
                await fetchDashboardData(true);
            }, 800);
        } else {
            statusText.textContent = "خطا در نصب: " + (res.message || "پاسخ نامعتبر");
            statusText.className = "text-red-400";
            btn.disabled = false;
        }
    } catch (e) {
        statusText.textContent = "خطا: " + e.message;
        statusText.className = "text-red-400";
        btn.disabled = false;
    }
}

// ----------------- SERVERS CRUD -----------------
async function loadServersManagerCards() {
    const servers = await callApi('get_servers') || [];
    const container = document.getElementById('servers-cards-grid');
    if (!container) return;

    if (servers.length === 0) {
        container.innerHTML = `
            <div class="col-span-full glass-panel p-10 text-center border-dashed border-white/10">
                <p class="text-slate-400 text-sm mb-3">هنوز هیچ سروری ثبت نشده است.</p>
                <button onclick="openAddServerModal()" class="btn btn-primary text-xs px-4 py-2">+ افزودن اولین سرور</button>
            </div>
        `;
        return;
    }

    container.innerHTML = servers.map(s => {
        const isActive = (currentServerId === s.id && isConnected);
        return `
            <div class="glass-panel p-5 flex flex-col justify-between space-y-4 ${isActive ? 'border-emerald-500/40 glow-success' : ''}">
                <div>
                    <div class="flex items-center justify-between mb-2">
                        <h3 class="font-bold text-white text-sm flex items-center gap-2">
                            ${escapeHtml(s.name || s.host)}
                            ${isActive ? '<span class="text-[9px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono">متصل</span>' : ''}
                        </h3>
                        <span class="text-[11px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">Port ${s.port || 22}</span>
                    </div>

                    <div class="space-y-1.5 text-xs text-slate-400 border-t border-white/5 pt-3">
                        <div class="flex justify-between items-center">
                            <span>آدرس میزبان:</span>
                            <span class="font-mono text-slate-200 select-all font-semibold">${escapeHtml(s.host)}</span>
                        </div>
                        <div class="flex justify-between items-center">
                            <span>کاربر لینوکس:</span>
                            <span class="font-mono text-slate-300">${escapeHtml(s.username || 'root')}</span>
                        </div>
                        <div class="flex justify-between items-center">
                            <span>مک‌آدرس:</span>
                            <span class="font-mono text-slate-400 text-[11px]">${s.mac_address || 'ثبت نشده'}</span>
                        </div>
                    </div>
                </div>

                <div class="pt-3 border-t border-white/5 flex items-center justify-between gap-1.5">
                    <button onclick="quickConnectServer('${s.id}')" class="btn ${isActive ? 'btn-danger' : 'btn-primary'} text-xs flex-1 py-1.5">
                        ${isActive ? 'قطع اتصال' : '⚡ اتصال'}
                    </button>
                    <button onclick="testServerCard('${s.id}')" class="btn btn-secondary text-xs px-2.5 py-1.5" title="تست سریع ارتباط">🧪 تست</button>
                    <button onclick="editServer('${s.id}')" class="btn btn-outline text-xs px-2.5 py-1.5" title="ویرایش سرور">✏️</button>
                    <button onclick="triggerDeleteServer('${s.id}', '${escapeHtml(s.name || s.host)}')" class="btn btn-danger text-xs px-2.5 py-1.5" title="حذف سرور">🗑️</button>
                </div>
            </div>
        `;
    }).join('');
}

function openAddServerModal() {
    document.getElementById('modal-server-title').textContent = "افزودن سرور جدید";
    document.getElementById('srv-form-id').value = "";
    document.getElementById('srv-form-name').value = "";
    document.getElementById('srv-form-host').value = "";
    document.getElementById('srv-form-port').value = "22";
    document.getElementById('srv-form-user').value = "root";
    document.getElementById('srv-form-auth-type').value = "password";
    document.getElementById('srv-form-pass').value = "";
    document.getElementById('srv-form-key').value = "";
    document.getElementById('srv-form-mac').value = "";
    toggleAuthFields();
    openModal('modal-server');
}

async function editCurrentSelectedServer() {
    if (!currentServerId) {
        showToast("ابتدا یک سرور را از منوی بالا انتخاب کنید", "error");
        return;
    }
    await editServer(currentServerId);
}

async function editServer(id) {
    const s = await callApi('get_server_details', id);
    if (!s) {
        showToast("اطلاعات سرور یافت نشد", "error");
        return;
    }

    document.getElementById('modal-server-title').textContent = `ویرایش مشخصات: ${s.name || s.host}`;
    document.getElementById('srv-form-id').value = s.id;
    document.getElementById('srv-form-name').value = s.name || "";
    document.getElementById('srv-form-host').value = s.host || "";
    document.getElementById('srv-form-port').value = s.port || "22";
    document.getElementById('srv-form-user').value = s.username || "root";
    document.getElementById('srv-form-auth-type').value = s.key_path ? "key" : "password";
    document.getElementById('srv-form-pass').value = s.password || "";
    document.getElementById('srv-form-key').value = s.key_path || "";
    document.getElementById('srv-form-mac').value = s.mac_address || "";
    toggleAuthFields();
    openModal('modal-server');
}

function toggleAuthFields() {
    const type = document.getElementById('srv-form-auth-type').value;
    if (type === 'key') {
        document.getElementById('auth-field-password').classList.add('hidden');
        document.getElementById('auth-field-key').classList.remove('hidden');
    } else {
        document.getElementById('auth-field-password').classList.remove('hidden');
        document.getElementById('auth-field-key').classList.add('hidden');
    }
}

async function testConnectionFromModal() {
    const btn = document.getElementById('btn-modal-test-conn');
    const host = document.getElementById('srv-form-host').value.trim();
    const port = parseInt(document.getElementById('srv-form-port').value) || 22;
    const username = document.getElementById('srv-form-user').value.trim() || 'root';
    const authType = document.getElementById('srv-form-auth-type').value;
    const password = document.getElementById('srv-form-pass').value;
    const keyPath = document.getElementById('srv-form-key').value.trim();

    if (!host) {
        showToast("لطفاً آدرس IP سرور را وارد کنید", "error");
        return;
    }

    btn.disabled = true;
    btn.textContent = "در حال تست...";

    const res = await callApi('test_connection_params', {
        host, port, username,
        password: authType === 'password' ? password : '',
        key_path: authType === 'key' ? keyPath : ''
    });

    btn.disabled = false;
    btn.textContent = "🧪 تست اتصال";

    if (res && res.success) {
        showToast(`اتصال برقرار شد! زمان پاسخ: ${res.latency_ms || 0}ms`, "success");
    } else {
        showToast("اتصال ناموفق بود: " + (res.message || "خطای نامشخص"), "error");
    }
}

async function testServerCard(id) {
    const s = await callApi('get_server_details', id);
    if (!s) return;
    showToast(`در حال تست اتصال به ${s.name || s.host}...`, "info");
    const res = await callApi('test_connection_params', s);
    if (res && res.success) {
        showToast(`ارتباط با ${s.name || s.host} برقرار است (${res.latency_ms}ms)`, "success");
    } else {
        showToast(`خطا در اتصال: ` + (res.message || "خطا"), "error");
    }
}

async function saveServerForm() {
    const id = document.getElementById('srv-form-id').value;
    const name = document.getElementById('srv-form-name').value.trim();
    const host = document.getElementById('srv-form-host').value.trim();
    const port = parseInt(document.getElementById('srv-form-port').value) || 22;
    const username = document.getElementById('srv-form-user').value.trim() || 'root';
    const authType = document.getElementById('srv-form-auth-type').value;
    const password = document.getElementById('srv-form-pass').value;
    const keyPath = document.getElementById('srv-form-key').value.trim();
    const mac = document.getElementById('srv-form-mac').value.trim();

    if (!host) {
        showToast("لطفاً آدرس IP یا دامنه سرور را وارد کنید", "error");
        return;
    }

    const payload = {
        name: name || host,
        host: host,
        port: port,
        username: username,
        password: authType === 'password' ? password : '',
        key_path: authType === 'key' ? keyPath : '',
        mac_address: mac
    };
    if (id) payload.id = id;

    const saved = await callApi('save_server', payload);
    closeModal('modal-server');
    showToast(id ? "مشخصات سرور بروزرسانی شد" : "سرور جدید ذخیره شد", "success");

    await loadServersList();
    if (!document.getElementById('tab-servers').classList.contains('hidden')) {
        loadServersManagerCards();
    }

    if (saved && saved.id) {
        currentServerId = saved.id;
        document.getElementById('server-select').value = saved.id;
        updateQuickActionButtonsState();
    }
}

function deleteCurrentSelectedServer() {
    if (!currentServerId) {
        showToast("سروری برای حذف انتخاب نشده است", "error");
        return;
    }
    const select = document.getElementById('server-select');
    const selectedText = select.options[select.selectedIndex]?.text || "این سرور";
    triggerDeleteServer(currentServerId, selectedText);
}

function triggerDeleteServer(id, serverName) {
    document.getElementById('delete-target-id').value = id;
    document.getElementById('delete-target-type').value = 'server';
    document.getElementById('confirm-delete-msg').textContent = `آیا از حذف سرور «${serverName}» اطمینان دارید؟`;
    openModal('modal-confirm-delete');
}

async function confirmExecuteDelete() {
    const id = document.getElementById('delete-target-id').value;
    const type = document.getElementById('delete-target-type').value;
    if (!id) return;

    closeModal('modal-confirm-delete');

    if (type === 'server') {
        await callApi('delete_server', id);
        showToast("سرور با موفقیت حذف شد", "info");
        if (currentServerId === id) {
            currentServerId = null;
            updateConnectionState(false);
        }
        await loadServersList();
        if (!document.getElementById('tab-servers').classList.contains('hidden')) {
            loadServersManagerCards();
        }
    } else if (type === 'file' || type === 'dir') {
        const res = await callApi('file_delete', id, type === 'dir');
        if (res && res.success) {
            showToast("آیتم حذف شد", "info");
            await refreshFileManager();
        } else {
            showToast("خطا در حذف: " + (res.error || 'ناشناخته'), "error");
        }
    }
}

async function quickConnectServer(id) {
    if (currentServerId === id && isConnected) {
        await toggleConnectServer();
        loadServersManagerCards();
        return;
    }

    currentServerId = id;
    document.getElementById('server-select').value = id;
    updateQuickActionButtonsState();
    switchTab('dashboard');
    await toggleConnectServer();
    loadServersManagerCards();
}

// ----------------- LOGS & AUTO-REFRESH -----------------
async function viewContainerLogs(containerId, containerName) {
    currentViewingContainerId = containerId;
    document.getElementById('logs-modal-title').textContent = `لاگ‌های کانتینر: ${containerName}`;
    document.getElementById('container-logs-content').textContent = "در حال دریافت لاگ‌ها...";
    openModal('modal-logs');
    await refreshCurrentLogs();
}

async function refreshCurrentLogs() {
    if (!currentViewingContainerId) return;
    try {
        const res = await callApi('get_container_logs', currentViewingContainerId, 250);
        const content = document.getElementById('container-logs-content');
        if (res && res.logs) {
            content.textContent = res.logs || "(لاگی ثبت نشده است)";
            content.scrollTop = content.scrollHeight;
        } else {
            content.textContent = "لاگی یافت نشد.";
        }
    } catch (e) {
        document.getElementById('container-logs-content').textContent = "خطا در دریافت لاگ: " + e.message;
    }
}

function startAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(() => {
        const chk = document.getElementById('chk-auto-refresh');
        if (chk && chk.checked && isConnected) {
            fetchDashboardData();
        }
    }, 6000);
}

function toggleAutoRefresh() {
    const chk = document.getElementById('chk-auto-refresh');
    if (!chk.checked && autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
    } else {
        startAutoRefresh();
    }
}

// ----------------- DOCKER IMAGES & RUN CONTAINER -----------------
let cachedDockerImages = [];

// ----------------- PORT MAPPING HELPERS -----------------
function addPortMappingRow(hostPort = '', containerPort = '', protocol = 'tcp') {
    const container = document.getElementById('port-mappings-list');
    if (!container) return;

    const row = document.createElement('div');
    row.className = "port-mapping-row flex items-center gap-2 bg-slate-800/80 p-2.5 rounded-xl border border-white/5";
    row.innerHTML = `
        <div class="flex-1">
            <label class="block text-[10px] text-slate-400 mb-0.5">پورت دستگاه (Host / Server)</label>
            <input type="number" class="host-port-input w-full bg-slate-900 border border-white/10 rounded-lg px-2.5 py-1.5 text-white font-mono text-xs outline-none focus:border-blue-500" placeholder="مثلاً: 8080" min="1" max="65535" value="${escapeHtml(String(hostPort || ''))}">
        </div>

        <div class="text-slate-500 pt-3 flex flex-col items-center">
            <span class="text-xs">➔</span>
        </div>

        <div class="flex-1">
            <label class="block text-[10px] text-slate-400 mb-0.5">پورت کانتینر (Container)</label>
            <input type="number" class="container-port-input w-full bg-slate-900 border border-white/10 rounded-lg px-2.5 py-1.5 text-white font-mono text-xs outline-none focus:border-blue-500" placeholder="مثلاً: 80" min="1" max="65535" value="${escapeHtml(String(containerPort || ''))}">
        </div>

        <div class="w-20">
            <label class="block text-[10px] text-slate-400 mb-0.5">پروتکل</label>
            <select class="protocol-select w-full bg-slate-900 border border-white/10 rounded-lg px-2 py-1.5 text-slate-300 font-mono text-xs outline-none">
                <option value="tcp" ${(protocol || 'tcp').toLowerCase() === 'tcp' ? 'selected' : ''}>TCP</option>
                <option value="udp" ${(protocol || '').toLowerCase() === 'udp' ? 'selected' : ''}>UDP</option>
            </select>
        </div>

        <button type="button" onclick="removePortMappingRow(this)" class="text-slate-500 hover:text-rose-400 p-1 pt-3.5" title="حذف این پورت">✕</button>
    `;
    container.appendChild(row);
}

function removePortMappingRow(btn) {
    const container = document.getElementById('port-mappings-list');
    if (!container) return;
    const rows = container.querySelectorAll('.port-mapping-row');
    if (rows.length > 1) {
        const row = btn.closest('.port-mapping-row');
        if (row) row.remove();
    } else {
        const row = rows[0];
        const h = row.querySelector('.host-port-input');
        const c = row.querySelector('.container-port-input');
        if (h) h.value = '';
        if (c) c.value = '';
    }
}

function setQuickPortMapping(hostPort, containerPort, protocol = 'tcp') {
    const container = document.getElementById('port-mappings-list');
    if (!container) return;
    const rows = container.querySelectorAll('.port-mapping-row');
    if (rows.length === 1) {
        const firstRow = rows[0];
        const hInput = firstRow.querySelector('.host-port-input');
        const cInput = firstRow.querySelector('.container-port-input');
        const pSelect = firstRow.querySelector('.protocol-select');
        if (!hInput.value && !cInput.value) {
            hInput.value = hostPort;
            cInput.value = containerPort;
            if (pSelect) pSelect.value = protocol;
            return;
        }
    }
    addPortMappingRow(hostPort, containerPort, protocol);
}

function collectPortMappings() {
    const container = document.getElementById('port-mappings-list');
    if (!container) return '';
    const rows = container.querySelectorAll('.port-mapping-row');
    const portParts = [];

    rows.forEach(row => {
        const hInput = row.querySelector('.host-port-input');
        const cInput = row.querySelector('.container-port-input');
        const pSelect = row.querySelector('.protocol-select');

        const host = hInput ? hInput.value.trim() : '';
        const cont = cInput ? cInput.value.trim() : '';
        const proto = (pSelect ? pSelect.value : 'tcp').toLowerCase();

        if (cont) {
            const protoSuffix = (proto && proto !== 'tcp') ? `/${proto}` : '';
            if (host) {
                portParts.push(`${host}:${cont}${protoSuffix}`);
            } else {
                portParts.push(`${cont}${protoSuffix}`);
            }
        }
    });

    return portParts.join(' ');
}

function setPortMappingsFromStr(portStr) {
    const container = document.getElementById('port-mappings-list');
    if (!container) return;
    container.innerHTML = '';

    if (!portStr || !portStr.trim()) {
        resetPortMappingsToDefault();
        return;
    }

    const items = portStr.replace(/,/g, ' ').split(/\s+/).filter(Boolean);
    if (items.length === 0) {
        resetPortMappingsToDefault();
        return;
    }

    items.forEach(item => {
        let proto = 'tcp';
        let val = item;
        if (val.includes('/')) {
            const parts = val.split('/');
            val = parts[0];
            proto = parts[1] || 'tcp';
        }

        if (val.includes(':')) {
            const parts = val.split(':');
            const host = parts[0];
            const cont = parts[1];
            addPortMappingRow(host, cont, proto);
        } else {
            addPortMappingRow(val, val, proto);
        }
    });
}

function resetPortMappingsToDefault() {
    const container = document.getElementById('port-mappings-list');
    if (!container) return;
    container.innerHTML = '';
    addPortMappingRow('', '', 'tcp');
}

function appendEnvVar(elementId, envString) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const current = el.value.trim();
    if (current.includes(envString)) {
        showToast("این متغیر محیطی قبلاً درج شده است", "info");
        return;
    }
    el.value = current ? `${current}\n${envString}` : envString;
    showToast(`متغیر ${envString} با موفقیت اضافه شد`, "success");
}

async function openRunContainerModal(prefillImage = "") {
    if (!isConnected) {
        showToast("لطفاً ابتدا به سرور متصل شوید", "error");
        return;
    }
    const imgInput = document.getElementById('run-container-image');
    if (imgInput) imgInput.value = prefillImage || '';
    
    const nameInput = document.getElementById('run-container-name');
    if (nameInput) nameInput.value = '';

    const envInput = document.getElementById('run-container-env');
    if (envInput) envInput.value = '';

    const restartSelect = document.getElementById('run-container-restart');
    if (restartSelect) restartSelect.value = 'unless-stopped';

    const replaceCheck = document.getElementById('run-container-replace-existing');
    if (replaceCheck) replaceCheck.checked = false;

    resetPortMappingsToDefault();

    const drop = document.getElementById('run-container-existing-images');
    if (drop) drop.classList.add('hidden');

    openModal('modal-run-container');
    loadImagesIntoRunDropdown();
}

async function loadImagesIntoRunDropdown() {
    const drop = document.getElementById('run-container-existing-images');
    if (!drop) return;
    try {
        const res = await callApi('docker_list_images');
        if (res && res.images && res.images.length > 0) {
            cachedDockerImages = res.images;
            drop.innerHTML = '<option value="">-- انتخاب از ایمیج‌های موجود در سرور (' + res.images.length + ' مورد) --</option>' +
                res.images.map(img => `<option value="${escapeHtml(img.full_tag)}">${escapeHtml(img.full_tag)} (${escapeHtml(img.size)})</option>`).join('');
            drop.classList.remove('hidden');
        } else {
            drop.classList.add('hidden');
        }
    } catch (e) {
        console.error("Error loading images dropdown:", e);
    }
}

function onExistingImageSelected() {
    const drop = document.getElementById('run-container-existing-images');
    const imgInput = document.getElementById('run-container-image');
    if (drop && drop.value && imgInput) {
        imgInput.value = drop.value;
    }
}

async function executeRunContainer() {
    const imgInput = document.getElementById('run-container-image');
    const image = imgInput ? imgInput.value.trim() : '';
    if (!image) {
        showToast("نام یا تگ ایمیج داکر الزامی است", "error");
        return;
    }

    const nameInput = document.getElementById('run-container-name');
    const name = nameInput ? nameInput.value.trim() : '';

    const ports = collectPortMappings();

    const envInput = document.getElementById('run-container-env');
    const env = envInput ? envInput.value.trim() : '';

    const restartSelect = document.getElementById('run-container-restart');
    const restart = restartSelect ? restartSelect.value : 'unless-stopped';

    const replaceCheck = document.getElementById('run-container-replace-existing');
    let replaceExisting = replaceCheck ? replaceCheck.checked : false;

    const btn = document.getElementById('btn-submit-run-container');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال ایجاد و استارت کانتینر...";
    }

    try {
        const res = await callApi('docker_run_container', image, name, ports, env, restart, replaceExisting);
        if (res && res.success) {
            showToast(`کانتینر با موفقیت استارت شد! شناسه: ${res.container_id.slice(0, 12)}`, "success");
            closeModal('modal-run-container');
            await fetchDashboardData(true);
            switchTab('containers');
        } else {
            const errStr = res ? (res.error || '') : '';
            // Detect name conflict
            if (errStr.includes('already in use by container') && name) {
                if (confirm(`کانتینری با نام "${name}" از قبل در سیستم وجود دارد.\nآیا می‌خواهید کانتینر قدیمی متوقف و حذف شده و کانتینر جدید جایگزین آن شود؟`)) {
                    if (btn) btn.textContent = "در حال حذف کانتینر قبلی و جایگزینی...";
                    const retryRes = await callApi('docker_run_container', image, name, ports, env, restart, true);
                    if (retryRes && retryRes.success) {
                        showToast(`کانتینر قبلی با موفقیت حذف شد و کانتینر جدید با نام "${name}" استارت گردید!`, "success");
                        closeModal('modal-run-container');
                        await fetchDashboardData(true);
                        switchTab('containers');
                        return;
                    } else {
                        showToast("خطا در جایگزینی کانتینر: " + (retryRes ? retryRes.error : 'ناشناخته'), "error");
                        return;
                    }
                }
            }
            showToast("خطا در اجرای کانتینر: " + (errStr || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "🚀 استارت کانتینر";
        }
    }
}

async function openDockerImagesModal() {
    if (!isConnected) {
        showToast("لطفاً ابتدا به سرور متصل شوید", "error");
        return;
    }
    openModal('modal-docker-images');
    await refreshDockerImagesList();
}

async function refreshDockerImagesList() {
    const tbody = document.getElementById('docker-images-tbody');
    const badge = document.getElementById('badge-docker-images-count');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="4" class="py-6 text-center text-slate-500">در حال دریافت لیست ایمیج‌ها...</td></tr>';

    try {
        const res = await callApi('docker_list_images');
        if (res && res.images) {
            cachedDockerImages = res.images;
            if (badge) badge.textContent = res.images.length;

            if (res.images.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" class="py-8 text-center text-slate-500">هیچ ایمیجی در داکر یافت نشد. می‌توانید با دکمه بالا یک ایمیج pull کنید.</td></tr>';
                return;
            }

            tbody.innerHTML = res.images.map(img => `
                <tr class="hover:bg-white/[0.02] transition">
                    <td class="py-2.5 px-3">
                        <div class="font-bold text-white font-mono">${escapeHtml(img.full_tag)}</div>
                        <div class="text-[10px] text-slate-500 font-mono">${escapeHtml(img.id)}</div>
                    </td>
                    <td class="py-2.5 px-3 font-mono text-slate-300">${escapeHtml(img.size)}</td>
                    <td class="py-2.5 px-3 font-mono text-slate-400 text-[11px]">${escapeHtml(img.created)}</td>
                    <td class="py-2.5 px-3 text-center">
                        <div class="inline-flex items-center gap-1.5">
                            <button onclick="closeModal('modal-docker-images'); openRunContainerModal('${escapeHtml(img.full_tag)}');" class="btn btn-primary text-xs px-2.5 py-1" title="اجرای این ایمیج">
                                🚀 اجرا
                            </button>
                            <button onclick="triggerRemoveDockerImage('${escapeHtml(img.full_tag || img.id)}')" class="btn btn-danger text-xs px-2 py-1" title="حذف ایمیج">
                                🗑️
                            </button>
                        </div>
                    </td>
                </tr>
            `).join('');
        } else {
            tbody.innerHTML = '<tr><td colspan="4" class="py-6 text-center text-rose-400">خطا در دریافت لیست ایمیج‌ها</td></tr>';
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-rose-400">خطا: ${escapeHtml(e.message)}</td></tr>`;
    }
}

async function openPullImagePrompt() {
    closeModal('modal-docker-images');
    openDockerDownloadsModal();
    const input = document.getElementById('input-new-download-image');
    if (input) {
        input.focus();
    }
}

async function triggerRemoveDockerImage(imageTagOrId) {
    if (!confirm(`آیا از حذف ایمیج داکر "${imageTagOrId}" مطمئن هستید؟`)) return;

    showToast(`در حال حذف ایمیج ${imageTagOrId}...`, "info");
    try {
        const res = await callApi('docker_remove_image', imageTagOrId);
        if (res && res.success) {
            showToast(`ایمیج ${imageTagOrId} حذف شد`, "success");
            await refreshDockerImagesList();
        } else {
            showToast("خطا در حذف ایمیج: " + (res.stderr || res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function promptPruneDocker() {
    if (!confirm("آیا مایل به پاکسازی منابع متوقف‌شده و حافظه موقت داکر (docker system prune) هستید؟")) return;

    showToast("در حال پاکسازی داکر...", "info");
    try {
        const res = await callApi('docker_prune');
        if (res && res.success) {
            showToast("پاکسازی داکر با موفقیت انجام شد", "success");
            await fetchDashboardData(true);
        } else {
            showToast("خطا در پاکسازی داکر", "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

// ----------------- REMOTE URL DOWNLOAD -----------------
function openDownloadUrlModal() {
    if (!isConnected) {
        showToast("لطفاً ابتدا به سرور متصل شوید", "error");
        return;
    }
    document.getElementById('download-url-input').value = '';
    // Pre-fill target dir with current remote path
    document.getElementById('download-target-dir-input').value = currentRemotePath || '';
    document.getElementById('download-filename-input').value = '';
    openModal('modal-download-url');
}

async function executeDownloadUrl() {
    const url = document.getElementById('download-url-input').value.trim();
    if (!url) {
        showToast("آدرس لینک دانلود الزامی است", "error");
        return;
    }

    const targetDir = document.getElementById('download-target-dir-input').value.trim();
    const filename = document.getElementById('download-filename-input').value.trim();

    const btn = document.getElementById('btn-submit-download-url');
    btn.disabled = true;
    btn.textContent = "در حال ارسال دستور دانلود به سرور...";

    try {
        const res = await callApi('file_download_from_url', url, targetDir || null, filename || null);
        if (res && res.success) {
            showToast(res.message, "success");
            closeModal('modal-download-url');
            // Refresh file list after a couple seconds
            setTimeout(() => {
                refreshFileManager();
            }, 2500);
        } else {
            showToast("خطا در شروع دانلود: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "📥 شروع دانلود در لینوکس";
    }
}

// ----------------- REMOTE DESKTOP (RDP) CONTROLLER -----------------
async function checkRdpStatus(notify = false) {
    const badge = document.getElementById('rdp-status-badge');
    const hostInput = document.getElementById('rdp-target-host');
    const userInput = document.getElementById('rdp-target-user');
    
    if (badge) {
        badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-300 font-mono";
        badge.textContent = "در حال بررسی...";
    }

    try {
        const res = await callApi('rdp_check_status');
        if (!res || !res.connected) {
            if (badge) {
                badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono";
                badge.textContent = "سرور متصل نیست";
            }
            if (notify) showToast("ابتدا به سرور لینوکس متصل شوید", "error");
            return;
        }

        if (hostInput) hostInput.value = res.host || (currentConnectedHost || '--');
        if (userInput && !userInput.value) userInput.value = res.username || '';

        if (badge) {
            if (res.running) {
                badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-bold border border-emerald-500/30";
                badge.textContent = "🟢 فعال و آماده اتصال (پورت ۳۳۸۹ باز است)";
            } else if (res.installed) {
                badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/30";
                badge.textContent = "🟡 نصب‌شده (سرویس خاموش است - ری‌استارت کنید)";
            } else {
                badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 border border-rose-500/30";
                badge.textContent = "🔴 سرویس xrdp فعال نیست (کلیک روی نصب و تعمیر)";
            }
        }

        // Check local physical screen session
        const warningBanner = document.getElementById('rdp-local-warning-banner');
        if (warningBanner) {
            if (res.local_user_active) {
                warningBanner.classList.remove('hidden');
            } else {
                warningBanner.classList.add('hidden');
            }
        }

        if (notify) {
            if (res.running) {
                if (res.local_user_active) {
                    showToast("سرویس فعال است اما کاربر روی سیستم فیزیکی لاگین است (خطر صفحه سیاه)", "info");
                } else {
                    showToast("سرویس xrdp فعال و پورت ۳۳۸۹ آماده اتصال است", "success");
                }
            } else {
                showToast("سرویس xrdp روی سرور فعال نیست. روی نصب خودکار کلیک کنید", "info");
            }
        }
    } catch (e) {
        console.error("RDP Status check failed:", e);
        if (badge) {
            badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400";
            badge.textContent = "خطا در بررسی وضعیت";
        }
    }
}

async function launchWindowsRdpClient() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }

    const hostInput = document.getElementById('rdp-target-host');
    const userInput = document.getElementById('rdp-target-user');
    const host = hostInput ? hostInput.value.trim() : null;
    const user = userInput ? userInput.value.trim() : null;
    const resolution = document.getElementById('rdp-resolution-select')?.value || '1920x1080';
    const scaleFactor = document.getElementById('rdp-scale-select')?.value || '125';

    if (!host || host === '--') {
        showToast("آدرس سرور مشخص نشده است", "error");
        return;
    }

    const btn = document.getElementById('btn-rdp-launch');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال باز کردن mstsc ویندوز...";
    }

    try {
        const res = await callApi('rdp_launch_client', host, user, resolution, scaleFactor);
        if (res && res.success) {
            showToast(res.message || "ریموت دسکتاپ ویندوز باز شد", "success");
        } else {
            showToast("خطا در باز کردن کلاینت RDP: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
              <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path>
              </svg>
              🚀 باز کردن ریموت دسکتاپ ویندوز
            `;
        }
    }
}

async function applyLinuxHighDpi() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }
    const scaleSelect = document.getElementById('rdp-scale-select');
    const scaleVal = scaleSelect ? scaleSelect.value : '125';
    let targetDpi = 120;
    if (scaleVal === '150') targetDpi = 144;
    else if (scaleVal === '100') targetDpi = 96;

    const btn = document.getElementById('btn-rdp-dpi');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال اعمال مقیاس...";
    }
    try {
        const res = await callApi('rdp_set_linux_dpi', targetDpi);
        if (res && res.success) {
            showToast(res.message || "تنظیمات بزرگ‌نمایی در لینوکس اعمال شد", "success");
        } else {
            showToast("خطا در تنظیم مقیاس: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "🔍 درشت کردن آیکون‌ها و فونت‌ها در لینوکس";
        }
    }
}

async function promptSetupRdpService() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }

    const sudoPassInput = document.getElementById('rdp-sudo-pass');
    const sudoPass = sudoPassInput ? sudoPassInput.value : '';

    const btn = document.getElementById('btn-rdp-setup');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "⏳ در حال اعمال پچ در /etc/xrdp/startwm.sh و رفع باگ...";
    }

    try {
        const res = await callApi('rdp_setup_service', sudoPass || null);
        if (res && res.success) {
            showToast(res.message || "پچ با موفقیت اعمال شد", "success");
            await checkRdpStatus(false);
        } else {
            showToast("خطا در پیکربندی: " + (res.error || 'خطای دسترسی یا sudo'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "🛠️ اعمال پچ ضد صفحه سیاه (Fix Black Screen)";
        }
    }
}

async function installXfceForRdp() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }
    const sudoPassInput = document.getElementById('rdp-sudo-pass');
    const sudoPass = sudoPassInput ? sudoPassInput.value : '';

    const btn = document.getElementById('btn-rdp-xfce');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "⏳ در حال نصب میزکار سبک XFCE (لطفاً ۲-۳ دقیقه صبور باشید)...";
    }

    try {
        const res = await callApi('rdp_install_xfce', sudoPass || null);
        if (res && res.success) {
            showToast(res.message || "میزکار XFCE با موفقیت نصب و تنظیم شد!", "success");
            await checkRdpStatus(false);
        } else {
            showToast("خطا در نصب XFCE: " + (res.error || 'خطای دسترسی sudo'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "📦 نصب میزکار سبک XFCE ویژه ریموت (حل قطعی بدون نیاز به خروج فیزیکی)";
        }
    }
}

async function restartRdpService() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }

    const sudoPassInput = document.getElementById('rdp-sudo-pass');
    const sudoPass = sudoPassInput ? sudoPassInput.value : '';

    const btn = document.getElementById('btn-rdp-restart');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال ری‌استارت...";
    }

    try {
        const res = await callApi('rdp_restart_service', sudoPass || null);
        if (res && res.success) {
            showToast(res.message || "سرویس ری‌استارت شد", "success");
            await checkRdpStatus(false);
        } else {
            showToast("خطا در ری‌استارت: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "🔄 ری‌استارت";
        }
    }
}

// ----------------- TAILSCALE CONTROLLER -----------------
let cachedTsPeers = [];
let cachedTsData = null;

async function refreshTailscaleStatus(notify = false) {
    const headerBadge = document.getElementById('ts-header-badge');
    const navDot = document.getElementById('badge-tailscale-status');
    const btnRefresh = document.getElementById('btn-ts-refresh');
    
    if (headerBadge) {
        headerBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-300 font-mono";
        headerBadge.textContent = "در حال بررسی وضعیت...";
    }

    try {
        const res = await callApi('tailscale_get_status');
        cachedTsData = res || {};
        
        const local = (res && res.local) || {};
        const remote = (res && res.remote) || {};

        // 1. Header Badge & Sidebar Dot
        if (headerBadge) {
            if (local.running || remote.running) {
                headerBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-bold border border-emerald-500/30";
                headerBadge.textContent = "🟢 شبکه تایل‌اسکیل فعال است";
                if (navDot) navDot.className = "mr-auto w-2 h-2 rounded-full bg-emerald-400";
            } else {
                headerBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/30";
                headerBadge.textContent = "🟡 کلاینت‌ها متوقف یا غیرفعال";
                if (navDot) navDot.className = "mr-auto w-2 h-2 rounded-full bg-amber-400";
            }
        }

        // 2. Windows Node Card
        const localPill = document.getElementById('ts-local-status-pill');
        const localIp = document.getElementById('ts-local-ip');
        const localTailnet = document.getElementById('ts-local-tailnet');
        const localSubtext = document.getElementById('ts-local-subtext');
        const localExitSelect = document.getElementById('ts-local-exit-node-select');
        const optShields = document.getElementById('ts-local-opt-shields');

        if (localPill) {
            if (local.running) {
                localPill.className = "text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-bold";
                localPill.textContent = "آنلاین و متصل";
            } else if (local.installed) {
                localPill.className = "text-[11px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400";
                localPill.textContent = "متوقف";
            } else {
                localPill.className = "text-[11px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400";
                localPill.textContent = "نصب‌نشده در ویندوز";
            }
        }

        if (localIp) localIp.textContent = (local.device && local.device.tailscale_ip) || '--';
        if (localTailnet) {
            const devName = (local.device && local.device.hostname) || 'ویندوز';
            const tailnet = local.tailnet_name || (local.user && local.user.email) || '--';
            localTailnet.textContent = `${devName} (${tailnet})`;
        }
        if (localSubtext && local.user) {
            localSubtext.textContent = `حساب: ${local.user.display_name || local.user.email || 'متصل'}`;
        }

        if (optShields && local.preferences) {
            optShields.checked = !!local.preferences.shields_up;
        }

        // 3. Remote Linux Node Card
        const remotePill = document.getElementById('ts-remote-status-pill');
        const remoteIp = document.getElementById('ts-remote-ip');
        const remoteHost = document.getElementById('ts-remote-hostname');
        const remoteInstallBox = document.getElementById('ts-remote-install-box');
        const btnToggleExitAdv = document.getElementById('btn-ts-toggle-exit-adv');
        const btnToggleSsh = document.getElementById('btn-ts-toggle-ssh');

        if (remotePill) {
            if (remote.running) {
                remotePill.className = "text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-bold";
                remotePill.textContent = "آنلاین در سرور";
            } else if (remote.installed) {
                remotePill.className = "text-[11px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400";
                remotePill.textContent = "سرویس متوقف";
            } else {
                remotePill.className = "text-[11px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400";
                remotePill.textContent = "نصب‌نشده روی لینوکس";
            }
        }

        if (remoteIp) remoteIp.textContent = (remote.device && remote.device.tailscale_ip) || '--';
        if (remoteHost) remoteHost.textContent = (remote.device && remote.device.hostname) || (currentConnectedHost || '--');

        if (remoteInstallBox) {
            if (isConnected && !remote.installed) {
                remoteInstallBox.classList.remove('hidden');
            } else {
                remoteInstallBox.classList.add('hidden');
            }
        }

        // Remote capabilities buttons
        if (btnToggleExitAdv && remote.device) {
            const isAdvExit = remote.device.is_exit_node_option || false;
            btnToggleExitAdv.className = isAdvExit 
                ? "btn btn-primary text-[11px] px-3 py-1 font-bold bg-emerald-600 hover:bg-emerald-500" 
                : "btn btn-secondary text-[11px] px-3 py-1 font-bold";
            btnToggleExitAdv.textContent = isAdvExit ? "فعال (خروجی مجاز)" : "غیرفعال (فعال‌سازی)";
        }

        if (btnToggleSsh && remote.preferences) {
            const isSsh = remote.preferences.ssh_enabled || false;
            btnToggleSsh.className = isSsh 
                ? "btn btn-primary text-[11px] px-3 py-1 font-bold bg-emerald-600 hover:bg-emerald-500" 
                : "btn btn-secondary text-[11px] px-3 py-1 font-bold";
            btnToggleSsh.textContent = isSsh ? "فعال (مجاز)" : "غیرفعال (فعال‌سازی)";
        }

        // 4. Update Exit Node Dropdown & Peers Table
        const peers = (local.peers && local.peers.length > 0) ? local.peers : ((remote.peers && remote.peers.length > 0) ? remote.peers : []);
        cachedTsPeers = peers;

        // Populate exit node dropdown
        if (localExitSelect) {
            localExitSelect.innerHTML = '<option value="">بدون Exit Node (اتصال مستقیم عادی)</option>';
            
            const exitNodes = (local.exit_nodes && local.exit_nodes.length > 0) 
                ? local.exit_nodes 
                : peers.filter(p => p.is_exit_node_option);

            exitNodes.forEach(en => {
                const opt = document.createElement('option');
                opt.value = en.ip || en.tailscale_ip || en.hostname;
                const statusTag = en.online ? '🟢' : '⚪';
                opt.textContent = `${statusTag} ${en.hostname} (${en.ip || en.tailscale_ip})`;
                if (en.active || en.is_active_exit_node) {
                    opt.selected = true;
                }
                localExitSelect.appendChild(opt);
            });
        }

        renderTailscalePeersTable(peers);
        refreshTailscaleFunnelStatus(false);

        if (notify) showToast("وضعیت شبکه تایل‌اسکیل بروزرسانی شد", "success");
    } catch (e) {
        console.error("Error refreshing Tailscale status:", e);
        if (headerBadge) {
            headerBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400";
            headerBadge.textContent = "خطا در بررسی وضعیت تایل‌اسکیل";
        }
    }
}

function renderTailscalePeersTable(peers) {
    const tbody = document.getElementById('ts-peers-tbody');
    const peersCount = document.getElementById('ts-peers-count');
    if (!tbody) return;

    if (peersCount) peersCount.textContent = `${peers.length} دستگاه`;

    if (!peers || peers.length === 0) {
        tbody.innerHTML = `
            <tr>
              <td colspan="6" class="text-center py-6 text-slate-500">هیچ دستگاه دیگری در این شبکه یافت نشد</td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = peers.map(p => {
        let osIcon = '💻';
        const os = (p.os || '').toLowerCase();
        if (os.includes('linux')) osIcon = '🐧';
        else if (os.includes('windows')) osIcon = '🪟';
        else if (os.includes('darwin') || os.includes('mac') || os.includes('ios')) osIcon = '🍎';
        else if (os.includes('android')) osIcon = '🤖';

        const isOnline = p.online;
        const statusBadge = isOnline 
            ? '<span class="inline-flex items-center gap-1 text-emerald-400 font-semibold"><span class="w-2 h-2 rounded-full bg-emerald-400"></span> آنلاین</span>' 
            : '<span class="inline-flex items-center gap-1 text-slate-500"><span class="w-2 h-2 rounded-full bg-slate-600"></span> آفلاین</span>';

        let exitBadge = '<span class="text-slate-500 text-[10px]">-</span>';
        if (p.is_active_exit_node) {
            exitBadge = '<span class="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-bold text-[10px]">خروجی فعال 🚀</span>';
        } else if (p.is_exit_node_option) {
            exitBadge = '<span class="px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300 text-[10px]">مجاز به Exit</span>';
        }

        const ip = p.tailscale_ip || (p.all_ips && p.all_ips[0]) || '--';

        return `
            <tr class="hover:bg-slate-800/40 transition">
              <td class="py-2.5 px-3">
                <div class="font-bold text-white flex items-center gap-1.5">
                  <span>${osIcon}</span>
                  <span class="font-mono text-xs">${escapeHtml(p.hostname || p.dns_name)}</span>
                </div>
              </td>
              <td class="py-2.5 px-3 font-mono text-slate-400 text-[11px]">${escapeHtml(p.os || 'unknown')}</td>
              <td class="py-2.5 px-3">
                <div class="flex items-center gap-1.5">
                  <span class="font-mono text-emerald-400 font-bold select-all">${escapeHtml(ip)}</span>
                  <button onclick="copyToClipboard('${ip}')" title="کپی IP" class="text-[10px] text-slate-400 hover:text-white px-1.5 py-0.5 rounded bg-slate-800">📋</button>
                </div>
              </td>
              <td class="py-2.5 px-3">${statusBadge}</td>
              <td class="py-2.5 px-3">${exitBadge}</td>
              <td class="py-2.5 px-3">
                <div class="flex items-center justify-center gap-1.5">
                  <button onclick="quickPingPeer('${ip}')" title="پینگ تاخیر شبکه" class="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[10px] flex items-center gap-1">
                    📶 پینگ
                  </button>
                  <button onclick="quickConnectPeerRdp('${ip}')" title="اتصال RDP به این نود" class="px-2 py-1 rounded bg-blue-600/30 hover:bg-blue-600/50 text-blue-300 text-[10px] flex items-center gap-1 font-bold">
                    🖥️ RDP
                  </button>
                </div>
              </td>
            </tr>
        `;
    }).join('');
}

function filterTailscalePeersTable() {
    const query = (document.getElementById('ts-peer-search')?.value || '').toLowerCase().trim();
    if (!query) {
        renderTailscalePeersTable(cachedTsPeers);
        return;
    }
    const filtered = cachedTsPeers.filter(p => {
        return (p.hostname || '').toLowerCase().includes(query) ||
               (p.dns_name || '').toLowerCase().includes(query) ||
               (p.tailscale_ip || '').toLowerCase().includes(query) ||
               (p.os || '').toLowerCase().includes(query);
    });
    renderTailscalePeersTable(filtered);
}

async function applyLocalExitNode() {
    const select = document.getElementById('ts-local-exit-node-select');
    const chosenIp = select ? select.value : '';
    try {
        const res = await callApi('tailscale_set_exit_node', 'local', chosenIp);
        if (res && res.success) {
            showToast(res.message || "تنظیم Exit Node اعمال شد", "success");
            await refreshTailscaleStatus(false);
        } else {
            showToast("خطا در اعمال Exit Node: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function toggleLocalTsPref(prefKey, value) {
    try {
        const res = await callApi('tailscale_set_preference', 'local', prefKey, value);
        if (res && res.success) {
            showToast("تنظیم تایل‌اسکیل ذخیره شد", "success");
        } else {
            showToast("خطا در تغییر تنظیم: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function triggerLocalTsUp() {
    const btn = document.getElementById('btn-ts-local-up');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال اتصال...";
    }
    try {
        const res = await callApi('tailscale_up', 'local');
        if (res && res.success) {
            showToast(res.message || "تایل‌اسکیل ویندوز متصل شد", "success");
            await refreshTailscaleStatus(false);
        } else {
            showToast("خطا در اتصال: " + (res.error || res.message || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "⚡ اتصال Tailscale ویندوز";
        }
    }
}

async function triggerLocalTsDown() {
    try {
        const res = await callApi('tailscale_down', 'local');
        if (res && res.success) {
            showToast(res.message || "اتصال تایل‌اسکیل قطع شد", "info");
            await refreshTailscaleStatus(false);
        } else {
            showToast("خطا: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function triggerRemoteTsUp() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }
    const btn = document.getElementById('btn-ts-remote-up');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال اتصال در سرور...";
    }
    try {
        const res = await callApi('tailscale_up', 'remote');
        if (res && res.success) {
            showToast(res.message || "تایل‌اسکیل سرور متصل شد", "success");
            await refreshTailscaleStatus(false);
        } else {
            showToast("خطا در اتصال سرور: " + (res.error || res.message || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "⚡ اتصال Tailscale لینوکس";
        }
    }
}

async function triggerRemoteTsDown() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }
    try {
        const res = await callApi('tailscale_down', 'remote');
        if (res && res.success) {
            showToast(res.message || "اتصال تایل‌اسکیل سرور قطع شد", "info");
            await refreshTailscaleStatus(false);
        } else {
            showToast("خطا: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function toggleRemoteExitNodeAdv() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }
    const isAdv = (cachedTsData && cachedTsData.remote && cachedTsData.remote.device && cachedTsData.remote.device.is_exit_node_option) || false;
    const newVal = !isAdv;
    try {
        const res = await callApi('tailscale_set_preference', 'remote', 'advertise-exit-node', newVal);
        if (res && res.success) {
            showToast(newVal ? "تبلیغ سرور به عنوان Exit Node فعال شد" : "تبلیغ Exit Node غیرفعال شد", "success");
            await refreshTailscaleStatus(false);
        } else {
            showToast("خطا: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function toggleRemoteTsSsh() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }
    const isSsh = (cachedTsData && cachedTsData.remote && cachedTsData.remote.preferences && cachedTsData.remote.preferences.ssh_enabled) || false;
    const newVal = !isSsh;
    try {
        const res = await callApi('tailscale_set_preference', 'remote', 'ssh', newVal);
        if (res && res.success) {
            showToast(newVal ? "Tailscale SSH روی سرور فعال شد" : "Tailscale SSH غیرفعال شد", "success");
            await refreshTailscaleStatus(false);
        } else {
            showToast("خطا: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    }
}

async function installTailscaleOnLinux() {
    if (!isConnected) {
        showToast("ابتدا به سرور لینوکس متصل شوید", "error");
        return;
    }
    const btn = document.getElementById('btn-ts-install-linux');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال نصب Tailscale (لطفاً صبور باشید)...";
    }
    try {
        const res = await callApi('tailscale_install_remote');
        if (res && res.success) {
            showToast(res.message || "Tailscale با موفقیت روی سرور نصب شد!", "success");
            await refreshTailscaleStatus(false);
        } else {
            showToast("خطا در نصب Tailscale: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "⚡ نصب خودکار با ۱ کلیک";
        }
    }
}

async function runTailscalePing(targetIp = null) {
    const input = document.getElementById('ts-ping-target');
    const resultBox = document.getElementById('ts-ping-result-box');
    const ip = targetIp || (input ? input.value.trim() : '');
    
    if (!ip) {
        showToast("آدرس IP یا نام نود برای پینگ را وارد کنید", "error");
        return;
    }

    if (input) input.value = ip;
    if (resultBox) {
        resultBox.classList.remove('hidden');
        resultBox.textContent = `در حال ارسال پینگ به ${ip} ...`;
    }

    const btn = document.getElementById('btn-ts-run-ping');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال پینگ...";
    }

    try {
        const res = await callApi('tailscale_ping_peer', ip, 3);
        if (res && res.success) {
            if (resultBox) resultBox.textContent = res.raw_output || res.summary;
            showToast(`پینگ موفق: ${res.summary || 'پاسخ دریافت شد'}`, "success");
        } else {
            if (resultBox) resultBox.textContent = res.raw_output || res.error || "خطا در دریافت پاسخ پینگ";
            showToast("خطا در پینگ: " + (res.error || res.summary || 'ناشناخته'), "error");
        }
    } catch (e) {
        if (resultBox) resultBox.textContent = "خطا: " + e.message;
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "ارسال پینگ (Ping)";
        }
    }
}

function quickPingPeer(ip) {
    const targetInput = document.getElementById('ts-ping-target');
    if (targetInput) targetInput.value = ip;
    runTailscalePing(ip);
}

function quickConnectPeerRdp(ip) {
    const rdpInput = document.getElementById('rdp-target-host');
    if (rdpInput) rdpInput.value = ip;
    switchTab('rdp');
    showToast(`آدرس RDP به نود ${ip} تنظیم شد`, "info");
}

// ----------------- TAILSCALE FUNNEL CONTROLLER -----------------
let currentFunnelUrl = "";

async function refreshTailscaleFunnelStatus(notify = false) {
    const badge = document.getElementById('ts-funnel-status-badge');
    const refreshBtn = document.getElementById('btn-ts-funnel-refresh');
    const targetDeviceSelect = document.getElementById('ts-funnel-target-device');
    const device = targetDeviceSelect ? targetDeviceSelect.value : 'remote';

    const activeBox = document.getElementById('ts-funnel-active-box');
    const configBox = document.getElementById('ts-funnel-config-box');
    const urlLink = document.getElementById('ts-funnel-url-link');
    const targetDisplay = document.getElementById('ts-funnel-target-display');
    const pubPortDisplay = document.getElementById('ts-funnel-public-port-display');

    if (refreshBtn) refreshBtn.disabled = true;
    if (badge) badge.textContent = "در حال بررسی...";

    try {
        const res = await callApi('tailscale_funnel_get_status', device);
        if (res && res.success) {
            if (res.active && res.public_url) {
                currentFunnelUrl = res.public_url;
                if (badge) {
                    badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono font-bold";
                    badge.textContent = `فعال روی اینترنت (${res.public_port})`;
                }
                if (activeBox) activeBox.classList.remove('hidden');
                if (configBox) configBox.classList.add('hidden');

                if (urlLink) {
                    urlLink.href = res.public_url;
                    urlLink.textContent = res.public_url;
                }
                if (targetDisplay) {
                    targetDisplay.textContent = res.target_port ? `Port ${res.target_port}` : (res.target_backend || 'Port 80');
                }
                if (pubPortDisplay) {
                    pubPortDisplay.textContent = `${res.public_port || 443} (HTTPS)`;
                }
            } else {
                currentFunnelUrl = "";
                if (badge) {
                    badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono";
                    badge.textContent = "غیرفعال (آفلاین)";
                }
                if (activeBox) activeBox.classList.add('hidden');
                if (configBox) configBox.classList.remove('hidden');
            }
            if (notify) showToast("وضعیت Tailscale Funnel بروزرسانی شد", "success");
        } else {
            if (badge) {
                badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 font-mono";
                badge.textContent = "خطا در اتصال";
            }
            if (notify && res && res.error) showToast("خطا: " + res.error, "error");
        }
    } catch (e) {
        if (badge) {
            badge.className = "text-xs px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 font-mono";
            badge.textContent = "خطا";
        }
        if (notify) showToast("خطا در بررسی Funnel: " + e.message, "error");
    } finally {
        if (refreshBtn) refreshBtn.disabled = false;
    }
}

function setFunnelPortPreset(port) {
    const input = document.getElementById('ts-funnel-local-port');
    if (input) {
        input.value = port;
        showToast(`پورت ورودی روی ${port} تنظیم شد`, "info");
    }
}

async function triggerStartFunnel() {
    const targetDeviceSelect = document.getElementById('ts-funnel-target-device');
    const localPortInput = document.getElementById('ts-funnel-local-port');
    const publicPortSelect = document.getElementById('ts-funnel-public-port');
    const btn = document.getElementById('btn-ts-funnel-start');

    const device = targetDeviceSelect ? targetDeviceSelect.value : 'remote';
    const localPort = parseInt(localPortInput ? localPortInput.value : 80) || 80;
    const publicPort = parseInt(publicPortSelect ? publicPortSelect.value : 443) || 443;

    if (!localPort || localPort < 1 || localPort > 65535) {
        showToast("لطفاً یک شماره پورت معتبر بین ۱ تا ۶۵۵۳۵ وارد کنید", "error");
        return;
    }

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="animate-spin">⏳</span> در حال فعال‌سازی Funnel...`;
    }

    try {
        showToast(`در حال فعال‌سازی Funnel برای پورت ${localPort}...`, "info");
        const res = await callApi('tailscale_funnel_start', device, localPort, publicPort);
        if (res && res.success) {
            showToast(res.message || "Tailscale Funnel فعال شد!", "success");
            await refreshTailscaleFunnelStatus(false);
        } else {
            showToast("خطا در فعال‌سازی Funnel: " + (res.error || 'ناشناخته'), "error");
            alert(`خطا در فعال‌سازی Tailscale Funnel:\n\n${res.error || 'نامشخص'}\n\nنکته: مطمئن شوید قابلیت Funnel در کنسول مدیریتی Tailscale (Tailscale Admin Console) برای حساب کاربری شما فعال باشد.`);
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<span>⚡</span> فعال‌سازی و انتشار روی اینترنت`;
        }
    }
}

async function triggerStopFunnel() {
    if (!confirm("آیا از متوقف‌سازی و بستن دسترسی اینترنتی Tailscale Funnel اطمینان دارید؟")) {
        return;
    }

    const targetDeviceSelect = document.getElementById('ts-funnel-target-device');
    const device = targetDeviceSelect ? targetDeviceSelect.value : 'remote';
    const btn = document.getElementById('btn-ts-funnel-stop');

    if (btn) btn.disabled = true;

    try {
        showToast("در حال توقف و ریست Funnel...", "info");
        const res = await callApi('tailscale_funnel_stop', device);
        if (res && res.success) {
            showToast(res.message || "Funnel متوقف شد", "success");
            await refreshTailscaleFunnelStatus(false);
        } else {
            showToast("خطا: " + (res.error || 'ناشناخته'), "error");
        }
    } catch (e) {
        showToast("خطا: " + e.message, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

function openFunnelInBrowser() {
    if (currentFunnelUrl) {
        window.open(currentFunnelUrl, '_blank');
    } else {
        showToast("آدرس فعالی یافت نشد", "error");
    }
}

function copyToClipboard(text) {
    if (!text || text === '--') return;
    navigator.clipboard.writeText(text).then(() => {
        showToast(`آدرس کپی شد: ${text}`, "success");
    }).catch(() => {
        showToast("خطا در کپی کردن", "error");
    });
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ----------------- WEB SERVER & DATABASE (LAMP) CONTROLLER -----------------
let currentEditingVhost = null;
let cachedWebStatus = null;
let cachedVhosts = [];
let cachedDatabases = [];

async function refreshWebServerData(force = false) {
    const headerBadge = document.getElementById('web-header-badge');
    const navBadge = document.getElementById('badge-web-status');
    const refreshBtn = document.getElementById('btn-web-refresh');

    if (refreshBtn) refreshBtn.disabled = true;
    if (headerBadge) headerBadge.textContent = "در حال دریافت اطلاعات...";

    try {
        const [statusRes, vhostsRes, dbsRes, phpRes] = await Promise.allSettled([
            callApi('web_get_status'),
            callApi('web_list_vhosts'),
            callApi('web_mysql_list_databases'),
            callApi('web_get_php_details')
        ]);

        const status = statusRes.status === 'fulfilled' ? statusRes.value : null;
        const vhostsData = vhostsRes.status === 'fulfilled' ? vhostsRes.value : null;
        const dbsData = dbsRes.status === 'fulfilled' ? dbsRes.value : null;
        const phpData = phpRes.status === 'fulfilled' ? phpRes.value : null;

        cachedWebStatus = status;

        // Render Service Statuses
        if (status && (status.success || status.apache || status.connected)) {
            const apache = status.apache || {};
            const mysql = status.mysql || {};
            const php = status.php || {};

            // Apache / Nginx Card
            const apPill = document.getElementById('web-apache-status-pill');
            const apVer = document.getElementById('web-apache-version');
            const apPorts = document.getElementById('web-apache-ports');
            const apTitle = document.getElementById('web-service-title');

            if (apTitle) apTitle.textContent = apache.service === 'nginx' ? 'وب‌سرور انجین‌اکس (Nginx)' : 'وب‌سرور آپاچی (Apache2)';
            if (apVer) apVer.textContent = apache.version ? `نسخه ${apache.version}` : (apache.installed ? 'نصب شده' : 'نصب نیست');
            if (apPill) {
                if (apache.running) {
                    apPill.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono font-bold";
                    apPill.textContent = "فعال و در حال اجرا";
                } else if (apache.installed) {
                    apPill.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 font-mono font-bold";
                    apPill.textContent = "متوقف شده";
                } else {
                    apPill.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono";
                    apPill.textContent = "نصب نشده";
                }
            }
            if (apPorts) {
                const ports = [];
                const pObj = status.ports || {};
                if (pObj.port_80 || (typeof pObj.raw === 'string' && pObj.raw.includes('80'))) ports.push("80 (HTTP)");
                if (pObj.port_443 || (typeof pObj.raw === 'string' && pObj.raw.includes('443'))) ports.push("443 (HTTPS)");
                apPorts.textContent = ports.length > 0 ? ports.join(" , ") : (apache.running ? "Port 80, 443" : "غیرفعال");
            }

            // MySQL / MariaDB Card
            const dbPill = document.getElementById('web-mysql-status-pill');
            const dbVer = document.getElementById('web-mysql-version');
            const dbTitle = document.getElementById('web-db-title');
            const dbPort = document.getElementById('web-mysql-port');
            const dbCount = document.getElementById('web-mysql-db-count');

            if (dbTitle) dbTitle.textContent = mysql.service === 'mariadb' ? 'پایگاه داده MariaDB' : 'پایگاه داده MySQL';
            if (dbVer) dbVer.textContent = mysql.version ? `نسخه ${mysql.version}` : (mysql.installed ? 'نصب شده' : 'نصب نیست');
            if (dbPill) {
                if (mysql.running) {
                    dbPill.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono font-bold";
                    dbPill.textContent = "فعال و در حال اجرا";
                } else if (mysql.installed) {
                    dbPill.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 font-mono font-bold";
                    dbPill.textContent = "متوقف شده";
                } else {
                    dbPill.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono";
                    dbPill.textContent = "نصب نشده";
                }
            }
            if (dbPort) {
                const pObj = status.ports || {};
                const is3306Active = pObj.port_3306 || (typeof pObj.raw === 'string' && pObj.raw.includes('3306')) || mysql.running;
                dbPort.textContent = is3306Active ? "Port 3306 (Active)" : "3306 (Inactive)";
            }

            // PHP Card
            const phpPill = document.getElementById('web-php-status-pill');
            const phpVer = document.getElementById('web-php-version');
            const phpMem = document.getElementById('web-php-memory');
            const phpUp = document.getElementById('web-php-upload');

            const pVer = (phpData && phpData.version) ? phpData.version : (php.version || (php.installed ? 'نصب شده' : 'نصب نیست'));
            if (phpVer) phpVer.textContent = pVer.startsWith('PHP') ? pVer : `نسخه ${pVer}`;
            if (phpPill) {
                if (php.installed || (phpData && phpData.installed)) {
                    phpPill.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono font-bold";
                    phpPill.textContent = "آماده به کار";
                } else {
                    phpPill.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono";
                    phpPill.textContent = "نصب نشده";
                }
            }
            if (phpMem && phpData) {
                phpMem.textContent = (phpData.ini_settings && phpData.ini_settings.memory_limit) || phpData.memory_limit || "128M";
            }
            if (phpUp && phpData) {
                phpUp.textContent = (phpData.ini_settings && phpData.ini_settings.upload_max_filesize) || phpData.upload_max_filesize || "2M";
            }

            // Header and Sidebar Badges
            const isFullyActive = apache.running && mysql.running;
            if (headerBadge) {
                if (isFullyActive) {
                    headerBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono font-bold";
                    headerBadge.textContent = "استک فعال و پایدار";
                } else if (apache.running || mysql.running) {
                    headerBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 font-mono font-bold";
                    headerBadge.textContent = "بخشی فعال";
                } else {
                    headerBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 font-mono font-bold";
                    headerBadge.textContent = "سرویس‌ها خاموش";
                }
            }
            if (navBadge) {
                navBadge.className = isFullyActive ? "mr-auto w-2 h-2 rounded-full bg-emerald-400" : (apache.running || mysql.running ? "mr-auto w-2 h-2 rounded-full bg-amber-400" : "mr-auto w-2 h-2 rounded-full bg-slate-600");
            }
        }

        // Render Virtual Hosts
        if (vhostsData && (vhostsData.success || Array.isArray(vhostsData.sites) || Array.isArray(vhostsData.vhosts))) {
            cachedVhosts = vhostsData.sites || vhostsData.vhosts || [];
            renderVhostsTable(cachedVhosts);
        } else {
            renderVhostsTable([]);
        }

        // Render Databases
        if (dbsData && dbsData.success) {
            cachedDatabases = dbsData.databases || [];
            renderDatabasesTable(cachedDatabases);
            const dbCount = document.getElementById('web-mysql-db-count');
            if (dbCount) dbCount.textContent = `${cachedDatabases.length} دیتابیس`;
        } else {
            renderDatabasesTable([]);
        }

        if (force) showToast("اطلاعات وب‌سرور و دیتابیس بروزرسانی شد", "success");

    } catch (e) {
        console.error("Error refreshing webserver data:", e);
        if (headerBadge) {
            headerBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 font-mono";
            headerBadge.textContent = "خطا در ارتباط";
        }
        showToast("خطا در دریافت وضعیت وب‌سرور: " + e.message, "error");
    } finally {
        if (refreshBtn) refreshBtn.disabled = false;
    }
}

async function controlWebService(service, action) {
    try {
        showToast(`در حال ارسال دستور ${action} برای سرویس ${service}...`, "info");
        const res = await callApi('web_service_action', service, action);
        if (res && res.success) {
            showToast(res.message || `سرویس ${service} با موفقیت ${action} شد`, "success");
            setTimeout(() => refreshWebServerData(false), 800);
        } else {
            showToast(res ? res.error : "خطا در اجرای عملیات", "error");
        }
    } catch (e) {
        showToast("خطا در کنترل سرویس: " + e.message, "error");
    }
}

async function testWebConfig(service = "apache2") {
    try {
        showToast("در حال تست صحت تنظیمات کانفیگ...", "info");
        const res = await callApi('web_test_config', service);
        const isValid = res && (res.valid === true || res.success === true);
        if (isValid) {
            const out = (res.output || 'Syntax OK').trim();
            showToast(`✅ تست کانفیگ موفقیت‌آمیز بود: ${out}`, "success");
            alert(`✅ نتیجه تست صحت کانفیگ وب‌سرور:\n\n${out}`);
        } else {
            let err = "";
            if (res && res.error) {
                err = res.error;
            } else if (res && res.output) {
                err = res.output;
            } else {
                err = "وب‌سرور روی این سرور لینوکس نصب نیست یا خطای ناشناخته رخ داده است.";
            }
            showToast(`⚠️ وضعیت کانفیگ: ${err}`, "error");
            alert(`گزارش وضعیت کانفیگ وب‌سرور:\n\n${err}`);
        }
    } catch (e) {
        showToast("خطا در تست کانفیگ: " + e.message, "error");
    }
}

async function installLampStack() {
    if (!confirm("آیا مایل به نصب خودکار پکیج‌های کامل LAMP (شامل Apache2, MySQL Server, PHP و اکستنشن‌های پرکاربرد) روی سیستم لینوکس هستید؟ این پروسه ممکن است ۱ الی ۲ دقیقه زمان ببرد.")) {
        return;
    }
    const btn = document.getElementById('btn-lamp-install');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="animate-spin">⏳</span> در حال نصب پکیج‌های LAMP...`;
    }
    try {
        showToast("فرآیند نصب بسته‌های LAMP در لینوکس آغاز شد...", "info");
        const res = await callApi('web_install_lamp_stack');
        if (res && res.success) {
            showToast(res.message || "پکیج‌های LAMP با موفقیت نصب و راه‌اندازی شدند!", "success");
            setTimeout(() => refreshWebServerData(true), 1500);
        } else {
            showToast(res ? res.error : "خطا در نصب LAMP", "error");
        }
    } catch (e) {
        showToast("خطا در نصب LAMP: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `⚡ نصب ۱ کلیک پکیج LAMP`;
        }
    }
}

function openDocRootInFileManager() {
    currentRemotePath = '/var/www/html';
    switchTab('files');
    if (typeof fetchDirectory === 'function') {
        fetchDirectory('/var/www/html');
    }
    showToast("پوشه ریشه وب‌سرور (/var/www/html) در مدیر فایل باز شد", "info");
}

// ----------------- VIRTUAL HOSTS (SITES) -----------------
function renderVhostsTable(vhosts) {
    const tbody = document.getElementById('web-vhosts-tbody');
    const badgeCount = document.getElementById('web-vhosts-count');
    if (!tbody) return;

    if (badgeCount) badgeCount.textContent = `${vhosts.length} سایت`;

    if (!vhosts || vhosts.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center py-8 text-slate-500">
                    هیچ هاست مجازی در سرور یافت نشد یا وب‌سرور نصب نیست.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = vhosts.map(v => {
        const isDefault = v.filename.includes('000-default') || v.filename.includes('default-ssl');
        const statusBadge = v.enabled
            ? `<span class="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-bold font-mono text-[10px]">فعال (a2ensite)</span>`
            : `<span class="px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono text-[10px]">غیرفعال</span>`;

        const toggleBtnText = v.enabled ? 'غیرفعال‌سازی' : 'فعال‌سازی';
        const toggleBtnClass = v.enabled ? 'text-amber-400' : 'text-emerald-400';

        return `
            <tr class="hover:bg-white/[0.02] transition-colors">
                <td class="py-3 px-3 font-mono text-slate-200 font-bold text-xs">
                    ${escapeHtml(v.filename)}
                </td>
                <td class="py-3 px-3 font-mono text-sky-400 font-bold text-xs">
                    ${escapeHtml(v.server_name)}
                </td>
                <td class="py-3 px-3 font-mono text-slate-300 text-xs">
                    :${escapeHtml((v.port && !String(v.port).includes('\\')) ? v.port : (v.filename.includes('ssl') ? '443' : '80'))}
                </td>
                <td class="py-3 px-3 font-mono text-slate-300 text-xs truncate max-w-xs" title="${escapeHtml(v.document_root)}">
                    <span class="hover:underline cursor-pointer text-emerald-400" onclick="openCustomPathInFileManager('${escapeHtml(v.document_root)}')">
                        📁 ${escapeHtml(v.document_root)}
                    </span>
                </td>
                <td class="py-3 px-3">
                    ${statusBadge}
                </td>
                <td class="py-3 px-3 text-center">
                    <div class="flex items-center justify-center gap-1.5">
                        <button onclick="toggleVhost('${escapeHtml(v.filename)}', ${!v.enabled})" class="btn btn-secondary text-[11px] px-2.5 py-1 ${toggleBtnClass} font-bold">
                            ${toggleBtnText}
                        </button>
                        <button onclick="editVhost('${escapeHtml(v.filename)}')" class="btn btn-secondary text-[11px] px-2.5 py-1 text-slate-300 font-bold hover:text-white">
                            📝 کانفیگ
                        </button>
                        ${!isDefault ? `
                            <button onclick="deleteVhost('${escapeHtml(v.filename)}')" class="btn btn-secondary text-[11px] px-2 py-1 text-rose-400 hover:bg-rose-500/20 font-bold" title="حذف هاست">
                                🗑️
                            </button>
                        ` : ''}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function openCustomPathInFileManager(path) {
    if (!path || path === '-') return;
    currentRemotePath = path;
    switchTab('files');
    if (typeof fetchDirectory === 'function') {
        fetchDirectory(path);
    }
}

function openCreateVhostModal() {
    const domainInput = document.getElementById('vhost-input-domain');
    const portInput = document.getElementById('vhost-input-port');
    const docrootInput = document.getElementById('vhost-input-docroot');
    const adminInput = document.getElementById('vhost-input-admin');

    if (domainInput) domainInput.value = '';
    if (portInput) portInput.value = '80';
    if (docrootInput) docrootInput.value = '';
    if (adminInput) adminInput.value = 'webmaster@localhost';

    openModal('modal-create-vhost');
}

async function submitCreateVhost() {
    const domain = document.getElementById('vhost-input-domain').value.trim();
    const port = parseInt(document.getElementById('vhost-input-port').value) || 80;
    const docroot = document.getElementById('vhost-input-docroot').value.trim();
    const admin = document.getElementById('vhost-input-admin').value.trim();

    if (!domain) {
        showToast("لطفاً نام دامنه یا هاست را وارد کنید", "error");
        return;
    }

    const btn = document.getElementById('btn-submit-create-vhost');
    if (btn) btn.disabled = true;

    try {
        showToast(`در حال ایجاد هاست مجازی برای دامنه ${domain}...`, "info");
        const res = await callApi('web_create_vhost', domain, port, docroot, admin);
        if (res && res.success) {
            closeModal('modal-create-vhost');
            showToast(res.message || `هاست مجازی ${domain} با موفقیت ساخته و فعال شد!`, "success");
            setTimeout(() => refreshWebServerData(false), 800);
        } else {
            showToast(res ? res.error : "خطا در ایجاد هاست مجازی", "error");
        }
    } catch (e) {
        showToast("خطا در ارسال درخواست: " + e.message, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function toggleVhost(siteFile, enable) {
    try {
        const actionText = enable ? "فعال‌سازی" : "غیرفعال‌سازی";
        showToast(`در حال ${actionText} سایت ${siteFile}...`, "info");
        const res = await callApi('web_toggle_vhost', siteFile, enable);
        if (res && res.success) {
            showToast(res.message || `سایت ${siteFile} با موفقیت ${actionText} شد`, "success");
            setTimeout(() => refreshWebServerData(false), 600);
        } else {
            showToast(res ? res.error : "خطا در تغییر وضعیت هاست", "error");
        }
    } catch (e) {
        showToast("خطا در تغییر وضعیت هاست: " + e.message, "error");
    }
}

async function editVhost(siteFile) {
    currentEditingVhost = siteFile;
    const title = document.getElementById('vhost-edit-name');
    const textarea = document.getElementById('vhost-edit-textarea');

    if (title) title.textContent = siteFile;
    if (textarea) textarea.value = "در حال خواندن محتوای فایل کانفیگ...";

    openModal('modal-edit-vhost');

    try {
        const res = await callApi('web_get_vhost_content', siteFile);
        if (res && res.success && textarea) {
            textarea.value = res.content || '';
        } else {
            if (textarea) textarea.value = "خطا در خواندن فایل کانفیگ: " + (res ? res.error : "نامشخص");
        }
    } catch (e) {
        if (textarea) textarea.value = "خطا: " + e.message;
    }
}

async function saveVhostConfig() {
    if (!currentEditingVhost) return;
    const textarea = document.getElementById('vhost-edit-textarea');
    const content = textarea ? textarea.value : '';

    const btn = document.getElementById('btn-save-vhost');
    if (btn) btn.disabled = true;

    try {
        showToast(`در حال ذخیره کانفیگ ${currentEditingVhost}...`, "info");
        const res = await callApi('web_save_vhost_content', currentEditingVhost, content);
        if (res && res.success) {
            closeModal('modal-edit-vhost');
            showToast(res.message || "فایل کانفیگ ذخیره شد و وب‌سرور بارگذاری مجدد گردید!", "success");
            setTimeout(() => refreshWebServerData(false), 800);
        } else {
            showToast(res ? res.error : "خطا در ذخیره کانفیگ", "error");
        }
    } catch (e) {
        showToast("خطا در ذخیره کانفیگ: " + e.message, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function deleteVhost(siteFile) {
    if (!confirm(`آیا از حذف هاست مجازی ${siteFile} اطمینان دارید؟ این فایل کانفیگ به صورت دائمی حذف خواهد شد.`)) {
        return;
    }
    try {
        showToast(`در حال حذف ${siteFile}...`, "info");
        const res = await callApi('web_delete_vhost', siteFile);
        if (res && res.success) {
            showToast(res.message || `هاست ${siteFile} با موفقیت حذف شد`, "success");
            setTimeout(() => refreshWebServerData(false), 600);
        } else {
            showToast(res ? res.error : "خطا در حذف هاست", "error");
        }
    } catch (e) {
        showToast("خطا در حذف هاست: " + e.message, "error");
    }
}

// ----------------- MYSQL DATABASES -----------------
function renderDatabasesTable(databases) {
    const tbody = document.getElementById('web-databases-tbody');
    const badgeCount = document.getElementById('web-databases-count');
    const dbSelect = document.getElementById('sql-db-select');

    if (badgeCount) badgeCount.textContent = `${databases.length} دیتابیس`;

    // Populate SQL target db select
    if (dbSelect) {
        const prevVal = dbSelect.value;
        dbSelect.innerHTML = '<option value="">بدون انتخاب پایگاه داده (کوئری عمومی سرور)</option>';
        databases.forEach(db => {
            const opt = document.createElement('option');
            opt.value = db.name;
            opt.textContent = `${db.name} (${db.size_mb || 0} MB)`;
            dbSelect.appendChild(opt);
        });
        if (prevVal) dbSelect.value = prevVal;
    }

    if (!tbody) return;

    if (!databases || databases.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="4" class="text-center py-8 text-slate-500">
                    هیچ پایگاه داده‌ای در MySQL یافت نشد یا سرویس خاموش است.
                </td>
            </tr>
        `;
        return;
    }

    const systemDbs = ['information_schema', 'mysql', 'performance_schema', 'sys'];

    tbody.innerHTML = databases.map(db => {
        const isSystem = systemDbs.includes(db.name.toLowerCase());
        const sizeFormatted = db.size_mb !== undefined ? `${db.size_mb} MB` : '-';
        const tablesFormatted = db.tables_count !== undefined ? `${db.tables_count} جدول` : '-';

        return `
            <tr class="hover:bg-white/[0.02] transition-colors">
                <td class="py-3 px-3">
                    <div class="flex items-center gap-2">
                        <span class="text-amber-400 font-bold">🗄️</span>
                        <span class="font-mono text-slate-200 font-bold text-xs">${escapeHtml(db.name)}</span>
                        ${isSystem ? `<span class="text-[9px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 font-mono">System</span>` : ''}
                    </div>
                </td>
                <td class="py-3 px-3 font-mono text-slate-300 text-xs">
                    ${tablesFormatted}
                </td>
                <td class="py-3 px-3 font-mono text-emerald-400 font-bold text-xs">
                    ${sizeFormatted}
                </td>
                <td class="py-3 px-3 text-center">
                    <div class="flex items-center justify-center gap-2">
                        <button onclick="backupDatabase('${escapeHtml(db.name)}')" class="btn btn-secondary text-[11px] px-2.5 py-1 text-sky-400 hover:text-white font-bold" title="پشتیبان‌گیری سریع">
                            💾 بکاپ (.sql)
                        </button>
                        ${!isSystem ? `
                            <button onclick="dropDatabase('${escapeHtml(db.name)}')" class="btn btn-secondary text-[11px] px-2.5 py-1 text-rose-400 hover:bg-rose-500/20 font-bold" title="حذف پایگاه داده">
                                🗑️ حذف
                            </button>
                        ` : `
                            <span class="text-[10px] text-slate-600 px-2 py-1">محافظت‌شده</span>
                        `}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function openCreateDbModal() {
    const nameInput = document.getElementById('db-input-name');
    if (nameInput) nameInput.value = '';
    openModal('modal-create-db');
}

async function submitCreateDb() {
    const nameInput = document.getElementById('db-input-name');
    const collationSelect = document.getElementById('db-input-collation');

    const name = nameInput ? nameInput.value.trim() : '';
    const collation = collationSelect ? collationSelect.value : 'utf8mb4_unicode_ci';

    if (!name) {
        showToast("لطفاً نام پایگاه داده را وارد کنید", "error");
        return;
    }

    const btn = document.getElementById('btn-submit-create-db');
    if (btn) btn.disabled = true;

    try {
        showToast(`در حال ساخت دیتابیس ${name}...`, "info");
        const res = await callApi('web_mysql_create_database', name, collation);
        if (res && res.success) {
            closeModal('modal-create-db');
            showToast(res.message || `دیتابیس ${name} با موفقیت ساخته شد!`, "success");
            setTimeout(() => refreshWebServerData(false), 800);
        } else {
            showToast(res ? res.error : "خطا در ساخت دیتابیس", "error");
        }
    } catch (e) {
        showToast("خطا در ایجاد پایگاه داده: " + e.message, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function dropDatabase(dbName) {
    if (!confirm(`⚠️ هشدار جدی:\nآیا از حذف دیتابیس "${dbName}" اطمینان کامل دارید؟\nتمام جداول و رکوردهای موجود در این دیتابیس برای همیشه پاک خواهند شد.`)) {
        return;
    }

    try {
        showToast(`در حال حذف پایگاه داده ${dbName}...`, "info");
        const res = await callApi('web_mysql_drop_database', dbName);
        if (res && res.success) {
            showToast(res.message || `دیتابیس ${dbName} حذف شد`, "success");
            setTimeout(() => refreshWebServerData(false), 800);
        } else {
            showToast(res ? res.error : "خطا در حذف دیتابیس", "error");
        }
    } catch (e) {
        showToast("خطا در حذف دیتابیس: " + e.message, "error");
    }
}

async function backupDatabase(dbName) {
    try {
        showToast(`در حال تهیه پشتیبان mysqldump از دیتابیس ${dbName}...`, "info");
        const res = await callApi('web_mysql_backup_database', dbName);
        if (res && res.success) {
            showToast(`✅ بکاپ در مسیر ${res.backup_file} ذخیره شد (${res.size_formatted})`, "success");
        } else {
            showToast(res ? res.error : "خطا در پشتیبان‌گیری", "error");
        }
    } catch (e) {
        showToast("خطا در پشتیبان‌گیری: " + e.message, "error");
    }
}

// ----------------- SQL QUERY CONSOLE -----------------
function openSqlQueryModal() {
    const input = document.getElementById('sql-query-input');
    if (input && !input.value.trim()) {
        input.value = "SHOW DATABASES;";
    }
    openModal('modal-sql-console');
}

async function executeSqlQuery() {
    const queryInput = document.getElementById('sql-query-input');
    const dbSelect = document.getElementById('sql-db-select');
    const resultBox = document.getElementById('sql-result-container');
    const metaBox = document.getElementById('sql-result-meta');
    const runBtn = document.getElementById('btn-run-sql');

    const query = queryInput ? queryInput.value.trim() : '';
    const db = dbSelect ? dbSelect.value : null;

    if (!query) {
        showToast("لطفاً یک دستور SQL وارد کنید", "error");
        return;
    }

    if (runBtn) {
        runBtn.disabled = true;
        runBtn.textContent = "⏳ در حال اجرا...";
    }
    if (metaBox) metaBox.textContent = "در حال اجرا روی سرور...";
    if (resultBox) resultBox.innerHTML = '<div class="text-slate-500 font-mono">در حال اجرای کوئری...</div>';

    try {
        const res = await callApi('web_mysql_execute_query', query, db);
        if (res && res.success) {
            if (metaBox) {
                metaBox.textContent = `انجام شد (${res.rows_count} سطر | نوع: ${res.query_type})`;
            }

            if (res.data && res.data.length > 0) {
                const cols = Object.keys(res.data[0]);
                let tableHtml = `
                    <div class="overflow-x-auto">
                        <table class="w-full text-right text-xs">
                            <thead class="bg-slate-900 text-slate-300 font-mono text-[11px] border-b border-white/10">
                                <tr>
                                    ${cols.map(c => `<th class="p-2">${escapeHtml(c)}</th>`).join('')}
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-white/5">
                                ${res.data.map(row => `
                                    <tr class="hover:bg-white/[0.02]">
                                        ${cols.map(c => `<td class="p-2 font-mono text-slate-200">${escapeHtml(row[c] !== null ? row[c] : 'NULL')}</td>`).join('')}
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                `;
                if (resultBox) resultBox.innerHTML = tableHtml;
            } else {
                if (resultBox) {
                    resultBox.innerHTML = `<div class="text-emerald-400 font-mono">✅ کوئری با موفقیت اجرا شد (سطری برگردانده نشد).</div>`;
                }
            }
        } else {
            if (metaBox) metaBox.textContent = "خطا در اجرا";
            if (resultBox) {
                resultBox.innerHTML = `<div class="text-rose-400 font-mono">❌ خطا: ${escapeHtml(res ? res.error : "نامشخص")}</div>`;
            }
        }
    } catch (e) {
        if (metaBox) metaBox.textContent = "خطا";
        if (resultBox) {
            resultBox.innerHTML = `<div class="text-rose-400 font-mono">❌ خطا: ${escapeHtml(e.message)}</div>`;
        }
    } finally {
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.textContent = "▶️ اجرای کوئری (Run)";
        }
    }
}

// ----------------- WEB LOGS MODAL -----------------
function openWebLogsModal() {
    openModal('modal-web-logs');
    fetchWebLogs();
}

async function fetchWebLogs() {
    const select = document.getElementById('web-log-type-select');
    const pre = document.getElementById('web-logs-content');
    const logType = select ? select.value : 'apache_error';

    if (pre) pre.textContent = "در حال دریافت لاگ‌ها...";

    try {
        const res = await callApi('web_get_logs', logType, 100);
        if (res && res.success && pre) {
            pre.textContent = res.content || 'فایل لاگ خالی است.';
        } else {
            if (pre) pre.textContent = "خطا در دریافت لاگ: " + (res ? res.error : "نامشخص");
        }
    } catch (e) {
        if (pre) pre.textContent = "خطا: " + e.message;
    }
}

// ----------------- MYSQL DATABASE RESTORE & USER MANAGEMENT -----------------
async function openRestoreDbModal() {
    if (!isConnected) {
        showToast("ابتدا به سرور متصل شوید", "error");
        return;
    }
    const select = document.getElementById('restore-db-select');
    if (select) {
        select.innerHTML = '<option value="">-- در حال دریافت دیتابیس‌ها... --</option>';
        try {
            const res = await callApi('web_mysql_list_databases');
            if (res && res.success && res.databases) {
                const nonSys = res.databases.filter(d => !d.is_system);
                select.innerHTML = nonSys.length > 0
                    ? nonSys.map(d => `<option value="${escapeHtml(d.name)}">${escapeHtml(d.name)}</option>`).join('')
                    : res.databases.map(d => `<option value="${escapeHtml(d.name)}">${escapeHtml(d.name)}</option>`).join('');
            }
        } catch (e) {}
    }
    openModal('modal-restore-db');
}

async function submitRestoreDatabase() {
    const select = document.getElementById('restore-db-select');
    const fileInput = document.getElementById('restore-db-filepath');
    const db = select ? select.value.trim() : '';
    const path = fileInput ? fileInput.value.trim() : '';

    if (!db) {
        showToast("لطفاً دیتابیس مقصد را انتخاب کنید", "error");
        return;
    }
    if (!path) {
        showToast("لطفاً مسیر فایل SQL بکاپ را وارد نمایید", "error");
        return;
    }

    const btn = document.getElementById('btn-submit-restore-db');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال بازیابی فایل...";
    }

    try {
        const res = await callApi('web_mysql_restore_database', db, path);
        if (res && res.success) {
            closeModal('modal-restore-db');
            showToast(res.message || `دیتابیس ${db} با موفقیت بازیابی شد!`, "success");
            setTimeout(() => refreshWebServerData(false), 800);
        } else {
            showToast(res ? res.error : "خطا در بازیابی دیتابیس", "error");
        }
    } catch (e) {
        showToast("خطای سیستم در بازیابی: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "شروع بازیابی (Restore)";
        }
    }
}

async function openMysqlUsersModal() {
    if (!isConnected) {
        showToast("ابتدا به سرور متصل شوید", "error");
        return;
    }
    openModal('modal-mysql-users');
    loadMysqlUsersList();

    // Populate db grant select
    const grantSelect = document.getElementById('new-db-user-grant');
    if (grantSelect) {
        grantSelect.innerHTML = '<option value="">بدون دیتابیس خاص</option>';
        try {
            const res = await callApi('web_mysql_list_databases');
            if (res && res.success && res.databases) {
                res.databases.forEach(d => {
                    grantSelect.innerHTML += `<option value="${escapeHtml(d.name)}">دسترسی کامل به ${escapeHtml(d.name)}</option>`;
                });
            }
        } catch (e) {}
    }
}

async function loadMysqlUsersList() {
    const tbody = document.getElementById('mysql-users-tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="2" class="text-center py-4 text-slate-500">در حال دریافت لیست کاربران...</td></tr>';

    try {
        const res = await callApi('web_mysql_list_users');
        if (res && res.success && res.users) {
            if (res.users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="2" class="text-center py-4 text-slate-500">کاربری یافت نشد.</td></tr>';
                return;
            }
            tbody.innerHTML = res.users.map(u => `
                <tr class="hover:bg-slate-800/40">
                  <td class="py-2 px-3 text-white font-bold flex items-center gap-1.5">
                    <span>👤</span> <span>${escapeHtml(u.user)}</span>
                  </td>
                  <td class="py-2 px-3 text-slate-400 font-mono">${escapeHtml(u.host)}</td>
                </tr>
            `).join('');
        } else {
            tbody.innerHTML = `<tr><td colspan="2" class="text-center py-4 text-rose-400">${escapeHtml(res ? res.error : "خطا در دریافت کاربران")}</td></tr>`;
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="2" class="text-center py-4 text-rose-400">خطا: ${escapeHtml(e.message)}</td></tr>`;
    }
}

async function submitCreateMysqlUser() {
    const nameInput = document.getElementById('new-db-user-name');
    const passInput = document.getElementById('new-db-user-pass');
    const hostInput = document.getElementById('new-db-user-host');
    const grantSelect = document.getElementById('new-db-user-grant');

    const name = nameInput ? nameInput.value.trim() : '';
    const pass = passInput ? passInput.value : '';
    const host = hostInput ? hostInput.value.trim() || 'localhost' : 'localhost';
    const grant = grantSelect ? grantSelect.value : null;

    if (!name) {
        showToast("نام کاربری الزامی است", "error");
        return;
    }
    if (!pass) {
        showToast("کلمه عبور نمی‌تواند خالی باشد", "error");
        return;
    }

    try {
        const res = await callApi('web_mysql_create_user', name, pass, host, grant);
        if (res && res.success) {
            showToast(res.message || `کاربر ${name} با موفقیت ایجاد شد!`, "success");
            if (nameInput) nameInput.value = '';
            if (passInput) passInput.value = '';
            loadMysqlUsersList();
        } else {
            showToast(res ? res.error : "خطا در ساخت کاربر", "error");
        }
    } catch (e) {
        showToast("خطا در ساخت کاربر: " + e.message, "error");
    }
}

// Mock fallback for browser dev
function mockApiResponse(method, args) {
    if (method === 'web_mysql_list_users') return { success: true, users: [{ user: 'root', host: 'localhost' }, { user: 'app_user', host: '%' }] };
    if (method === 'web_mysql_create_user') return { success: true, message: 'کاربر دیتابیس با موفقیت ساخته شد' };
    if (method === 'web_mysql_restore_database') return { success: true, message: 'دیتابیس با موفقیت بازیابی شد' };
    if (method === 'get_servers') return [{ id: 'srv1', name: 'لینوکس مینت', host: '100.85.120.45', port: 22, username: 'aboalfzl' }];
    if (method === 'get_server_details') return { id: args[0], name: 'لینوکس مینت', host: '100.85.120.45', port: 22, username: 'aboalfzl', mac_address: 'd8:5e:d3:11:22:33' };
    if (method === 'get_connection_info') return { connected: false };
    if (method === 'test_connection_params') return { success: true, latency_ms: 22 };
    if (method === 'file_list') return { success: true, current_path: '/home/aboalfzl', items: [{ name: 'projects', is_dir: true, size: 0, size_formatted: '-', mtime: 1700000000, mtime_formatted: '2026-10-12 12:00', permissions: '755' }] };
    if (method === 'rdp_check_status') return { connected: true, installed: true, running: true, host: '100.85.120.45', port: 3389, username: 'aboalfzl', local_user_active: false };
    if (method === 'rdp_launch_client') return { success: true, message: 'کلاینت ریموت دسکتاپ ویندوز باز شد' };
    if (method === 'rdp_setup_service') return { success: true, message: 'سرویس xrdp با موفقیت برای میزکار Cinnamon کانفیگ شد' };
    if (method === 'rdp_restart_service') return { success: true, message: 'سرویس xrdp با موفقیت ری‌استارت شد' };
    if (method === 'rdp_install_xfce') return { success: true, message: 'میزکار سبک XFCE با موفقیت نصب و تنظیم شد!' };
    if (method === 'tailscale_get_status') {
        return {
            local: {
                running: true,
                installed: true,
                tailnet_name: 'aboalfzl-vpn.ts.net',
                user: { display_name: 'aboalfzl', email: 'aboalfzl@gmail.com' },
                device: { hostname: 'DESKTOP-WIN11', tailscale_ip: '100.85.120.10', os: 'windows' },
                exit_nodes: [{ hostname: 'linux-mint-server', ip: '100.85.120.45', online: true, active: false }],
                peers: [
                    { hostname: 'linux-mint-server', os: 'linux', tailscale_ip: '100.85.120.45', online: true, is_exit_node_option: true, is_active_exit_node: false },
                    { hostname: 'galaxy-s24', os: 'android', tailscale_ip: '100.85.120.88', online: true, is_exit_node_option: false, is_active_exit_node: false }
                ],
                preferences: { shields_up: false, accept_routes: true }
            },
            remote: {
                running: true,
                installed: true,
                device: { hostname: 'linux-mint-server', tailscale_ip: '100.85.120.45', is_exit_node_option: true },
                preferences: { ssh_enabled: true }
            }
        };
    }
    if (method === 'tailscale_ping_peer') return { success: true, summary: 'pong from 100.85.120.45 via DERP(fra) in 42ms' };
    if (method === 'tailscale_set_exit_node') return { success: true, message: 'Exit node تغییر یافت' };
    if (method === 'tailscale_set_preference') return { success: true, message: 'تنظیم اعمال شد' };
    if (method === 'tailscale_up') return { success: true, message: 'Tailscale متصل شد' };
    if (method === 'tailscale_down') return { success: true, message: 'Tailscale قطع شد' };
    if (method === 'web_get_status') {
        return {
            success: true,
            apache: { installed: true, running: true, version: 'Apache/2.4.52 (Ubuntu)', service: 'apache2' },
            mysql: { installed: true, running: true, version: 'mysql  Ver 8.0.36-0ubuntu0.22.04.1', service: 'mysql' },
            php: { installed: true, version: 'PHP 8.1.2-1ubuntu2.14' },
            ports: { port_80: true, port_443: false, port_3306: true },
            docroot: { exists: true, size: '24M' }
        };
    }
    if (method === 'web_list_vhosts') {
        return {
            success: true,
            vhosts: [
                { filename: '000-default.conf', server_name: 'localhost', port: '80', document_root: '/var/www/html', enabled: true },
                { filename: 'mysite.conf', server_name: 'mysite.local', port: '80', document_root: '/var/www/mysite.local', enabled: true },
                { filename: 'api.conf', server_name: 'api.example.com', port: '8080', document_root: '/var/www/api', enabled: false }
            ]
        };
    }
    if (method === 'web_mysql_list_databases') {
        return {
            success: true,
            databases: [
                { name: 'information_schema', tables_count: 78, size_mb: 0.16 },
                { name: 'mysql', tables_count: 37, size_mb: 2.45 },
                { name: 'performance_schema', tables_count: 110, size_mb: 0.0 },
                { name: 'sys', tables_count: 101, size_mb: 0.02 },
                { name: 'wordpress_db', tables_count: 12, size_mb: 18.7 },
                { name: 'ecommerce_app', tables_count: 24, size_mb: 42.1 }
            ]
        };
    }
    if (method === 'web_get_php_details') {
        return {
            success: true,
            installed: true,
            version: 'PHP 8.1.2-1ubuntu2.14',
            ini_settings: {
                memory_limit: '256M',
                upload_max_filesize: '64M',
                post_max_size: '64M',
                max_execution_time: '60'
            },
            extensions: ['Core', 'date', 'libxml', 'openssl', 'pcre', 'zlib', 'curl', 'gd', 'mbstring', 'mysqli', 'pdo', 'pdo_mysql']
        };
    }
    if (method === 'web_service_action') return { success: true, message: `عملیات روی سرویس با موفقیت انجام شد` };
    if (method === 'web_test_config') return { valid: true, output: 'Syntax OK' };
    if (method === 'web_toggle_vhost') return { success: true, message: 'وضعیت هاست مجازی بروز شد' };
    if (method === 'web_create_vhost') return { success: true, message: 'هاست مجازی جدید ساخته شد' };
    if (method === 'web_delete_vhost') return { success: true, message: 'هاست مجازی حذف شد' };
    if (method === 'web_get_vhost_content') return { success: true, content: '<VirtualHost *:80>\n    ServerName mysite.local\n    DocumentRoot /var/www/mysite.local\n    ErrorLog ${APACHE_LOG_DIR}/mysite_error.log\n    CustomLog ${APACHE_LOG_DIR}/mysite_access.log combined\n</VirtualHost>' };
    if (method === 'web_save_vhost_content') return { success: true, message: 'فایل کانفیگ ذخیره شد' };
    if (method === 'web_mysql_create_database') return { success: true, message: 'پایگاه داده ایجاد گردید' };
    if (method === 'web_mysql_drop_database') return { success: true, message: 'پایگاه داده حذف گردید' };
    if (method === 'web_mysql_backup_database') return { success: true, backup_file: '/var/backups/mysqldump_wordpress_db_20261013.sql', size_formatted: '18.7 MB' };
    if (method === 'web_mysql_execute_query') return { success: true, rows_count: 2, query_type: 'SELECT', data: [{ Database: 'wordpress_db' }, { Database: 'ecommerce_app' }] };
    if (method === 'web_install_lamp_stack') return { success: true, message: 'پکیج‌های LAMP با موفقیت نصب شدند' };
    if (method === 'web_get_logs') return { success: true, content: '[Tue Oct 13 14:20:01 2026] [mpm_event:notice] [pid 1234:tid 1402] AH00489: Apache/2.4.52 (Ubuntu) configured -- resuming normal operations\n[Tue Oct 13 14:20:01 2026] [core:notice] [pid 1234:tid 1402] AH00094: Command line: \'/usr/sbin/apache2\'' };
    if (method === 'tailscale_funnel_get_status') {
        return {
            success: true,
            installed: true,
            active: false,
            public_url: "",
            public_port: 443,
            target_port: "80",
            target_backend: "http://127.0.0.1:80",
            allow_funnel: false
        };
    }
    if (method === 'tailscale_funnel_start') {
        return {
            success: true,
            message: "Tailscale Funnel فعال شد!",
            public_url: "https://linux-mint-server.aboalfzl-vpn.ts.net",
            status: { active: true, public_url: "https://linux-mint-server.aboalfzl-vpn.ts.net", public_port: 443, target_port: "80" }
        };
    }
    if (method === 'docker_get_download_queue') {
        return {
            success: true,
            tasks: [],
            active_task: null,
            queued_count: 0,
            downloading_count: 0,
            completed_count: 0,
            failed_count: 0,
            total_count: 0
        };
    }
    if (method === 'docker_queue_pull') {
        return { success: true, message: `ایمیج ${args[0]} به صف دانلود لینوکس اضافه شد` };
    }
    if (method === 'docker_get_container_details') {
        return {
            success: true,
            container: {
                id: (args[0] || '1234567890ab').slice(0, 12),
                full_id: args[0] || '1234567890abcdef',
                name: 'my-container',
                image: 'nginx:alpine',
                state: 'running',
                restart_policy: 'unless-stopped',
                ports: [{ host_port: '8080', container_port: '80', protocol: 'tcp' }],
                env_vars: 'DEBUG=false'
            }
        };
    }
    if (method === 'docker_edit_container') {
        return { success: true, message: `کانتینر با موفقیت بروزرسانی شد` };
    }
    return { success: true };
}
