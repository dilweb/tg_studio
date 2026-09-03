/**
 * Dashboard screen component.
 * Список проектов мастера.
 */

const STATUS_LABELS = {
    planned: 'Запланирован',
    in_progress: 'В работе',
    completed: 'Завершён',
    cancelled: 'Отменён',
};

function renderDashboard() {
    const app = document.getElementById('screen-dashboard');
    const { master } = getState();

    app.innerHTML = `
        <div class="screen-header">
            <div>
                <h1>📋 Проекты</h1>
                <div class="subtitle">${master?.master_name || 'Мастер'}</div>
            </div>
            <button class="btn-ghost" id="logout-btn">Выйти</button>
        </div>
        <div id="projects-list"></div>
        <button class="fab" id="create-fab">+</button>
    `;

    document.getElementById('logout-btn').addEventListener('click', () => {
        resetAuth();
        switchScreen('login');
    });

    document.getElementById('create-fab').addEventListener('click', () => {
        showModal('create');
    });

    app.classList.remove('hidden');
    loadProjects();
}

async function loadProjects() {
    const list = document.getElementById('projects-list');
    list.innerHTML = '<div class="spinner">Загрузка</div>';

    try {
        const data = await apiRequest('/projects');
        if (!data) return;

        setProjects(data.projects || []);
        renderProjects(data.projects || []);
    } catch (e) {
        list.innerHTML = `<div class="error">${e.message}</div>`;
    }
}

function renderProjects(projects) {
    const list = document.getElementById('projects-list');

    if (projects.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <div class="icon">📝</div>
                <strong>Нет проектов</strong>
                <p>Нажмите + чтобы создать первый проект</p>
            </div>
        `;
        return;
    }

    list.innerHTML = projects.map(p => `
        <div class="project-card" data-id="${p.id}">
            <div class="top">
                <div>
                    <strong>${escapeHtml(p.placement)}</strong>
                    <span class="status-badge status-${p.status}">${STATUS_LABELS[p.status] || p.status}</span>
                </div>
                <div class="cost">${Number(p.cost).toLocaleString()} ₸</div>
            </div>
            <div class="meta">
                <span>📏 ${escapeHtml(p.size)}</span>
                <span>🔧 ${escapeHtml(p.complexity)}</span>
            </div>
            <div class="date">📅 ${formatDate(p.session_date)}</div>
        </div>
    `).join('');

    list.querySelectorAll('.project-card').forEach(card => {
        card.addEventListener('click', () => {
            const id = parseInt(card.dataset.id, 10);
            const project = getState().projects.find(p => p.id === id);
            if (project) showModal('edit', project);
        });
    });
}

function formatDate(iso) {
    return new Date(iso).toLocaleString('ru-RU', {
        day: 'numeric',
        month: 'long',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}