window.StampPriceTracker = (() => {
    const e = React.createElement;

    return () => {
        const [stamps, setStamps] = React.useState([]);
        const [filteredStamps, setFilteredStamps] = React.useState([]);
        const [series, setSeries] = React.useState('all');
        const [number, setNumber] = React.useState('');
        const [loading, setLoading] = React.useState(true);
        const [priceHistory, setPriceHistory] = React.useState({});

        const seriesTypes = [
            { value: 'all', label: '全部系列' },
            { value: 'J', label: 'J系列' },
            { value: 'T', label: 'T系列' },
            { value: '文', label: '文革系列' },
            { value: '編', label: '編號系列' },
            { value: '紀', label: '紀念系列' },
            { value: '特', label: '特種系列' }
        ];

        // 創建進度條字符串
        const createProgressBar = (percentage, width = 20) => {
            const pos = Math.floor((width - 2) * (percentage / 100));
            const bar = new Array(width).fill('-');
            bar[0] = '|';
            bar[width - 1] = '|';
            bar[pos] = 'x';
            return bar.join('');
        };

        React.useEffect(() => {
            fetchData();
        }, []);

        const fetchData = async () => {
            try {
                const response = await fetch('./data/stamps_history.json');
                const data = await response.json();
                const latestData = data[data.length - 1].data;
                
                // 處理歷史價格數據
                const history = {};
                data.forEach(snapshot => {
                    snapshot.data.forEach(stamp => {
                        if (!history[stamp.series]) {
                            history[stamp.series] = {
                                prices: [],
                                min: Infinity,
                                max: -Infinity
                            };
                        }
                        history[stamp.series].prices.push(stamp.price);
                        history[stamp.series].min = Math.min(history[stamp.series].min, stamp.price);
                        history[stamp.series].max = Math.max(history[stamp.series].max, stamp.price);
                    });
                });
                
                setPriceHistory(history);
                setStamps(latestData);
                setFilteredStamps(latestData);
            } catch (error) {
                console.error('Error fetching data:', error);
            } finally {
                setLoading(false);
            }
        };

        const handleSearch = () => {
            let filtered = [...stamps];
            
            if (series !== 'all') {
                filtered = filtered.filter(stamp => 
                    stamp.series_type === series
                );
            }
            
            if (number) {
                filtered = filtered.filter(stamp => 
                    stamp.series.toLowerCase().includes(number.toLowerCase())
                );
            }
            
            setFilteredStamps(filtered);
        };

        if (loading) {
            return e('div', { className: "flex justify-center items-center h-screen" },
                "Loading..."
            );
        }

        return e('div', { className: "max-w-4xl mx-auto p-4" }, [
            // 搜索表單
            e('div', { key: 'search-form', className: "mb-8 flex gap-4" }, [
                e('select', {
                    key: 'series-select',
                    className: "border rounded p-2",
                    value: series,
                    onChange: (event) => setSeries(event.target.value)
                }, 
                    seriesTypes.map(type => 
                        e('option', { key: type.value, value: type.value }, type.label)
                    )
                ),
                e('input', {
                    key: 'number-input',
                    type: "text",
                    placeholder: "輸入編號",
                    className: "border rounded p-2",
                    value: number,
                    onChange: (event) => setNumber(event.target.value)
                }),
                e('button', {
                    key: 'search-button',
                    className: "bg-blue-500 text-white px-4 py-2 rounded",
                    onClick: handleSearch
                }, "搜尋")
            ]),

            // 郵票列表
            e('div', { key: 'stamps-list', className: "space-y-4" },
                filteredStamps.map(stamp => {
                    const stampHistory = priceHistory[stamp.series] || { min: stamp.price, max: stamp.price };
                    const priceRange = stampHistory.max - stampHistory.min;
                    const percentage = priceRange === 0 
                        ? 50  // 如果最高價和最低價相同，顯示在中間
                        : ((stamp.price - stampHistory.min) / priceRange) * 100;
                    
                    return e('div', { 
                        key: stamp.series,
                        className: "border rounded p-4"
                    }, [
                        e('div', { 
                            key: 'header',
                            className: "flex items-center justify-between mb-2" 
                        }, [
                            e('div', null, [
                                e('span', { 
                                    className: "inline-block px-2 py-1 text-sm rounded bg-gray-200 mr-2" 
                                }, stamp.series_type),
                                e('span', null, `${stamp.series} - ${stamp.title}`)
                            ]),
                            e('span', { 
                                className: "text-xl text-blue-600" 
                            }, `¥${stamp.price.toLocaleString()}`)
                        ]),
                        e('pre', { 
                            key: 'price-bar',
                            className: "font-mono text-center bg-gray-100 p-2 rounded"
                        }, createProgressBar(percentage)),
                        e('div', { 
                            key: 'price-range',
                            className: "flex justify-between text-sm text-gray-500 mt-1" 
                        }, [
                            e('span', null, `¥${stampHistory.min.toLocaleString()}`),
                            e('span', null, `¥${stampHistory.max.toLocaleString()}`)
                        ])
                    ]);
                })
            ),

            // 計數器
            e('div', { 
                key: 'counter',
                className: "mt-4 text-right text-sm text-gray-500" 
            }, `共顯示 ${filteredStamps.length} 個項目`)
        ]);
    };
})();
