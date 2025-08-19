import React, { useState, useEffect } from 'react';
import StampPriceTracker from './scraper';
import { Lock } from 'lucide-react';

function App() {
    const [isAuthenticated, setIsAuthenticated] = useState(false);
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [lastUpdated, setLastUpdated] = useState('');

    useEffect(() => {
        // 設置最後更新時間
        setLastUpdated('2025-08-19 15:07:01');
        
        // 檢查登入狀態
        const authStatus = sessionStorage.getItem('isAuthenticated');
        if (authStatus === 'true') {
            setIsAuthenticated(true);
        }
    }, []);

    const handleLogin = (e) => {
        e.preventDefault();
        
        try {
            // 直接比對密碼
            if (password === window.ACCESS_PASSWORD) {
                setIsAuthenticated(true);
                sessionStorage.setItem('isAuthenticated', 'true');
                setError('');
            } else {
                setError('密碼錯誤');
            }
        } catch (error) {
            setError('驗證過程發生錯誤');
            console.error('Error:', error);
        }
    };

    if (!isAuthenticated) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-gray-100">
                <div className="max-w-md w-full space-y-8 p-8 bg-white rounded-lg shadow-lg">
                    <div className="text-center">
                        <div className="mx-auto h-12 w-12 text-blue-500">
                            <Lock />
                        </div>
                        <h2 className="mt-6 text-3xl font-extrabold text-gray-900">
                            郵票價格查詢系統
                        </h2>
                        <p className="mt-2 text-sm text-gray-600">
                            最後更新：{lastUpdated} UTC
                        </p>
                    </div>
                    <form className="mt-8 space-y-6" onSubmit={handleLogin}>
                        <div>
                            <input
                                type="password"
                                required
                                className="appearance-none rounded relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 focus:outline-none focus:ring-blue-500 focus:border-blue-500 focus:z-10 sm:text-sm"
                                placeholder="請輸入密碼"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                            />
                        </div>
                        {error && (
                            <div className="text-red-500 text-sm text-center">
                                {error}
                            </div>
                        )}
                        <div>
                            <button
                                type="submit"
                                className="group relative w-full flex justify-center py-2 px-4 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                            >
                                登入
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        );
    }

    return <StampPriceTracker />;
}

export default App;
