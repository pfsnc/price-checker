window.StampPriceTracker = function StampPriceTracker() {
    const e = React.createElement;

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
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const data = await response.json();
            console.log('Fetched data:', data); // 添加日誌
    
            if (!Array.isArray(data) || data.length === 0) {
                throw new Error('Invalid data format');
            }
    
            const latestData = data[data.length - 1].data;
            console.log('Latest data:', latestData); // 添加日誌
            
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
            setLoading(false);
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
        return e('div', { className: "container" }, "Loading...");
    }

    return e('div', { className: "container" }, [
        e('div', { key: 'search-form', className: "search-form" }, [
            e('select', {
                key: 'series-select',
                className: "form-input",
                value: series,
                onChange: (event) => setSeries(event.target.value)
            }, 
                seriesTypes.map((type, index) => 
                    e('option', { 
                        key: `series-type-${index}`,
                        value: type.value 
                    }, type.label)
                )
            ),
            e('input', {
                key: 'number-input',
                type: "text",
                placeholder: "輸入編號",
                className: "form-input",
                value: number,
                onChange: (event) => setNumber(event.target.value)
            }),
            e('button', {
                key: 'search-button',
                className: "btn",
                onClick: handleSearch
            }, "搜尋")
        ]),

        e('div', { key: 'stamps-list' },
            filteredStamps.map((stamp, index) => {
                const stampHistory = priceHistory[stamp.series] || { min: stamp.price, max: stamp.price };
                const priceRange = stampHistory.max - stampHistory.min;
                const percentage = priceRange === 0 ? 50 : ((stamp.price - stampHistory.min) / priceRange) * 100;
                
                return e('div', { 
                    key: `stamp-${stamp.series}-${index}`,
                    className: "stamp-item"
                }, [
                    e('div', { 
                        key: `header-${stamp.series}-${index}` 
                    }, [
                        e('span', { 
                            key: `tag-${stamp.series}-${index}`,
                            className: "series-tag"
                        }, stamp.series_type),
                        e('span', {
                            key: `title-${stamp.series}-${index}`
                        }, `${stamp.series} - ${stamp.title}`),
                        e('span', { 
                            key: `price-${stamp.series}-${index}`,
                            className: "current-price"
                        }, `¥${stamp.price.toLocaleString()}`)
                    ]),
                    e('pre', { 
                        key: `bar-${stamp.series}-${index}`,
                        className: "price-bar"
                    }, createProgressBar(percentage)),
                    e('div', { 
                        key: `range-${stamp.series}-${index}`,
                        className: "price-range"
                    }, [
                        e('span', { 
                            key: `min-${stamp.series}-${index}`
                        }, `¥${stampHistory.min.toLocaleString()}`),
                        e('span', { 
                            key: `max-${stamp.series}-${index}`
                        }, `¥${stampHistory.max.toLocaleString()}`)
                    ])
                ]);
            })
        ),

        e('div', { 
            key: 'counter',
            className: "mt-4 text-right text-sm text-gray-500"
        }, `共顯示 ${filteredStamps.length} 個項目`)
    ]);
};
