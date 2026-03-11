import requests
from bs4 import BeautifulSoup
import sqlite3
import json
import re
from datetime import datetime
import time
import random
import os
from urllib.parse import urljoin

class StampScraperV2:
    def __init__(self, db_path='data/stamps.db'):
        self.db_path = db_path
        self.base_urls = {
            'JT': {
                'url': 'http://www.518yp.com/JTxilie',
                'list_param': '90',
                'price_table_url': 'http://www.518yp.com/youpiaojiagebiao/6476.html'
            },
            'WB': {
                'url': 'http://www.518yp.com/wbypiao',
                'list_param': '84',
                'price_table_url': None
            },
            'LJT': {
                'url': 'http://www.518yp.com/ljt',
                'list_param': '82',
                'price_table_url': None
            },
            'M': {
                'url': 'http://www.518yp.com/xiaoxingzhang',
                'list_param': '117',
                'price_table_url': 'http://www.518yp.com/youpiaojiagebiao/6481.html'
            },
            'PGHJQJB': {
                'url': 'http://www.518yp.com/pugaihangqianjunbao',
                'list_param': '135',
                'price_table_url': None
            }
        }
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Referer': 'http://www.518yp.com/'
        }
        
        self.img_dir = './img'
        os.makedirs(self.img_dir, exist_ok=True)
    
    def get_db_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def get_page_content(self, url):
        """Fetch page content"""
        try:
            print(f"  → Fetching: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'gbk'
            
            if response.status_code != 200:
                print(f"  ✗ Status code: {response.status_code}")
                return None
            
            return response.text
        except Exception as e:
            print(f"  ✗ Request failed: {e}")
            return None
    
    def parse_price_table(self, html, series_key=''):
        """Parse HTML price table and extract stamp data with variants"""
        if not html:
            return []
        
        soup = BeautifulSoup(html, 'html.parser', from_encoding='gbk')
        stamps = []
        
        # Find all tables
        tables = soup.find_all('table', {'border': '1'})
        
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue
            
            # Get table headers
            header_row = rows[0]
            headers = [th.text.strip() for th in header_row.find_all('td')]
            
            # Parse data rows
            for row in rows[1:]:
                cells = row.find_all('td')
                if len(cells) < 2:
                    continue
                
                try:
                    # Extract catalog number and title
                    catalog_text = cells[0].text.strip()
                    catalog_number = self._extract_catalog_number(catalog_text)
                    
                    if not catalog_number:
                        continue
                    
                    # Extract variants (different columns = different conditions)
                    variants = {} 
                    
                    for idx, cell in enumerate(cells[1:], start=1):
                        if idx < len(headers):
                            condition = self._normalize_condition(headers[idx])
                            try:
                                price = float(cell.text.strip())
                                variants[condition] = price
                            except (ValueError, AttributeError):
                                continue
                    
                    if variants:
                        stamps.append({
                            'catalog_number': catalog_number,
                            'title': catalog_text,
                            'variants': variants
                        })
                        print(f"✓ Parsed: {catalog_number}")
                
                except Exception as e:
                    print(f"✗ Parse error: {e}")
                    continue
        
        return stamps
    
    def _extract_catalog_number(self, text):
        """Extract catalog number from text"""
        patterns = [
            r'^(00)(?:[年月]|个?[纪编特JT])',
            r'^([JT]00M?)',
            r'^(M00)',
            r'^(00)(?:[甲乙丙])?(?:-00)?$'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                code = match.group(1)
                if code.isdigit():
                    code = f"编{code}"
                return code
        
        return None
    
    def _normalize_condition(self, header_text):
        """Normalize condition name"""
        mapping = {
            '全集': 'set',
            '郵票單枚': 'single',
            '邮票单枚': 'single',
            '四方連': 'block4',
            '四方连': 'block4',
            '單張': 'sheet',
            '单张': 'sheet'
        }
        
        for key, value in mapping.items():
            if key in header_text:
                return value
        
        return header_text.replace('郵票', '').replace('邮票', '').strip()
    
    def save_to_database(self, stamps_data):
        """Save stamp data to SQLite"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        current_date = datetime.now().isoformat()
        updates_count = 0
        
        try:
            for stamp in stamps_data:
                catalog_number = stamp['catalog_number']
                
                # Insert or ignore stamp record
                cursor.execute('''
                    INSERT OR IGNORE INTO stamps (catalog_number, title, source_url)
                    VALUES (?, ?, ?)
                ''', (catalog_number, stamp.get('title', ''), ''))
                
                cursor.execute('SELECT id FROM stamps WHERE catalog_number = ?', 
                              (catalog_number,))
                result = cursor.fetchone()
                if not result:
                    continue
                stamp_id = result[0]
                
                # Process each variant
                for condition, price in stamp.get('variants', {}).items():
                    variant_key = f"{catalog_number}_{condition}"
                    
                    # Insert variant
                    cursor.execute('''
                        INSERT OR IGNORE INTO variants (stamp_id, condition, variant_key)
                        VALUES (?, ?, ?)
                    ''', (stamp_id, condition, variant_key))
                    
                    cursor.execute('SELECT id FROM variants WHERE variant_key = ?', 
                                  (variant_key,))
                    var_result = cursor.fetchone()
                    if not var_result:
                        continue
                    variant_id = var_result[0]
                    
                    # Check if price changed
                    cursor.execute('''
                        SELECT price FROM price_history 
                        WHERE variant_id = ? 
                        ORDER BY recorded_at DESC 
                        LIMIT 1
                    ''', (variant_id,))
                    
                    last_record = cursor.fetchone() 
                    last_price = last_record[0] if last_record else None
                    
                    if last_price != price:
                        cursor.execute('''
                            INSERT INTO price_history (variant_id, price, recorded_at)
                            VALUES (?, ?, ?)
                        ''', (variant_id, price, current_date))
                        
                        updates_count += 1
                        print(f"✓ Updated: {catalog_number} ({condition}) = ¥{price}")
            
            conn.commit()
            print(f"\n✓ Successfully updated {updates_count} price records")
            
        except Exception as e:
            conn.rollback()
            print(f"✗ Database error: {e}")
        finally:
            conn.close()
    
    def scrape_series(self, series_key):
        """Scrape single series"""
        url_info = self.base_urls.get(series_key)
        if not url_info:
            print(f"✗ Unknown series: {series_key}")
            return []
        
        all_stamps = []
        
        # Try to scrape price table first
        if url_info.get('price_table_url'):
            print(f"\n📥 Scraping {series_key} price table...")
            html = self.get_page_content(url_info['price_table_url'])
            if html:
                stamps = self.parse_price_table(html, series_key)
                all_stamps.extend(stamps)
        
        return all_stamps
    
    def scrape_all(self):
        """Scrape all series"""
        all_stamps = []
        for series_key in self.base_urls.keys():
            stamps = self.scrape_series(series_key)
            all_stamps.extend(stamps)
            time.sleep(3)
        
        return all_stamps


def main():
    # Initialize database
    from scripts.init_db import init_database
    init_database()
    
    # Scrape data
    scraper = StampScraperV2()
    stamps = scraper.scrape_all()
    
    # Save to database
    scraper.save_to_database(stamps)
    print("\n✓ Scraping completed!")


if __name__ == '__main__':
    main()