import os
from datetime import datetime
import requests
import pandas as pd
import pdfplumber
from io import BytesIO
from dotenv import load_dotenv
import asyncio
from crawl4ai import AsyncWebCrawler
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
import time
from contextlib import asynccontextmanager
from bs4 import BeautifulSoup
import aiohttp
import ssl
import threading
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAX_PDF_SIZE = 50 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_TEXT_LENGTH = 500000
MAX_URL_CONTENT_SIZE = 10 * 1024 * 1024
BATCH_SIZE = 10
CRAWLER_TIMEOUT = 45
PDF_TIMEOUT = 120
URL_TIMEOUT = 60
API_RETRY_ATTEMPTS = 3
API_TIMEOUT = 60

class TimeoutException(Exception):
    pass

class UserInputCollector:
    @staticmethod
    def get_keywords():
        print("Enter keywords (separated by /):")
        print("Type 'END' when done\n")
        keyword_list = []
        while True:
            line = input("➤ Keywords (or 'END'): ").strip()
            if line.upper() == "END":
                break
            if line:
                keyword_list.extend([kw.strip() for kw in line.split("/") if kw.strip()])
        return keyword_list

    @staticmethod
    def get_date():
        while True:
            user_input = input("Date (YYYY-MM-DD): ").strip()
            try:
                return datetime.strptime(user_input, "%Y-%m-%d").date()
            except ValueError:
                print("Invalid format. Try again.")

    @staticmethod
    def get_filetype():
        while True:
            choice = input("File type (PDF/URL): ").strip().upper()
            if choice in ("PDF", "URL"):
                return choice
            print("Enter PDF or URL")

    @staticmethod
    def get_country_code():
        while True:
            code = input("Country code (2 letters, e.g. 'us', 'gb', 'fr'): ").strip().lower()
            if len(code) == 2 and code.isalpha():
                return code
            print("Must be 2 letters")

    @staticmethod
    def get_num_results():
        while True:
            num = input("Number of results (1-100): ").strip()
            try:
                num = int(num)
                if 1 <= num <= 100:
                    return num
                print("Must be 1-100")
            except ValueError:
                print("Invalid number")

    @staticmethod
    def get_num_pages():
        while True:
            pages = input("Pages to search (1-50): ").strip()
            try:
                pages = int(pages)
                if 1 <= pages <= 50:
                    return pages
                print("Must be 1-50")
            except ValueError:
                print("Invalid number")
    
    @staticmethod
    def get_company_info():
        print("\nCompany information:")
        company_name = input("Company name: ").strip()
        print("Description (Enter on empty line when done):")
        
        lines = []
        while True:
            line = input()
            if not line.strip():
                if lines:
                    break
                continue
            lines.append(line)
        
        company_desc = "\n".join(lines).strip()
        return company_name, company_desc

class TimeoutHandler:
    @staticmethod
    def run_with_timeout(func, timeout_seconds, *args, **kwargs):
        result = [None]
        exception = [None]
        
        def target():
            try:
                result[0] = func(*args, **kwargs)
            except Exception as e:
                exception[0] = e
        
        thread = threading.Thread(target=target)
        thread.daemon = True
        thread.start()
        thread.join(timeout_seconds)
        
        if thread.is_alive():
            raise TimeoutException(f"Timeout after {timeout_seconds}s")
        
        if exception[0]:
            raise exception[0]
        
        return result[0]

