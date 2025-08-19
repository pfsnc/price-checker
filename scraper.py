import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import time

class StampScraper:
    def __init__(self):
        self.base_urls = {
            'JT': 'http://www.518yp.com/JTxilie/',    # J和T系列
            'LJT': 'http://www.518yp.com/ljt/',       # 紀和特系列
            'WB': 'http://www.518yp.com/wgbh/'        # 文和編系列
        }
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def determine_series_type(self, series_code):
        """準確判斷郵票系列類型"""
        if not series_code:
            return 'unknown'
            
        # 移除所有空白字符
        series_code = series_code.strip()
        
        # 優先檢查較長的匹配
        if series_code.startswith('文'):
            return '文'
        elif series_code.startswith('编'):
            return '編'
        elif series_code.startswith('纪'):
            return '紀'
        elif series_code.startswith('特'):
            return '特'
        elif series_code.startswith('J'):
            return 'J'
        elif series_code.startswith('T'):
            return 'T'
        else:
            return 'unknown'

    def extract_series_code(self, title):
        """從標題中提取志號"""
        series_match = re.search(r'志号[：:]\s*([\w\d]+)', title)
        if series_match:
            return series_match.group(1).strip()
        return None

    def extract_price(self, title):
        """從標題中提取價格"""
        price_match = re.search(r'￥\s*(\d+)', title)
        if price_match:
            return int(price_match.group(1))
        return None

    def extract_title(self, title):
        """提取乾淨的標題"""
        # 移除志號和價格部分
        title = re.sub(r'志号[：:]\s*[\w\d]+', '', title)
        title = re.sub(r'￥\s*\d+', '', title)
        return title.strip()

    def parse_stamp_data(self, html):
        if not html:
            return []
        
        soup = BeautifulSoup(html, 'html.parser')
        stamps = []
        
        # 查找所有可能的郵票項目
        for item in soup.find_all(['div', 'li'], class_=['item', 'list-item']):
            try:
                title_elem = item.find('a')
                if not title_elem:
                    continue
                    
                full_title = title_elem.text.strip()
                
                # 提取各個部分
                series_code = self.extract_series_code(full_title)
                price = self.extract_price(full_title)
                clean_title = self.extract_title(full_title)
                
                if series_code and price:
                    series_type = self.determine_series_type(series_code)
                    
                    stamp = {
                        'series': series_code,
                        'series_type': series_type,
                        'title': clean_title,
                        'price': price,
                        'date': datetime.now().strftime('%Y-%m-%d')
                    }
                    stamps.append(stamp)
                    
            except Exception as e:
                print(f"Error parsing stamp item: {e}")
                continue
                
        return stamps

    def get_page_content(self, url, page=1):
        try:
            if page > 1:
                # 處理分頁URL
                if url.endswith('/'):
                    url = f"{url}index_{page}.html"
                else:
                    url = f"{url}/index_{page}.html"
                    
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            return response.text
        except Exception as e:
            print(f"Error fetching page {url}: {e}")
            return None

    def scrape_all_pages(self, url):
        all_stamps = []
        page = 1
        max_pages = 20  # 設置最大頁數限制
        
        while page <= max_pages:
            print(f"Scraping page {page} of {url}")
            html = self.get_page_content(url, page)
            
            if not html:
                break
                
            stamps = self.parse_stamp_data(html)
            if not stamps:
                break
                
            all_stamps.extend(stamps)
            page += 1
            time.sleep(2)  # 增加延遲，避免被封
            
        return all_stamps

    def scrape_all_series(self):
        all_stamps = []
        
        for url_key, url in self.base_urls.items():
            print(f"Scraping {url_key} series from {url}")
            stamps = self.scrape_all_pages(url)
            all_stamps.extend(stamps)
            time.sleep(5)  # 在不同系列之間添加更長的延遲
            
        return all_stamps

    def save_data(self, stamps):
        """保存數據並進行驗證"""
        if not stamps:
            print("Warning: No stamps data to save")
            return
            
        # 數據驗證
        valid_stamps = []
        for stamp in stamps:
            if all(key in stamp for key in ['series', 'series_type', 'title', 'price', 'date']):
                valid_stamps.append(stamp)
            else:
                print(f"Invalid stamp data: {stamp}")
        
        # 讀取現有數據
        try:
            with open('data/stamps_history.json', 'r', encoding='utf-8') as f:
                history = json.load(f)
        except FileNotFoundError:
            history = []
        except json.JSONDecodeError:
            print("Error reading existing data file, starting fresh")
            history = []
        
        # 添加新數據
        history.append({
            'date': datetime.now().strftime('%Y-%m-%d'),
            'data': valid_stamps
        })
        
        # 保存數據
        try:
            with open('data/stamps_history.json', 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            print(f"Successfully saved {len(valid_stamps)} stamps")
        except Exception as e:
            print(f"Error saving data: {e}")

def main():
    scraper = StampScraper()
    stamps = scraper.scrape_all_series()
    scraper.save_data(stamps)

if __name__ == "__main__":
    main()
