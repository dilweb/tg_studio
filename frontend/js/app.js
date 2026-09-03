/**
 * TG Studio Master App — Main Entry Point.
 */

let tg = null;
try {
    tg = window.Telegram?.WebApp;
    if (tg) tg.expand();
} catch (e) {}

// ---- Navigation ----
const screens = {
    login: document.getElementById('screen-login'),
    dashboard: document.getElementById('screen-dashboard'),
};

function switchScreen(name) {
    Object.keys(screens).forEach(key => {
        screens[key].classList.add('hidden');
    });
    screens[name].classList.remove('hidden');
}

// ---- Events ----
window.addEventListener('screen:switch', (e) => {
    if (e.detail === 'login') {
        renderLogin();
        switchScreen('login');
    } else if (e.detail === 'dashboard') {
        renderDashboard();
        switchScreen('dashboard');
    }
});

window.addEventListener('auth:expired', () => {
    switchScreen('login');
    renderLogin();
});

window.addEventListener('projects:reload', () => {
    renderDashboard();
});

// ---- Init ----
async function init() {
    if (!isAuthenticated()) {
        renderLogin();
        switchScreen('login');
        return;
    }

    try {
        const data = await apiRequest('/projects');
        if (!data) {
            renderLogin();
            switchScreen('login');
            return;
        }
        renderDashboard();
        switchScreen('dashboard');
    } catch (e) {
        renderLogin();
        switchScreen('login');
    }
}

init();