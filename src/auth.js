const AUTH_KEY = 'auth_status';

async function hashPassword(password) {
    const encoder = new TextEncoder();
    const data = encoder.encode(password);
    const hash = await crypto.subtle.digest('SHA-256', data);
    return Array.from(new Uint8Array(hash))
        .map(b => b.toString(16).padStart(2, '0'))
        .join('');
}

// 驗證函數
async function checkPassword() {
    const passwordInput = document.getElementById('password').value;
    const hashedInput = await hashPassword(passwordInput);
    const correctHash = '6021861960b10db44466fb1b8888c7906ded6e22956bbe6d40bbeb29dd6484d6';
    
    if (hashedInput === correctHash) {
        sessionStorage.setItem(AUTH_KEY, 'true');
        document.getElementById('login').classList.add('hidden');
        document.getElementById('root').classList.remove('hidden');
        location.reload();
        return true;
    } else {
        alert('密碼錯誤');
        return false;
    }
}

function checkAuth() {
    return sessionStorage.getItem(AUTH_KEY) === 'true';
}

// 將函數掛載到 window 對象
window.checkPassword = checkPassword;
window.checkAuth = checkAuth;
