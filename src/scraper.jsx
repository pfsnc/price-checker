import React, { useState, useEffect } from 'react';
import { Search, SortAsc, SortDesc, RefreshCcw } from 'lucide-react';

function StampPriceTracker() {
    const [stamps, setStamps] = useState([]);
    const [filteredStamps, setFilteredStamps] = useState([]);
    const [series, setSeries] = useState('all');
    const [number, setNumber] = useState('');
    const [sortOrder, setSortOrder] = useState('asc');
    const [loading, setLoading] = useState(true);
    const [priceRange, setPriceRange] = useState({ min: 0, max: 10000 });

    const seriesTypes = [
        { value: 'all', label: '全部系列' },
        { value: 'J', label: 'J系列' },
        { value: 'T', label: 'T系列' },
        { value: '文', label: '文革系列' },
        { value: '編', label: '編號系列' },
        { value: '紀', label: '紀念系列' },
        { value: '特', label: '特種系列' }
    ];

    useEffect(() => {
        fetchData();
    }, []);

    const fetchData = async () => {
        try {
            const response = await fetch('/data/stamps_history.json');
            const data = await response.json();
            const latestData = data[data.length - 1].data;
            
            // 計算價格範圍
            const prices = latestData.map(stamp => stamp.price);
            setPriceRange({
                min: Math.min(...prices),
                max: Math.max(...prices)
            });
            
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
        
        filtered.sort((a, b) => {
            return sortOrder === 'asc' 
                ? a.price - b.price 
                : b.price - a.price;
        });
        
        setFilteredStamps(filtered);
    };

    const toggleSort = () => {
        setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
        handleSearch();
    };

    if (loading) {
        return (
            <div className="flex justify-center items-center h-screen">
                <RefreshCcw className="animate-spin" size={32} />
            </div>
        );
    }

    return (
        <div className="max-w-4xl mx-auto p-4">
            <div className="mb-8 flex flex-wrap gap-4">
                <select 
                    className="border rounded p-2"
                    value={series}
                    onChange={(e) => setSeries(e.target.value)}
                >
                    {seriesTypes.map(type => (
                        <option key={type.value} value={type.value}>
                            {type.label}
                        </option>
                    ))}
                </select>
                <input
                    type="text"
                    placeholder="輸入編號"
                    className="border rounded p-2"
                    value={number}
                    onChange={(e) => setNumber(e.target.value)}
                />
                <button
                    className="bg-blue-500 text-white px-4 py-2 rounded flex items-center gap-2"
                    onClick={handleSearch}
                >
                    <Search size={20} />
                    搜尋
                </button>
                <button
                    className="bg-gray-500 text-white px-4 py-2 rounded flex items-center gap-2"
                    onClick={toggleSort}
                >
                    {sortOrder === 'asc' ? <SortAsc size={20} /> : <SortDesc size={20} />}
                    價格排序
                </button>
            </div>

            <div className="space-y-4">
                {filteredStamps.map((stamp) => (
                    <div key={stamp.series} className="border rounded p-4 hover:shadow-lg transition-shadow">
                        <div className="flex justify-between items-center mb-2">
                            <div>
                                <span className="inline-block px-2 py-1 text-sm rounded bg-gray-200 mr-2">
                                    {stamp.series_type}
                                </span>
                                <h3 className="inline text-lg font-bold">
                                    {stamp.series} - {stamp.title}
                                </h3>
                            </div>
                            <span className="text-xl text-blue-600 font-semibold">
                                ¥{stamp.price.toLocaleString()}
                            </span>
                        </div>
                        <div className="relative h-4 bg-gray-200 rounded overflow-hidden">
                            <div 
                                className="absolute h-full bg-blue-500 rounded"
                                style={{
                                    width: `${((stamp.price - priceRange.min) / (priceRange.max - priceRange.min)) * 100}%`
                                }}
                            />
                        </div>
                        <div className="flex justify-between text-sm text-gray-500 mt-1">
                            <span>¥{priceRange.min.toLocaleString()}</span>
                            <span>¥{priceRange.max.toLocaleString()}</span>
                        </div>
                    </div>
                ))}
            </div>
            
            <div className="mt-4 text-right text-sm text-gray-500">
                共顯示 {filteredStamps.length} 個項目
            </div>
        </div>
    );
}

export default StampPriceTracker;
