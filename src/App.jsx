import React, { useState, useEffect } from 'react';
import StampPriceTracker from './scraper'; // 確保路徑正確

function App() {
    const [isAuthenticated, setIsAuthenticated] = useState(false);

    useEffect(() => {
        // 檢查登入狀態
        const authStatus = sessionStorage.getItem('authenticated');
        if (authStatus === 'true') {
            setIsAuthenticated(true);
        }
    }, []);

    if (!isAuthenticated) {
        return null; // 密碼驗證在 index.html 中處理
    }

    return <StampPriceTracker />; // 密碼驗證通過後顯示表單
}

export default App;
