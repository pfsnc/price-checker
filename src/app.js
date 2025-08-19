const e = React.createElement;

// App 組件
const App = () => {
    const [isAuthenticated, setIsAuthenticated] = React.useState(window.checkAuth());

    // 監聽認證狀態改變
    React.useEffect(() => {
        const handleAuthChange = () => {
            setIsAuthenticated(window.checkAuth());
        };

        window.addEventListener('auth-changed', handleAuthChange);
        return () => {
            window.removeEventListener('auth-changed', handleAuthChange);
        };
    }, []);

    if (!isAuthenticated) {
        return null;
    }

    return e(window.StampPriceTracker, null);
};

// 等待 DOM 完全載入後再渲染
document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('root');
    const root = ReactDOM.createRoot(container);
    root.render(e(App));
});
