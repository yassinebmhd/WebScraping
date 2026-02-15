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

# Configuration Constants
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

# Input Collection Module
class UserInputCollector:
    @staticmethod
    def get_keywords():
        print("Enter your keywords separated by slashes (/).")
        print("Type 'END' when you're done.\n")
        keyword_list = []
        while True:
            line = input("➤ Enter keywords (or 'END' to finish): ").strip()
            if line.upper() == "END":
                break
            if line:
                keyword_list.extend([kw.strip() for kw in line.split("/") if kw.strip()])
        return keyword_list

    @staticmethod
    def get_date():
        while True:
            user_input = input("Please enter a date in YYYY-MM-DD format: ").strip()
            try:
                return datetime.strptime(user_input, "%Y-%m-%d").date()
            except ValueError:
                print("Invalid format. Please try again using YYYY-MM-DD (e.g. 2025-07-09).")

    @staticmethod
    def get_filetype():
        while True:
            choice = input("Enter the file type for your search (PDF or URL): ").strip().upper()
            if choice in ("PDF", "URL"):
                return choice
            print("Invalid choice. Please enter either 'PDF' or 'URL'.")

    @staticmethod
    def get_country_code():
        reference_url = "https://serpapi.com/google-countries"
        prompt = (
            f"Enter the 2-letter Google country code for your search (e.g. 'fr', 'us', 'gb').\n"
            f"If you don't know the code, visit: {reference_url}\n➤ "
        )
        while True:
            code = input(prompt).strip().lower()
            if len(code) == 2 and code.isalpha():
                return code
            print("Invalid code. It must be exactly two letters (e.g. 'fr'). Try again.\n")

    @staticmethod
    def get_num_results():
        while True:
            num = input("Enter number of results (1-100): ").strip()
            try:
                num = int(num)
                if 1 <= num <= 100:
                    return num
                print("Number must be between 1-100.")
            except ValueError:
                print("Invalid number. Try again.")

    @staticmethod
    def get_num_pages():
        while True:
            pages = input("Enter number of pages to search (1–50): ").strip()
            try:
                pages = int(pages)
                if 1 <= pages <= 50:
                    return pages
                print("Please enter a number between 1 and 50.")
            except ValueError:
                print("Invalid input. Please enter a valid number.")

# Timeout Handler Module
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
            raise TimeoutException(f"Operation timed out after {timeout_seconds} seconds")
        
        if exception[0]:
            raise exception[0]
        
        return result[0]

