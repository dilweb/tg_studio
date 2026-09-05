/**
 * Modal component for creating / editing projects.
 * Универсальная модалка для CRUD.
 */

let currentProjectId = null;

function showModal(mode, project = null) {
    const overlay = document.getElementById('modal-overlay');
    const content = document.getElementById('modal-content');
    const isEdit = mode === 'edit';
    currentProjectId = isEdit ? project.id : null;

    const sessionDate = isEdit ? toLocalDatetime(project.session_date) : getDefaultDatetime();

    content.innerHTML = `
        <div class="modal-header">
            <h2>${isEdit ? '✏️ Редактировать' : '✏️ Новый проект'}</h2>
            <button class="modal-close" id="modal-close">✕</button>
        </div>

        <div class="form-group">
            <label>Размер</label>
            <select id="f-size">
                <option value="маленький" ${isEdit && project.size === 'маленький' ? 'selected' : ''}>Маленький</option>
                <option value="средний"  ${isEdit && project.size === 'средний' ? 'selected' : ''}>Средний</option>
                <option value="большой"  ${isEdit && project.size === 'большой' ? 'selected' : ''}>Большой</option>
            </select>
        </div>

        <div class="form-group">
            <label>Сложность</label>
            <select id="f-complexity">
                <option value="низкая"  ${isEdit && project.complexity === 'низкая' ? 'selected' : ''}>Низкая</option>
                <option value="средняя" ${isEdit && project.complexity === 'средняя' ? 'selected' : ''}>Средняя</option>
                <option value="высокая" ${isEdit && project.complexity === 'высокая' ? 'selected' : ''}>Высокая</option>
            </select>
        </div>

        <div class="form-group">
            <label>Место нанесения</label>
            <input type="text" id="f-placement" value="${isEdit ? escapeHtml(project.placement) : ''}" placeholder="Например: левое предплечье" />
        </div>

        <div class="form-group">
            <label>Дата и время сеанса</label>
            <input type="datetime-local" id="f-session-date" value="${sessionDate}" />
        </div>

        <div class="form-group">
            <label>Стоимость (₸)</label>
            <input type="number" id="f-cost" value="${isEdit ? project.cost : ''}" placeholder="0" min="0" />
        </div>

        <div id="modal-error" class="error"></div>

        <div class="btn-group">
            <button class="btn btn-primary" id="modal-submit">${isEdit ? 'Сохранить' : 'Создать'}</button>
            ${isEdit ? `<button class="btn btn-danger" id="modal-delete">Удалить</button>` : ''}
        </div>
    `;

    overlay.classList.remove('hidden');

    document.getElementById('modal-close').addEventListener('click', closeModal);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });

    document.getElementById('modal-submit').addEventListener('click', () => {
        if (isEdit) updateProject(project.id);
        else createProject();
    });

    if (isEdit) {
        document.getElementById('modal-delete').addEventListener('click', () => deleteProject(project.id));
    }
}

function closeModal() {
    document.getElementById('modal-overlay').classList.add('hidden');
    currentProjectId = null;
}

function getFormData() {
    const placement = document.getElementById('f-placement').value.trim();
    const cost = parseFloat(document.getElementById('f-cost').value);
    const sessionDate = document.getElementById('f-session-date').value;

    if (!placement) throw new Error('Укажите место нанесения');
    if (!cost || cost <= 0) throw new Error('Укажите корректную стоимость');
    if (!sessionDate) throw new Error('Укажите дату сеанса');

    return {
        size: document.getElementById('f-size').value,
        complexity: document.getElementById('f-complexity').value,
        placement,
        session_date: new Date(sessionDate).toISOString(),
        cost,
    };
}

async function createProject() {
    const error = document.getElementById('modal-error');
    error.textContent = '';
    try {
        const data = getFormData();
        await apiRequest('/projects', { method: 'POST', body: JSON.stringify(data) });
        closeModal();
        window.dispatchEvent(new CustomEvent('projects:reload'));
    } catch (e) {
        error.textContent = e.message;
    }
}

async function updateProject(id) {
    const error = document.getElementById('modal-error');
    error.textContent = '';
    try {
        const data = getFormData();
        await apiRequest(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(data) });
        closeModal();
        window.dispatchEvent(new CustomEvent('projects:reload'));
    } catch (e) {
        error.textContent = e.message;
    }
}

async function deleteProject(id) {
    if (!confirm('Удалить проект? Это действие нельзя отменить.')) return;

    try {
        await apiRequest(`/projects/${id}`, { method: 'DELETE' });
        closeModal();
        window.dispatchEvent(new CustomEvent('projects:reload'));
    } catch (e) {
        document.getElementById('modal-error').textContent = e.message;
    }
}

function toLocalDatetime(iso) {
    const d = new Date(iso);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function getDefaultDatetime() {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(10, 0, 0, 0);
    return d.toISOString().slice(0, 16);
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}