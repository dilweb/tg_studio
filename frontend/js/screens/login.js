/**
 * Login screen component.
 * Вход мастера по ID и паролю.
 */

function renderLogin() {
    const app = document.getElementById('screen-login');
    app.innerHTML = `
        <div class="login-box">
            <h2>🔐 TG Studio</h2>
            <p class="subtitle">Вход для мастера</p>

            <div class="form-group">
                <label>Telegram ID мастера</label>
                <input type="text" id="login-id" inputmode="numeric" placeholder="Ваш Telegram ID" autocomplete="off" />
            </div>

            <div class="form-group">
                <label>Пароль</label>
                <input type="password" id="login-password" placeholder="Пароль" autocomplete="off" />
            </div>

            <button class="btn btn-primary" id="login-btn">Войти</button>
            <div id="login-error" class="error"></div>
        </div>
    `;

    document.getElementById('login-btn').addEventListener('click', handleLogin);
    app.classList.remove('hidden');

    document.getElementById('login-password').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleLogin();
    });
}

async function handleLogin() {
    const id = document.getElementById('login-id').value.trim().replace(/[,\s]/g, '');
    const password = document.getElementById('login-password').value.trim();
    const error = document.getElementById('login-error');
    error.textContent = '';

    if (!id || !password) {
        error.textContent = 'Заполните все поля';
        return;
    }

    const numId = parseInt(id, 10);
    if (isNaN(numId)) {
        error.textContent = 'ID должен быть числом';
        return;
    }

    try {
        const data = await apiRequest('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ master_id: numId, password }),
        });
        if (!data) return;

        setAuth(data.access_token, data);
        switchScreen('dashboard');
    } catch (e) {
        error.textContent = e.message;
    }
}