const e = React.createElement;

// App 組件
const App = () => {
    const [isAuthenticated, setIsAuthenticated] = React.useState(window.checkAuth());
    const [mountTime] = React.useState(Date.now());

    // 監聽認證狀態改變
    React.useEffect(() => {
        const handleAuthChange = () => {
            const authenticated = window.checkAuth();
            setIsAuthenticated(authenticated);
            
            if (!authenticated) {
                // 如果認證失效，重新加載頁面
                window.location.reload();
            }
        };

        window.addEventListener('auth-changed', handleAuthChange);

        // 組件卸載時清理
        return () => {
            window.removeEventListener('auth-changed', handleAuthChange);
        };
    }, []);

    // 每分鐘檢查一次認證狀態
    React.useEffect(() => {
        const interval = setInterval(() => {
            if (!window.checkAuth()) {
                setIsAuthenticated(false);
            }
        }, 60000);

        return () => clearInterval(interval);
    }, []);

    if (!isAuthenticated) {
        return null;
    }

    return e(window.StampPriceTracker, null);
};

// 等待 DOM 完全載入後再渲染
document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('root');
    if (!container) return;

    try {
        const root = ReactDOM.createRoot(container);
        root.render(e(App));
    } catch (error) {
        console.error('Rendering error:', error);
    }
});