class ContentExtractor:
    @staticmethod
    def extract_from_pdf(url):
        try:
            print(f"Downloading PDF: {url}")
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            
            resp = requests.get(url, timeout=45, headers=headers, stream=True, verify=False)
            if resp.status_code != 200:
                print(f"Download failed: {resp.status_code}")
                return ""
            
            content_type = resp.headers.get('content-type', '').lower()
            if 'pdf' not in content_type and not url.lower().endswith('.pdf'):
                print(f"Not PDF: {content_type}")
                return ""
            
            pdf_bytes = resp.content
            
            if not pdf_bytes.startswith(b'%PDF'):
                print("Invalid PDF")
                return ""
                
            if len(pdf_bytes) > MAX_PDF_SIZE:
                print(f"PDF too large")
                return ""
                
            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                texts = []
                max_pages = min(len(pdf.pages), MAX_PDF_PAGES)
                
                for i, page in enumerate(pdf.pages[:max_pages]):
                    if i > 0 and i % 10 == 0:
                        print(f"Page {i+1}/{max_pages}")
                    
                    page_text = page.extract_text()
                    if page_text:
                        texts.append(page_text)
                        
                    current_text = "\n".join(texts)
                    if len(current_text) > MAX_TEXT_LENGTH:
                        print("Text limit reached")
                        break
            
            text = "\n".join(texts).strip()
            
            if text:
                actual_length = len(text.replace('\n', '').replace(' ', ''))
                print(f"Extracted: {len(text)} chars")
                return text[:MAX_TEXT_LENGTH]
            
            print("No text found")
            return ""
                
        except Exception as e:
            print(f"PDF error: {e}")
            return ""

    @staticmethod
    async def extract_fallback(url):
        try:
            print(f"Using fallback for: {url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            
            connector = aiohttp.TCPConnector(ssl=ssl.create_default_context())
            timeout = aiohttp.ClientTimeout(total=30)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        print(f"Status {response.status}")
                        return ""
                    
                    content_type = response.headers.get('content-type', '').lower()
                    if 'text/html' not in content_type:
                        print(f"Not HTML: {content_type}")
                        return ""
                    
                    content = await response.read()
                    if len(content) > MAX_URL_CONTENT_SIZE:
                        print("Content too large")
                        return ""
                    
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    for script in soup(["script", "style", "nav", "footer", "header"]):
                        script.decompose()
                    
                    text = soup.get_text(separator='\n', strip=True)
                    
                    if text and len(text.strip()) > 50:
                        print(f"Extracted {len(text)} chars")
                        return text[:300000]
                    
                    return ""
                    
        except Exception as e:
            print(f"Fallback error: {e}")
            return ""

    @staticmethod
    @asynccontextmanager
    async def get_crawler():
        crawler = None
        try:
            crawler = AsyncWebCrawler(
                headless=True,
                verbose=False,
                always_by_pass_cache=True,
                browser_type="chromium",
                page_timeout=30000,
                request_timeout=30000,
            )
            await crawler.start()
            yield crawler
        except Exception as e:
            print(f"Crawler init failed: {e}")
            yield None
        finally:
            if crawler:
                try:
                    await crawler.close()
                except:
                    pass

    @staticmethod
    async def extract_from_url(url):
        try:
            print(f"Crawling: {url}")
            
            async with ContentExtractor.get_crawler() as crawler:
                if crawler is None:
                    print("Using fallback")
                    return await ContentExtractor.extract_fallback(url)
                
                try:
                    result = await asyncio.wait_for(
                        crawler.arun(url), 
                        timeout=CRAWLER_TIMEOUT
                    )
                    
                    if isinstance(result, dict):
                        text = result.get("text", "")
                    elif isinstance(result, list) and result:
                        text = result[0].get("text", "")
                    else:
                        text = str(result)
                    
                    if text and len(text.strip()) > 50:
                        if len(text) > 800000:
                            print(f"Truncating ({len(text)} chars)")
                            text = text[:800000]
                        
                        print(f"Crawled {len(text)} chars")
                        return text
                    else:
                        print("No content, trying fallback")
                        return await ContentExtractor.extract_fallback(url)
                        
                except asyncio.TimeoutError:
                    print("Timeout, trying fallback")
                    return await ContentExtractor.extract_fallback(url)
                except Exception as e:
                    print(f"Error, trying fallback")
                    return await ContentExtractor.extract_fallback(url)
                    
        except Exception as e:
            print(f"Extraction failed: {e}")
            return ""

class AIAnalyzer:
    def __init__(self, api_key):
        self.api_key = api_key
        self.url = "https://api.deepseek.com/v1/chat/completions"

    def build_prompt(self, company, desc, rfp_content):
        truncated_content = rfp_content[:6000] if len(rfp_content) > 6000 else rfp_content
        
        return f"""Analyze this RFP for {company}.

Company: {company}
Description: {desc[:10000]}

RFP Content:
{truncated_content}

Is this RFP relevant for our company?
Answer: Yes/No
Reason: [2 sentences]
"""

    def query_api(self, prompt):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        if len(prompt) > 20000:
            prompt = prompt[:20000]
        
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1000,
            "stream": False
        }
        
        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                print(f"API request ({attempt + 1}/{API_RETRY_ATTEMPTS})")
                response = requests.post(self.url, headers=headers, json=data, timeout=API_TIMEOUT)
                          
                if response.status_code == 401:
                    return "API Error: Invalid key"
                
                response.raise_for_status()
                result = response.json()
                
                if 'choices' in result and result['choices']:
                    content = result['choices'][0]['message']['content']
                    
                    if 'usage' in result:
                        usage = result['usage']
                        print(f"Tokens: {usage.get('prompt_tokens', 0)} / {usage.get('completion_tokens', 0)}")
                    
                    return content
                else:
                    return "API Error: Bad response"
                    
            except requests.exceptions.Timeout:
                if attempt == API_RETRY_ATTEMPTS - 1:
                    return "API Error: Timeout"
                time.sleep(3)
            except Exception as e:
                if attempt == API_RETRY_ATTEMPTS - 1:
                    return f"API Error: {str(e)}"
                time.sleep(3)
        
        return "API Error: Failed"

    def analyze_rfp(self, row, company_name, company_desc, worker_id):
        title, link, snippet, date, full_content, kw = row
        print(f"[{worker_id}] {title[:50]}...")
        
        if worker_id > 0:
            time.sleep(1)
        
        try:
            def ai_analysis():
                prompt = self.build_prompt(company_name, company_desc, full_content)
                return self.query_api(prompt)
            
            ai_answer = TimeoutHandler.run_with_timeout(ai_analysis, 120)
            
            return {
                "keyword": kw,
                "title": title,
                "link": link,
                "snippet": snippet,
                "date": date,
                "full_content": full_content[:2000],
                "ai_answer": ai_answer,
                "ai_status": "Success" if "Error:" not in ai_answer else "Failed"
            }
            
        except TimeoutException:
            return {
                "keyword": kw,
                "title": title,
                "link": link,
                "snippet": snippet,
                "date": date,
                "full_content": full_content[:2000],
                "ai_answer": "Timeout",
                "ai_status": "Timeout"
            }
        except Exception as e:
            return {
                "keyword": kw,
                "title": title,
                "link": link,
                "snippet": snippet,
                "date": date,
                "full_content": full_content[:2000],
                "ai_answer": f"Error: {str(e)}",
                "ai_status": "Error"
            }

