/**
 * API client for TG Studio Master App.
 * Все запросы к бэкенду, управление токеном.
 */

const API_BASE = window.location.origin;
const STORAGE_KEY = 'tg_master_token';

function setToken(token) {
    localStorage.setItem(STORAGE_KEY, token);
}

function getToken() {
    return localStorage.getItem(STORAGE_KEY) || '';
}

function clearToken() {
    localStorage.removeItem(STORAGE_KEY);
}

async function apiRequest(path, options = {}) {
    const headers = { ...options.headers };
    const token = getToken();

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    if (!options.body || typeof options.body === 'string') {
        headers['Content-Type'] = 'application/json';
    }

    const res = await fetch(`${API_BASE}/api/tattoo${path}`, {
        ...options,
        headers,
    });

    if (res.status === 401) {
        clearToken();
        window.dispatchEvent(new CustomEvent('auth:expired'));
        return null;
    }

    if (res.status === 204) return null;

    const data = await res.json();
    if (!res.ok) {
        throw new Error(data.detail || `Ошибка ${res.status}`);
    }
    return data;
}