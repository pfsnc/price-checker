const AUTH_KEY = 'auth_status';

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
        localStorage.setItem(AUTH_KEY, 'true'); // 改用 localStorage 而不是 sessionStorage
        document.getElementById('login').classList.add('hidden');
        document.getElementById('root').classList.remove('hidden');
        window.dispatchEvent(new Event('auth-changed')); // 觸發認證狀態改變事件
        return true;
    } else {
        alert('密碼錯誤');
        return false;
    }
}

function checkAuth() {
    return localStorage.getItem(AUTH_KEY) === 'true'; // 改用 localStorage
}

// 初始化頁面時檢查認證狀態
document.addEventListener('DOMContentLoaded', () => {
    if (checkAuth()) {
        document.getElementById('login').classList.add('hidden');
        document.getElementById('root').classList.remove('hidden');
    }
});

window.checkPassword = checkPassword;
window.checkAuth = checkAuth;