class BatchProcessor:
    @staticmethod
    async def extract_items(items):
        extracted = []
        failed_extractions = []
        
        async def extract_single_item(item_data, item_index):
            title, link, snippet, date, kw = item_data
            try:
                print(f"\n[{item_index+1}/{len(items)}] {title[:60]}")
                print(f"   {link}")
                
                if link.lower().endswith(".pdf"):
                    loop = asyncio.get_event_loop()
                    content = await asyncio.wait_for(
                        loop.run_in_executor(None, ContentExtractor.extract_from_pdf, link),
                        timeout=PDF_TIMEOUT
                    )
                else:
                    content = await asyncio.wait_for(
                        ContentExtractor.extract_from_url(link),
                        timeout=URL_TIMEOUT
                    )
                
                if content:
                    stripped_content = content.strip()
                    actual_content = stripped_content.replace('\n', '').replace(' ', '')
                    
                    print(f"   Content: {len(actual_content)} chars")
                    
                    if len(actual_content) > 100:
                        print(f"   ✓ Added")
                        return (title, link, snippet, date, stripped_content, kw)
                    else:
                        failed_extractions.append((title, link, f"Too short ({len(actual_content)})"))
                        return None
                else:
                    failed_extractions.append((title, link, "No content"))
                    return None
                    
            except asyncio.TimeoutError:
                failed_extractions.append((title, link, "Timeout"))
                return None
            except Exception as e:
                failed_extractions.append((title, link, str(e)[:50]))
                return None
        
        print(f"\nExtracting {len(items)} items (batch size {BATCH_SIZE})...")
        
        for batch_start in range(0, len(items), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(items))
            batch_items = items[batch_start:batch_end]
            
            print(f"\nBatch {batch_start//BATCH_SIZE + 1}/{(len(items) + BATCH_SIZE - 1)//BATCH_SIZE}")
            
            tasks = []
            for i, item in enumerate(batch_items):
                global_index = batch_start + i
                task = extract_single_item(item, global_index)
                tasks.append(task)
            
            try:
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in batch_results:
                    if isinstance(result, Exception):
                        print(f"Exception: {result}")
                    elif result is not None:
                        extracted.append(result)
                
                batch_extracted = len([r for r in batch_results if r is not None and not isinstance(r, Exception)])
                print(f"Batch done: {batch_extracted}/{len(batch_items)}")
                
            except Exception as e:
                print(f"Batch error: {e}")
        
        print(f"\nExtraction complete:")
        print(f"   Success: {len(extracted)}")
        print(f"   Failed: {len(failed_extractions)}")
        
        if failed_extractions:
            print(f"\nFailed:")
            for title, link, reason in failed_extractions[:10]:
                print(f"   {title[:50]}... ({reason})")
            if len(failed_extractions) > 10:
                print(f"   ...and {len(failed_extractions) - 10} more")
        
        return extracted, failed_extractions

