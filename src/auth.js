// 將在構建時由 GitHub Actions 替換的雜湊值
const HASHED_PASSWORD = 'HASHED_PASSWORD_PLACEHOLDER';

// 雜湊函數
async function hashPassword(password) {
    const encoder = new TextEncoder();
    const data = encoder.encode(password);
    const hash = await crypto.subtle.digest('SHA-256', data);
    return Array.from(new Uint8Array(hash))
        .map(b => b.toString(16).padStart(2, '0'))
        .join('');
}

// 驗證函數
export async function verifyPassword(input) {
    const hashedInput = await hashPassword(input);
    return hashedInput === HASHED_PASSWORD;
}
