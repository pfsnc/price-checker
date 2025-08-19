import React from 'react';
import StampPriceTracker from './scraper';

function App() {
    const isAuthenticated = window.checkAuth();
    
    if (!isAuthenticated) {
        return null; // 未驗證時不顯示內容
    }

    return <StampPriceTracker />;
}

export default App;
