import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import time
import random
import os
from urllib.parse import urljoin

class StampScraper:
    def __init__(self):
        self.base_urls = {
            'JT': {
                'url': 'http://www.518yp.com/JTxilie',
                'list_param': '90'
            },
            'WB': {
                'url': 'http://www.518yp.com/wbypiao',
                'list_param': '84'
            },
            'LJT': {
                'url': 'http://www.518yp.com/ljt',
                'list_param': '82'
            },
            'M': {  # 新增小型張分類
                'url': 'http://www.518yp.com/xiaoxingzhang',
                'list_param': '117'
            }
            'PGHJQJB': {  # 新增普改航欠軍包分類
                'url': 'http://www.518yp.com/pugaihangqianjunbao',
                'list_param': '135'
            }
        }
            
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Referer': 'http://www.518yp.com/'
        }
        # 確保圖片目錄存在
        self.img_dir = './img'
        os.makedirs(self.img_dir, exist_ok=True)
        
    def generate_unique_key(self, series_code, title):
        """生成郵票的唯一鍵"""
        key = series_code
        
        # 檢查是否無齒版本
        if '无齿' in title:
            key += '_NH'
            
        # 檢查是否再版/改版
        if any(keyword in title for keyword in ['再版', '改版']):
            key += '_NEW'
            
        return key
        
    def download_image(self, img_url, unique_key):
        """下載並保存圖片"""
        if not img_url:
            return None
            
        try:
            # 確定圖片副檔名
            ext = os.path.splitext(img_url)[1]
            if not ext:
                ext = '.jpg'  # 預設使用 jpg
                
            # 構建圖片保存路徑
            img_path = os.path.join(self.img_dir, f'{unique_key}{ext}')
            
            # 如果圖片已存在，直接返回路徑
            if os.path.exists(img_path):
                return img_path
                
            # 下載圖片
            response = requests.get(img_url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                with open(img_path, 'wb') as f:
                    f.write(response.content)
                print(f"成功下載圖片: {img_path}")
                return img_path
                
        except Exception as e:
            print(f"下載圖片失敗 {img_url}: {e}")
            return None

    def get_page_content(self, base_url_info, page=1):
        try:
            url = f"{base_url_info['url']}/list_{base_url_info['list_param']}_{page}.html"
            
            print(f"正在請求URL: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            
            # 使用正確的編碼方式
            response.encoding = 'gbk'  # 修改為 GBK 編碼
            
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
        
        soup = BeautifulSoup(html, 'html.parser', from_encoding='gbk')
        stamps = []
        
        for item in soup.find_all('div', class_='goodsItem'):
            try:
                # 找到圖片URL
                img_elem = item.find('img')
                img_url = None
                if img_elem and img_elem.get('src'):
                    img_url = 'http://www.518yp.com' + img_elem['src']
                
                # 找到標題並轉換編碼
                title_elem = item.find('strong')
                if not title_elem:
                    continue
                    
                title = title_elem.text.strip()
                
                # 從標題或志號欄位找出編號
                series_code = None
                
                # 1. 先從 p 標籤中找編號
                for p in item.find_all('p'):
                    if p.string and '志号：' in p.string:
                        code_text = p.text.replace('志号：', '').strip()
                        if code_text:
                            series_code = code_text
                        break
                
                # 2. 如果找不到,從標題提取
                if not series_code:
                    patterns = [
                        r'[JT]\d+M?',  # 匹配 J123/T123 或 J123M/T123M
                        r'[特文纪]\d+M?',  # 匹配其他系列
                        r'[普改航欠军包]\d+[甲乙丙]?',  # 匹配普改航欠軍包系列
                        r'^\d+(-\d+)?$'  # 匹配純數字編號
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, title)
                        if match:
                            series_code = match.group()
                            break
                
                # 處理純數字編號（編號票）
                if series_code and series_code.replace('-', '').isdigit():
                    series_code = f"编{series_code}"
                
                # 找到價格
                price_elem = item.find('font', class_='shop_s')
                price = None
                if price_elem:
                    price_text = price_elem.text.replace('￥', '').strip()
                    try:
                        price = float(price_text)
                    except ValueError:
                        print(f"無法解析價格文本 '{price_text}'")
                        continue
                
                # 檢查是否為普改航欠軍包系列的網頁
                is_pghjqjb_page = 'pugaihangqianjunbao' in soup.find('link', rel='canonical').get('href', '')
                
                # 如果是普改航欠軍包系列的網頁，只處理相關郵票
                if is_pghjqjb_page and not (series_code and series_code[0] in ['普', '改', '航', '欠', '军', '包']):
                    continue
                
                if title and series_code and price is not None:
                    # 為所有郵票生成唯一鍵
                    unique_key = self.generate_unique_key(series_code, title)
                    
                    # 下載圖片
                    local_img_path = None
                    if img_url:
                        local_img_path = self.download_image(img_url, unique_key)
                    
                    stamp = {
                        'unique_key': unique_key,
                        'series': series_code,
                        'series_type': self.determine_series_type(series_code),
                        'title': title,
                        'price': price,
                        'date': datetime.now().strftime('%Y-%m-%d'),
                        'image_path': local_img_path
                    }
                    stamps.append(stamp)
                    print(f"成功解析郵票: {stamp}")
                else:
                    missing = []
                    if not title:
                        missing.append("標題")
                    if not series_code:
                        missing.append("編號")
                    if price is None:
                        missing.append("價格")
                    print(f"缺少數據: {', '.join(missing)}")
                    print(f"當前解析數據: 標題={title}, 編號={series_code}, 價格={price}")
                    
            except Exception as e:
                print(f"解析郵票項目時發生錯誤: {e}")
                continue
                
        print(f"本頁面共解析到 {len(stamps)} 個郵票數據")
        return stamps
    
    def determine_series_type(self, series_code):
        """根據編號判斷系列類型"""
        if not series_code:
            return None
            
        prefix_map = {
            'J': '纪字号',
            'T': '特字号', 
            '特': '特种邮票',
            '文': '文字号',
            '编': '编年号',
            '纪': '纪念邮票'
        }
        
        first_char = series_code[0]
        return prefix_map.get(first_char, '其他')
    
    def save_data(self, stamps):
        """修改保存數據的邏輯，使用唯一鍵作為索引"""
        if not stamps:
            print("警告：沒有郵票數據可保存")
            return
    
        try:
            # 讀取現有數據
            try:
                with open('data/stamps_data.json', 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                existing_data = {}
    
            current_date = datetime.now().strftime('%Y-%m-%d')
            updates_count = 0
    
            # 處理每個新抓取的郵票數據
            for new_stamp in stamps:
                unique_key = new_stamp['unique_key']
                new_price = new_stamp['price']
    
                # 如果是新郵票，創建基本結構
                if unique_key not in existing_data:
                    existing_data[unique_key] = {
                        'title': new_stamp['title'],
                        'series': new_stamp['series'],
                        'series_type': new_stamp['series_type'],
                        'image_path': new_stamp.get('image_path'),
                        'price_history': [],
                        'min_price': new_price,
                        'max_price': new_price,
                        'latest_price': new_price
                    }
    
                stamp_data = existing_data[unique_key]
                
                # 檢查最新價格是否有變化
                if not stamp_data['price_history'] or stamp_data['latest_price'] != new_price:
                    # 更新價格歷史
                    price_record = {
                        'date': current_date,
                        'price': new_price
                    }
                    stamp_data['price_history'].append(price_record)
                    stamp_data['latest_price'] = new_price
                    
                    # 更新最高最低價
                    stamp_data['min_price'] = min(stamp_data['min_price'], new_price)
                    stamp_data['max_price'] = max(stamp_data['max_price'], new_price)
                    
                    updates_count += 1
                    print(f"更新郵票價格 - 系列: {series_code}, 新價格: {new_price}")
                
            if updates_count > 0:
                with open('data/stamps_data.json', 'w', encoding='utf-8') as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=2)
                print(f"成功更新 {updates_count} 個郵票的價格資料")
                return True
            else:
                print("沒有發現價格變動，不進行更新")
                return False
    
        except Exception as e:
            print(f"保存數據時發生錯誤: {e}")
            return False
def main():
    scraper = StampScraper()
    stamps = scraper.scrape_all_series()
    scraper.save_data(stamps)

if __name__ == "__main__":
    main()