# Content Extraction Module
class ContentExtractor:
    @staticmethod
    def extract_from_pdf(url):
        try:
            print(f"Downloading PDF from: {url}")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            resp = requests.get(url, timeout=45, headers=headers, stream=True, verify=False)
            if resp.status_code != 200:
                print(f"Could not download PDF: status code {resp.status_code}")
                return ""
            
            content_type = resp.headers.get('content-type', '').lower()
            if 'pdf' not in content_type and not url.lower().endswith('.pdf'):
                print(f"Content type is not PDF: {content_type}")
                return ""
            
            pdf_bytes = resp.content
            
            if not pdf_bytes.startswith(b'%PDF'):
                print("Downloaded file is not a valid PDF")
                return ""
                
            if len(pdf_bytes) > MAX_PDF_SIZE:
                print(f"PDF too large (>{MAX_PDF_SIZE // (1024*1024)}MB), skipping")
                return ""
                
            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                texts = []
                max_pages = min(len(pdf.pages), MAX_PDF_PAGES)
                
                for i, page in enumerate(pdf.pages[:max_pages]):
                    if i > 0 and i % 10 == 0:
                        print(f"Processing page {i+1}/{max_pages}")
                    
                    page_text = page.extract_text()
                    if page_text:
                        texts.append(page_text)
                        
                    current_text = "\n".join(texts)
                    if len(current_text) > MAX_TEXT_LENGTH:
                        print("Reached text limit, stopping page processing")
                        break
            
            text = "\n".join(texts).strip()
            
            if text:
                actual_length = len(text.replace('\n', '').replace(' ', ''))
                print(f"Successfully extracted text from PDF - Total: {len(text)} chars, Non-whitespace: {actual_length} chars")
                if actual_length < 100:
                    print("Warning: Extracted text has very little actual content")
                return text[:MAX_TEXT_LENGTH]
            
            print("No text found with pdfplumber")
            return ""
                
        except Exception as e:
            print(f"PDF extraction failed: {e}")
            return ""

    @staticmethod
    async def extract_fallback(url):
        try:
            print(f"Fallback extraction for: {url}")
            
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
                        print(f"Fallback failed: status {response.status}")
                        return ""
                    
                    content_type = response.headers.get('content-type', '').lower()
                    if 'text/html' not in content_type:
                        print(f"Not HTML content: {content_type}")
                        return ""
                    
                    content = await response.read()
                    if len(content) > MAX_URL_CONTENT_SIZE:
                        print("Content too large for fallback processing")
                        return ""
                    
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    for script in soup(["script", "style", "nav", "footer", "header"]):
                        script.decompose()
                    
                    text = soup.get_text(separator='\n', strip=True)
                    
                    if text and len(text.strip()) > 50:
                        print(f"Fallback extraction successful ({len(text)} characters)")
                        return text[:300000]
                    
                    return ""
                    
        except Exception as e:
            print(f"Fallback extraction failed: {e}")
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
            print(f"Failed to start crawler: {e}")
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
            print(f"Crawling URL: {url}")
            
            async with ContentExtractor.get_crawler() as crawler:
                if crawler is None:
                    print("Crawler initialization failed, trying fallback")
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
                            print(f"Large content detected ({len(text)} chars), truncating...")
                            text = text[:800000] + "\n[Content truncated due to size]"
                        
                        print(f"Successfully crawled URL ({len(text)} characters)")
                        return text
                    else:
                        print("No meaningful content from crawler, trying fallback")
                        return await ContentExtractor.extract_fallback(url)
                        
                except asyncio.TimeoutError:
                    print("Crawler timeout, trying fallback method")
                    return await ContentExtractor.extract_fallback(url)
                except Exception as e:
                    print(f"Crawler error: {str(e)[:200]}... trying fallback")
                    return await ContentExtractor.extract_fallback(url)
                    
        except Exception as e:
            print(f"URL extraction completely failed for {url}: {e}")
            return ""

# AI Analysis Module
class AIAnalyzer:
    def __init__(self, api_key):
        self.api_key = api_key
        self.url = "https://api.deepseek.com/v1/chat/completions"

    def build_prompt(self, company, desc, rfp_content):
        truncated_content = rfp_content[:6000] if len(rfp_content) > 6000 else rfp_content
        
        return f"""
[ROLE]
You are an expert in RFP analysis for SaaS and fintech companies.

[COMPANY INFO]
{company}

{desc[:10000]}

[RFP TEXT]
{truncated_content}

Analyze this RFP for {company}.
Is this RFP specifically targeting our solution? Respond in this exact format:
Answer: Yes/No  
Reason: [1-2 sentences referencing explicit matches/mismatches from the RFP text]
"""

    def query_api(self, prompt):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        if len(prompt) > 20000:
            prompt = prompt[:20000] + "\n[Content truncated for AI processing]"
        
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1000,
            "stream": False
        }
        
        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                print(f"Making DeepSeek API request (attempt {attempt + 1}/{API_RETRY_ATTEMPTS})")
                response = requests.post(self.url, headers=headers, json=data, timeout=API_TIMEOUT)
                          
                if response.status_code == 401:
                    return "API Error: Invalid API key. Please check your DeepSeek API key."
                
                response.raise_for_status()
                result = response.json()
                
                if 'choices' in result and result['choices']:
                    content = result['choices'][0]['message']['content']
                    
                    if 'usage' in result:
                        usage = result['usage']
                        print(f"Tokens used - Input: {usage.get('prompt_tokens', 0)}, Output: {usage.get('completion_tokens', 0)}")
                    
                    return content
                else:
                    print(f"Unexpected API response: {result}")
                    return "API Error: Unexpected response structure"
                    
            except requests.exceptions.Timeout:
                print(f"API timeout (attempt {attempt + 1})")
                if attempt == API_RETRY_ATTEMPTS - 1:
                    return "API Error: Request timed out"
                time.sleep(3)
            except requests.exceptions.RequestException as e:
                print(f"DeepSeek API error: {e}")
                if attempt == API_RETRY_ATTEMPTS - 1:
                    return f"API Error: {str(e)}"
                time.sleep(3)
            except Exception as e:
                print(f"Unexpected error: {e}")
                if attempt == API_RETRY_ATTEMPTS - 1:
                    return f"API Error: {str(e)}"
                time.sleep(3)
        
        return "API Error: All attempts failed"

    def analyze_rfp(self, row, company_name, company_desc, worker_id):
        title, link, snippet, date, full_content, kw = row
        print(f"[Worker {worker_id}] Analyzing: {title[:50]}...")
        
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
            print(f"AI analysis timeout for: {title[:30]}...")
            return {
                "keyword": kw,
                "title": title,
                "link": link,
                "snippet": snippet,
                "date": date,
                "full_content": full_content[:2000],
                "ai_answer": "AI analysis timed out",
                "ai_status": "Timeout"
            }
        except Exception as e:
            print(f"AI worker error: {e}")
            return {
                "keyword": kw,
                "title": title,
                "link": link,
                "snippet": snippet,
                "date": date,
                "full_content": full_content[:2000],
                "ai_answer": f"AI error: {str(e)}",
                "ai_status": "Error"
            }

