const AUTH_KEY = 'auth_token';
const EXPIRE_TIME = 30 * 60 * 1000; // 30 minutes in milliseconds

// 簡單的 JWT 風格 token 生成
function generateToken() {
    const now = Date.now();
    const expireAt = now + EXPIRE_TIME;
    const token = {
        exp: expireAt,
        iat: now
    };
    return btoa(JSON.stringify(token)); // base64 encode
}

// 驗證 token
function validateToken(token) {
    try {
        const decoded = JSON.parse(atob(token)); // base64 decode
        return decoded.exp > Date.now();
    } catch {
        return false;
    }
}

async function hashPassword(password) {
    const encoder = new TextEncoder();
    const data = encoder.encode(password);
    const hash = await crypto.subtle.digest('SHA-256', data);
    return Array.from(new Uint8Array(hash))
        .map(b => b.toString(16).padStart(2, '0'))
        .join('');
}

async function checkPassword() {
    const passwordInput = document.getElementById('password').value;
    const hashedInput = await hashPassword(passwordInput);
    // 正確的密碼哈希值
    const correctHash = '6021861960b10db44466fb1b8888c7906ded6e22956bbe6d40bbeb29dd6484d6';
    
    if (hashedInput === correctHash) {
        const token = generateToken();
        localStorage.setItem(AUTH_KEY, token);
        document.getElementById('login').classList.add('hidden');
        document.getElementById('root').classList.remove('hidden');
        window.dispatchEvent(new Event('auth-changed'));
        return true;
    } else {
        alert('密碼錯誤');
        return false;
    }
}

function checkAuth() {
    const token = localStorage.getItem(AUTH_KEY);
    if (!token) return false;
    
    if (validateToken(token)) {
        return true;
    } else {
        localStorage.removeItem(AUTH_KEY);
        document.getElementById('login').classList.remove('hidden');
        document.getElementById('root').classList.add('hidden');
        return false;
    }
}

// 自動檢查認證狀態
function startAuthCheck() {
    // 初始檢查
    if (!checkAuth()) {
        document.getElementById('login').classList.remove('hidden');
        document.getElementById('root').classList.add('hidden');
    }

    // 每分鐘檢查一次認證狀態
    setInterval(() => {
        if (!checkAuth()) {
            window.dispatchEvent(new Event('auth-changed'));
        }
    }, 60000);
}

// 初始化頁面時啟動認證檢查
document.addEventListener('DOMContentLoaded', startAuthCheck);

window.checkPassword = checkPassword;
window.checkAuth = checkAuth;