class SearchEngine:
    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {"X-API-KEY": api_key}

    def search_keyword(self, keyword, filetype, start_date, country, num_results, num_pages):
        print(f"\nSearching: '{keyword}'")
        
        q = f"{keyword} filetype:{filetype.lower()}" if filetype == "PDF" else keyword
        q += f" after:{start_date}"
        payload = {"q": q, "gl": country, "num": num_results, "page": num_pages}
        
        try:
            response = requests.post(
                "https://google.serper.dev/search",
                json=payload, 
                headers=self.headers, 
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Search failed: {str(e)[:100]}")
            return None

    def filter_results(self, results, filetype):
        items = []
        organic_results = results.get("organic", [])
        print(f"Found {len(organic_results)} results")
        
        for item in organic_results:
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            date = item.get("date", "")

            if filetype == "URL" and link.lower().endswith(".pdf"):
                continue
            if filetype == "PDF" and not link.lower().endswith(".pdf"):
                continue

            items.append((title, link, snippet, date))

        print(f"Filtered: {len(items)}")
        return items

class ReportGenerator:
    @staticmethod
    def save_results(all_results, all_failed_extractions):
        if not all_results and not all_failed_extractions:
            print("No data to save")
            return ReportGenerator._save_empty_file()
        
        try:
            if all_results:
                df = pd.DataFrame(all_results)
            else:
                df = pd.DataFrame(columns=['keyword', 'title', 'link', 'snippet', 'date', 'full_content', 'ai_answer', 'ai_status'])
            
            if all_failed_extractions:
                failed_summary = []
                for title, link, reason in all_failed_extractions:
                    failed_summary.append({
                        'keyword': 'N/A',
                        'title': title,
                        'link': link,
                        'snippet': '',
                        'date': '',
                        'full_content': f'FAILED: {reason}',
                        'ai_answer': 'N/A',
                        'ai_status': 'Failed Extraction'
                    })
                
                if not df.empty:
                    df = pd.concat([df, pd.DataFrame(failed_summary)], ignore_index=True)
                else:
                    df = pd.DataFrame(failed_summary)
            
            filename = f'rfp_analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            
            with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='RFP_Analysis')
                worksheet = writer.sheets['RFP_Analysis']
                workbook = writer.book
                
                for i, col in enumerate(df.columns):
                    max_len = max(df[col].astype(str).map(len).max(), len(col))
                    worksheet.set_column(i, i, min(50, max(15, max_len)))
                
                success_format = workbook.add_format({'bg_color': '#90EE90'})
                fail_format = workbook.add_format({'bg_color': '#FFB6C1'})
                extraction_fail_format = workbook.add_format({'bg_color': '#FF6B6B'})
                yes_format = workbook.add_format({'bg_color': '#00FF00'})
                
                if 'ai_answer' in df.columns:
                    answer_col = df.columns.get_loc('ai_answer')
                    for row_num in range(1, len(df) + 1):
                        answer_value = str(df.iloc[row_num - 1]['ai_answer'])
                        if answer_value.startswith('Answer: Yes'):
                            worksheet.write(row_num, answer_col, answer_value, yes_format)
                
                if 'ai_status' in df.columns:
                    status_col = df.columns.get_loc('ai_status')
                    worksheet.conditional_format(1, status_col, len(df), status_col, 
                                               {'type': 'text', 'criteria': 'containing', 'value': 'Success', 'format': success_format})
                    worksheet.conditional_format(1, status_col, len(df), status_col, 
                                               {'type': 'text', 'criteria': 'containing', 'value': 'Failed', 'format': fail_format})
                    worksheet.conditional_format(1, status_col, len(df), status_col, 
                                               {'type': 'text', 'criteria': 'containing', 'value': 'Failed Extraction', 'format': extraction_fail_format})
                
                if all_failed_extractions:
                    failed_df = pd.DataFrame(all_failed_extractions, columns=['Title', 'Link', 'Reason'])
                    failed_df.to_excel(writer, index=False, sheet_name='Failed_Extractions')
                    
                    failed_worksheet = writer.sheets['Failed_Extractions']
                    for i, col in enumerate(failed_df.columns):
                        max_len = max(failed_df[col].astype(str).map(len).max(), len(col))
                        failed_worksheet.set_column(i, i, min(80, max(15, max_len)))
            
            print(f"Saved {len(df)} rows to {filename}")
            
            ReportGenerator._print_summary(df, all_results, all_failed_extractions)
            
            return filename
            
        except Exception as e:
            print(f"Save error: {e}")
            ReportGenerator._save_csv_fallback(all_results)
            return None

    @staticmethod
    def _print_summary(df, all_results, all_failed_extractions):
        success_count = sum(1 for _, row in df.iterrows() if row.get('ai_status') == 'Success')
        yes_count = sum(1 for _, row in df.iterrows() if 'Answer: Yes' in str(row.get('ai_answer', '')) and row.get('ai_status') == 'Success')
        
        print(f"\nSummary:")
        print(f"  Total: {len(df)}")
        print(f"  Extracted: {len(all_results)}")
        print(f"  Failed extraction: {len(all_failed_extractions)}")
        print(f"  AI success: {success_count}")
        print(f"  AI failed: {len(all_results) - success_count if all_results else 0}")
        print(f"  Matches: {yes_count}")

    @staticmethod
    def _save_csv_fallback(all_results):
        try:
            df = pd.DataFrame(all_results) if all_results else pd.DataFrame()
            csv_filename = f'rfp_analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            df.to_csv(csv_filename, index=False)
            print(f"Saved CSV: {csv_filename}")
        except:
            print("Could not save")

    @staticmethod
    def _save_empty_file():
        try:
            metadata = {
                "keyword": ["No results"],
                "title": ["No results"],
                "link": [""],
                "snippet": [""],
                "date": [""],
                "full_content": [""],
                "ai_answer": [""],
                "ai_status": ["N/A"]
            }
            df = pd.DataFrame(metadata)
            filename = f'rfp_analysis_empty_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            df.to_excel(filename, index=False)
            print(f"Empty file: {filename}")
            return filename
        except Exception as e:
            print(f"Save error: {e}")
            return None

