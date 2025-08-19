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
        { value: '编', label: '编号系列' },
        { value: '纪', label: '纪念系列' },
        { value: '特', label: '特种系列' }
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
            
            if (!Array.isArray(data) || data.length === 0) {
                throw new Error('無效的資料格式');
            }
    
            const latestData = data[data.length - 1].data.map(stamp => ({
                ...stamp,
                series: stamp.series,
                series_type: stamp.series_type,
                title: stamp.title,
                price: stamp.price,
                date: stamp.date,
                image_url: stamp.image_url
            }));
            
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
            console.error('獲取資料時發生錯誤:', error);
            setLoading(false);
        } finally {
            setLoading(false);
        }
    };

    const handleSearch = () => {
        let filtered = [...stamps];
        
        if (series !== 'all') {
            filtered = filtered.filter(stamp => {
                switch(series) {
                    case 'J':
                        return stamp.series.startsWith('J');
                    case 'T':
                        return stamp.series.startsWith('T');
                    case '文':
                        return stamp.series.startsWith('文');
                    case '编':
                        return stamp.series_type === '编号系列';
                    case '纪':
                        return stamp.series_type === '纪念系列';
                    case '特':
                        return stamp.series_type === '特种系列';
                    default:
                        return false;
                }
            });
        }
        
        if (number) {
            filtered = filtered.filter(stamp => {
                const searchTerm = number.toLowerCase();
                const series = stamp.series.toLowerCase();
                
                if (searchTerm.match(/^[jt]\d+$/i)) {
                    const letter = searchTerm[0].toUpperCase();
                    const num = searchTerm.slice(1);
                    return series === (letter + num).toLowerCase();
                }
                
                return series.includes(searchTerm);
            });
        }
        
        setFilteredStamps(filtered);
        
        console.log({
            selectedSeries: series,
            searchNumber: number,
            totalStamps: stamps.length,
            filteredCount: filtered.length,
            sampleFiltered: filtered.slice(0, 3).map(s => ({
                series: s.series,
                type: s.series_type,
                title: s.title
            }))
        });
    };

    React.useEffect(() => {
        handleSearch();
    }, [series]);

    if (loading) {
        return e('div', { className: "container" }, "載入中...");
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

        e('div', { key: 'stamps-list', className: "stamps-list" },
            filteredStamps.map((stamp, index) => {
                const stampHistory = priceHistory[stamp.series] || { min: stamp.price, max: stamp.price };
                const priceRange = stampHistory.max - stampHistory.min;
                const percentage = priceRange === 0 ? 50 : ((stamp.price - stampHistory.min) / priceRange) * 100;
                
                return e('div', { 
                    key: `stamp-${stamp.series}-${index}`,
                    className: "stamp-item"
                }, [
                    e('div', {
                        key: `image-container-${stamp.series}-${index}`,
                        className: "stamp-image-container"
                    }, 
                        e('img', {
                            src: stamp.image_url,
                            alt: stamp.title,
                            className: "stamp-image",
                            onError: (e) => {
                                e.target.onerror = null;
                                e.target.src = 'default-stamp.png';
                            }
                        })
                    ),
                    e('div', { 
                        key: `header-${stamp.series}-${index}`,
                        className: "stamp-info"
                    }, [
                        e('span', { 
                            key: `tag-${stamp.series}-${index}`,
                            className: "series-tag"
                        }, stamp.series_type),
                        e('span', {
                            key: `title-${stamp.series}-${index}`,
                            className: "stamp-title"
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
