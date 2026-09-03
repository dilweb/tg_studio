/**
 * State management for TG Studio Master App.
 * Централизованное состояние приложения.
 */

const appState = {
    token: localStorage.getItem('tg_master_token') || '',
    master: null,
    projects: [],
};

function isAuthenticated() {
    return !!appState.token;
}

function setAuth(token, master) {
    appState.token = token;
    appState.master = master;
    localStorage.setItem('tg_master_token', token);
}

function resetAuth() {
    appState.token = '';
    appState.master = null;
    appState.projects = [];
    localStorage.removeItem('tg_master_token');
}

function setProjects(projects) {
    appState.projects = projects;
}

function getState() {
    return appState;
}