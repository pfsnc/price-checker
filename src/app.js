const e = React.createElement;

// App 組件
const App = () => {
    const isAuthenticated = window.checkAuth();
    
    if (!isAuthenticated) {
        return null;
    }

    return e(window.StampPriceTracker, null);
};

// 渲染應用
const container = document.getElementById('root');
const root = ReactDOM.createRoot(container);
root.render(e(App));