# Batch Processing Module
class BatchProcessor:
    @staticmethod
    async def extract_items(items):
        extracted = []
        failed_extractions = []
        
        async def extract_single_item(item_data, item_index):
            title, link, snippet, date, kw = item_data
            try:
                print(f"\n[{item_index+1}/{len(items)}] Extracting: {title[:60]}...")
                print(f"   Link: {link}")
                
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
                    
                    print(f"   Raw content length: {len(content)} chars")
                    print(f"   Stripped content length: {len(stripped_content)} chars")
                    print(f"   Non-whitespace content: {len(actual_content)} chars")
                    
                    if len(actual_content) > 100:
                        print(f"   Content validated! Adding to results.")
                        return (title, link, snippet, date, stripped_content, kw)
                    else:
                        print(f"   Content too short after stripping whitespace")
                        failed_extractions.append((title, link, f"Content too short ({len(actual_content)} chars)"))
                        return None
                else:
                    print(f"   No content extracted")
                    failed_extractions.append((title, link, "No content extracted"))
                    return None
                    
            except asyncio.TimeoutError:
                print(f"   Timeout for individual extraction")
                failed_extractions.append((title, link, "Extraction timeout"))
                return None
            except Exception as e:
                print(f"   Error: {str(e)[:100]}")
                failed_extractions.append((title, link, f"Error: {str(e)[:50]}"))
                return None
        
        print(f"\nStarting extraction of {len(items)} items in batches of {BATCH_SIZE}...")
        
        for batch_start in range(0, len(items), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(items))
            batch_items = items[batch_start:batch_end]
            
            print(f"\nProcessing batch {batch_start//BATCH_SIZE + 1}/{(len(items) + BATCH_SIZE - 1)//BATCH_SIZE}")
            
            tasks = []
            for i, item in enumerate(batch_items):
                global_index = batch_start + i
                task = extract_single_item(item, global_index)
                tasks.append(task)
            
            try:
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in batch_results:
                    if isinstance(result, Exception):
                        print(f"Task failed with exception: {result}")
                    elif result is not None:
                        extracted.append(result)
                
                batch_extracted = len([r for r in batch_results if r is not None and not isinstance(r, Exception)])
                print(f"Batch complete. Extracted {batch_extracted} items from this batch")
                
            except Exception as e:
                print(f"Batch processing error: {e}")
        
        print(f"\nFinal Extraction Summary:")
        print(f"   Total items attempted: {len(items)}")
        print(f"   Successfully extracted: {len(extracted)}")
        print(f"   Failed extractions: {len(failed_extractions)}")
        
        if failed_extractions:
            print(f"\nFailed items:")
            for title, link, reason in failed_extractions[:10]:
                print(f"   - {title[:50]}... ({reason})")
            if len(failed_extractions) > 10:
                print(f"   ... and {len(failed_extractions) - 10} more")
        
        return extracted, failed_extractions

