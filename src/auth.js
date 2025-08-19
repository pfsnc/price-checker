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
    const correctHash = '6021861960b10db44466fb1b8888c7906ded6e22956bbe6d40bbeb29dd6484d6'; // 示例雜湊值
    return hashedInput === correctHash;
}
