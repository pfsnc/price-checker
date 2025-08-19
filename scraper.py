import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import time
import random

class StampScraper:
    def __init__(self):
        # 修正基礎 URL 和對應的列表頁參數
        self.base_urls = {
            'JT': {
                'url': 'http://www.518yp.com/JTxilie',
                'list_param': '90'     # list_90_X.html
            },
            'WB': {
                'url': 'http://www.518yp.com/wbypiao',
                'list_param': '84'     # list_84_X.html
            },
            'LJT': {
                'url': 'http://www.518yp.com/ljt',
                'list_param': '82'     # list_82_X.html
            }
        }
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Referer': 'http://www.518yp.com/'
        }

    def get_page_content(self, base_url_info, page=1):
        """
        獲取指定頁面的內容
        base_url_info: 包含 url 和 list_param 的字典
        """
        try:
            # 構建完整的URL
            url = f"{base_url_info['url']}/list_{base_url_info['list_param']}_{page}.html"
            
            print(f"正在請求URL: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            
            if response.status_code != 200:
                print(f"警告：獲得了非200響應代碼：{response.status_code}")
                return None
                
            content = response.text
            print(f"成功獲取網頁內容，長度：{len(content)} 字符")
            return content
            
        except requests.exceptions.RequestException as e:
            print(f"請求失敗 {url}: {e}")
            return None

    def scrape_all_pages(self, base_url_info):
        """
        抓取所有頁面的數據
        base_url_info: 包含 url 和 list_param 的字典
        """
        all_stamps = []
        page = 1
        max_pages = 20  # 設置最大頁數限制
        retry_count = 3  # 每頁最大重試次數
        
        while page <= max_pages:
            print(f"正在抓取第 {page} 頁，來自 {base_url_info['url']}")
            
            # 添加重試機制
            for attempt in range(retry_count):
                html = self.get_page_content(base_url_info, page)
                if html:
                    break
                print(f"第 {attempt + 1} 次重試抓取第 {page} 頁")
                time.sleep(5 * (attempt + 1))
            
            if not html:
                print(f"無法獲取第 {page} 頁的內容，即使在 {retry_count} 次重試後")
                break
                
            stamps = self.parse_stamp_data(html)
            if not stamps:
                print(f"在第 {page} 頁沒有找到郵票數據")
                # 檢查是否真的到達最後一頁
                if "没有找到相关的信息" in html:
                    print("已到達最後一頁")
                    break
            
            all_stamps.extend(stamps)
            print(f"當前已收集 {len(all_stamps)} 個郵票數據")
            
            page += 1
            
            # 添加隨機延遲，避免被反爬
            delay = 2 + random.random() * 3  # 2-5秒的隨機延遲
            print(f"等待 {delay:.1f} 秒後繼續...")
            time.sleep(delay)
                
        return all_stamps

    def scrape_all_series(self):
        all_stamps = []
        
        for series_key, url_info in self.base_urls.items():
            print(f"正在抓取 {series_key} 系列，來自 {url_info['url']}")
            stamps = self.scrape_all_pages(url_info)
            all_stamps.extend(stamps)
            time.sleep(5)  # 在不同系列之間添加更長的延遲
            
        return all_stamps

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
        
        # 尋找所有的表格，這些表格包含郵票信息
        for table in soup.find_all('table', attrs={'width': '100%'}):
            try:
                # 找到標題鏈接
                title_link = table.find('a', title=True)
                if not title_link:
                    continue
                    
                full_title = title_link['title'].strip()
                
                # 找到志號（在包含 "志号：" 的段落中）
                series_p = table.find('p', text=lambda t: t and '志号：' in t)
                series_code = None
                if series_p:
                    series_code = series_p.text.replace('志号：', '').strip()
                
                # 找到價格（在 class="shop_s" 的元素中）
                price_elem = table.find(class_='shop_s')
                price = None
                if price_elem:
                    price_match = re.search(r'￥\s*(\d+)', price_elem.text)
                    if price_match:
                        price = int(price_match.group(1))
                
                # 提取實際標題（從 strong 標籤中）
                clean_title = table.find('strong')
                if clean_title:
                    clean_title = clean_title.text.strip()
                
                if series_code and price and clean_title:
                    series_type = self.determine_series_type(series_code)
                    
                    stamp = {
                        'series': series_code,
                        'series_type': series_type,
                        'title': clean_title,
                        'price': price,
                        'date': datetime.now().strftime('%Y-%m-%d')
                    }
                    stamps.append(stamp)
                    print(f"成功解析郵票: {stamp}")  # 偵錯輸出
                    
            except Exception as e:
                print(f"解析郵票項目時發生錯誤: {e}")
                continue
        
        print(f"本頁面共解析到 {len(stamps)} 個郵票數據")
        return stamps

    def get_page_content(self, url, page=1):
        try:
            # 處理分頁URL
            if page > 1:
                # 從基礎URL中提取正確的路徑格式
                base_path = url.rstrip('/')
                if 'list_90_' not in base_path:
                    # 如果是第一次訪問，需要修改URL格式
                    if base_path.endswith('/JTxilie'):
                        url = f"{base_path}/list_90_{page}.html"
                    elif base_path.endswith('/ljt'):
                        url = f"{base_path}/list_90_{page}.html"
                    elif base_path.endswith('/wbypiao'):
                        url = f"{base_path}/list_84_{page}.html"
                else:
                    # 如果已經是列表頁面，直接替換頁碼
                    url = re.sub(r'list_90_\d+\.html', f'list_90_{page}.html', base_path)
            else:
                # 第一頁的處理
                base_path = url.rstrip('/')
                url = f"{base_path}/list_90_1.html"
    
            print(f"正在請求URL: {url}")  # 偵錯輸出
            
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            
            # 檢查響應狀態
            if response.status_code != 200:
                print(f"警告：獲得了非200響應代碼：{response.status_code}")
                return None
                
            content = response.text
            print(f"成功獲取網頁內容，長度：{len(content)} 字符")
            return content
        
    except requests.exceptions.RequestException as e:
        print(f"請求失敗 {url}: {e}")
        return None

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