# Search Module
class SearchEngine:
    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {"X-API-KEY": api_key}

    def search_keyword(self, keyword, filetype, start_date, country, num_results, num_pages):
        print(f"\nSearching for keyword: '{keyword}'")
        
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
            print(f"Search failed for '{keyword}': {str(e)[:200]}")
            return None

    def filter_results(self, results, filetype):
        items = []
        organic_results = results.get("organic", [])
        print(f"Found {len(organic_results)} organic results")
        
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

        print(f"Filtered to {len(items)} relevant items")
        return items

# Report Generator Module
class ReportGenerator:
    @staticmethod
    def save_results(all_results, all_failed_extractions):
        if not all_results and not all_failed_extractions:
            print("No data extracted to save.")
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
                        'full_content': f'EXTRACTION FAILED: {reason}',
                        'ai_answer': 'N/A - Extraction Failed',
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
            
            print(f"Saved {len(df)} total rows to {filename}")
            
            ReportGenerator._print_summary(df, all_results, all_failed_extractions)
            
        except Exception as e:
            print(f"Error saving results: {e}")
            ReportGenerator._save_csv_fallback(all_results)

    @staticmethod
    def _print_summary(df, all_results, all_failed_extractions):
        success_count = sum(1 for _, row in df.iterrows() if row.get('ai_status') == 'Success')
        yes_count = sum(1 for _, row in df.iterrows() if 'Answer: Yes' in str(row.get('ai_answer', '')) and row.get('ai_status') == 'Success')
        
        print(f"\nSummary:")
        print(f"- Total rows in Excel: {len(df)}")
        print(f"- Successful extractions: {len(all_results)}")
        print(f"- Failed extractions: {len(all_failed_extractions)}")
        print(f"- AI analysis successful: {success_count}")
        print(f"- AI analysis failed: {len(all_results) - success_count if all_results else 0}")
        print(f"- Potential matches (Yes): {yes_count}")

    @staticmethod
    def _save_csv_fallback(all_results):
        try:
            df = pd.DataFrame(all_results) if all_results else pd.DataFrame()
            csv_filename = f'rfp_analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            df.to_csv(csv_filename, index=False)
            print(f"Saved results as CSV fallback: {csv_filename}")
        except:
            print("Could not save results in any format")

    @staticmethod
    def _save_empty_file():
        try:
            metadata = {
                "keyword": ["No results"],
                "title": ["No results found"],
                "link": [""],
                "snippet": [""],
                "date": [""],
                "full_content": ["No content extracted"],
                "ai_answer": ["No analysis performed"],
                "ai_status": ["N/A"]
            }
            df = pd.DataFrame(metadata)
            filename = f'rfp_analysis_empty_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            df.to_excel(filename, index=False)
            print(f"Saved empty results file: {filename}")
        except Exception as e:
            print(f"Could not save empty results file: {e}")

# Main Application
def main():
    print("RFP Analysis Tool with DeepSeek AI\n")
    
    load_dotenv()
    
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
    serper_api_key = os.getenv("SERPER_API_KEY")
    
    if not deepseek_api_key:
        print("DEEPSEEK_API_KEY not found in .env file")
        print("To get your DeepSeek API key:")
        print("   1. Go to https://platform.deepseek.com")
        print("   2. Sign up / Log in")
        print("   3. Create an API key")
        print("   4. Add to your .env file: DEEPSEEK_API_KEY=sk-xxxxx")
        return
    
    if not serper_api_key:
        print("SERPER_API_KEY not found in .env file")
        return
    
    collector = UserInputCollector()
    keywords = collector.get_keywords()
    start_date = collector.get_date()
    filetype = collector.get_filetype()
    country = collector.get_country_code()
    num_results = collector.get_num_results()
    num_pages = collector.get_num_pages()
    
    print(f"\nSearch Configuration:")
    print(f"Keywords: {keywords}")
    print(f"Start date: {start_date}")
    print(f"File type: {filetype}")
    print(f"Country: {country}")
    print(f"Results per keyword: {num_results}")
    print(f"Number of pages: {num_pages}")