def main():
    print("RFP Analysis Tool\n")
    
    load_dotenv()
    
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
    serper_api_key = os.getenv("SERPER_API_KEY")
    
    if not deepseek_api_key:
        print("DEEPSEEK_API_KEY not in .env")
        return
    
    if not serper_api_key:
        print("SERPER_API_KEY not in .env")
        return
    
    collector = UserInputCollector()
    
    keywords = collector.get_keywords()
    if not keywords:
        print("No keywords")
        return
        
    start_date = collector.get_date()
    filetype = collector.get_filetype()
    country = collector.get_country_code()
    num_results = collector.get_num_results()
    num_pages = collector.get_num_pages()
    company_name, company_desc = collector.get_company_info()
    
    print(f"\n{'='*60}")
    print(f"Configuration:")
    print(f"{'='*60}")
    print(f"Keywords: {keywords}")
    print(f"Date: {start_date}")
    print(f"Type: {filetype}")
    print(f"Country: {country}")
    print(f"Results: {num_results}")
    print(f"Pages: {num_pages}")
    print(f"Company: {company_name}")
    print(f"{'='*60}\n")
    
    search_engine = SearchEngine(serper_api_key)
    
    all_items = []
    for keyword in keywords:
        results = search_engine.search_keyword(keyword, filetype, start_date, country, num_results, num_pages)
        
        if results:
            items = search_engine.filter_results(results, filetype)
            items_with_kw = [(title, link, snippet, date, keyword) for title, link, snippet, date in items]
            all_items.extend(items_with_kw)
        else:
            print(f"No results for: {keyword}")
    
    if not all_items:
        print("\nNo results")
        ReportGenerator._save_empty_file()
        return
    
    print(f"\n{'='*60}")
    print(f"Items to process: {len(all_items)}")
    print(f"{'='*60}\n")
    
    print("Extracting content...")
    try:
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            extracted_items, failed_extractions = loop.run_until_complete(
                BatchProcessor.extract_items(all_items)
            )
        finally:
            loop.close()
    except Exception as e:
        print(f"Extraction error: {e}")
        import traceback
        traceback.print_exc()
        extracted_items = []
        failed_extractions = []
    
    if not extracted_items:
        print("\nNo content extracted")
        ReportGenerator.save_results([], failed_extractions)
        return
    
    print(f"\n{'='*60}")
    print(f"Extracted: {len(extracted_items)}")
    print(f"{'='*60}\n")
    
    print("Running AI analysis...")
    analyzer = AIAnalyzer(deepseek_api_key)
    all_results = []
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(analyzer.analyze_rfp, item, company_name, company_desc, i): item
            for i, item in enumerate(extracted_items)
        }
        
        for future in as_completed(futures):
            try:
                result = future.result(timeout=180)
                all_results.append(result)
                print(f"Done: {len(all_results)}/{len(extracted_items)}")
            except FutureTimeoutError:
                item = futures[future]
                all_results.append({
                    "keyword": item[5],
                    "title": item[0],
                    "link": item[1],
                    "snippet": item[2],
                    "date": item[3],
                    "full_content": item[4][:2000],
                    "ai_answer": "Timeout",
                    "ai_status": "Timeout"
                })
            except Exception as e:
                item = futures[future]
                all_results.append({
                    "keyword": item[5],
                    "title": item[0],
                    "link": item[1],
                    "snippet": item[2],
                    "date": item[3],
                    "full_content": item[4][:2000],
                    "ai_answer": f"Error: {str(e)}",
                    "ai_status": "Error"
                })
    
    print("\nSaving...")
    filename = ReportGenerator.save_results(all_results, failed_extractions)
    
    if filename:
        print(f"\n{'='*60}")
        print(f"Complete: {filename}")
        print(f"{'='*60}\n")
    else:
        print("\nSave failed")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted")
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()