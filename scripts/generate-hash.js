const crypto = require('crypto');

// 從環境變量獲取密碼
const password = process.env.ACCESS_PASSWORD;

// 生成雜湊
const hash = crypto.createHash('sha256').update(password).digest('hex');

// 更新 auth.js 文件
const fs = require('fs');
const authFile = './src/auth.js';
let content = fs.readFileSync(authFile, 'utf8');
content = content.replace('HASHED_PASSWORD_PLACEHOLDER', hash);
fs.writeFileSync(authFile, content);

console.log('Password hash generated and updated in auth.js');
