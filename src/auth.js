const AUTH_KEY = 'auth_status';
const HASHED_PASSWORD = '6021861960b10db44466fb1b8888c7906ded6e22956bbe6d40bbeb29dd6484d6'; 

async function hashPassword(password) {
    const msgBuffer = new TextEncoder().encode(password);
    const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

async function checkPassword() {
    const password = document.getElementById('password').value;
    const hashedInput = await hashPassword(password);
    
    if (hashedInput === HASHED_PASSWORD) {
        sessionStorage.setItem(AUTH_KEY, 'true');
        document.getElementById('login').classList.add('hidden');
        document.getElementById('root').classList.remove('hidden');
        location.reload();
    } else {
        alert('密碼錯誤');
    }
}

function checkAuth() {
    return sessionStorage.getItem(AUTH_KEY) === 'true';
}

// 將函數掛載到 window 對象
window.checkPassword = checkPassword;
window.checkAuth = checkAuth;
