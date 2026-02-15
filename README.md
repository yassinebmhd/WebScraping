
# RFP Analysis Tool

This Python script automates the process of finding and analyzing RFP (Request For Proposal) documents and web pages related to payment systems and card program operations using an AI and Google search via the Serper API.

Features

- Search for RFPs and documents using customizable keywords, country codes, and file types (PDF/HTML).
- Extracts and processes content from PDF files or web pages using `pdfplumber`, `aiohttp`, `BeautifulSoup`, or a headless browser crawler.
- Uses the DeepSeek API to analyze and determine if each result is a match for the company's solution.
- Outputs a well-formatted Excel report with highlights, results, and summary statistics.
- Handles errors, large files, and timeouts gracefully.

AI Integration

- Utilizes the DeepSeek Chat API to interpret and summarize the relevance of RFP content.
- Token-efficient and cost-effective (~$0.07 per 1M tokens).

Dependencies

- Python 3.8+
- See `requirements.txt` for package list.

How to Use

1. Clone the project and install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

2. Create a `.env` file in the root directory with your API keys:
    ```
    DEEPSEEK_API_KEY=your_deepseek_key_here
    SERPER_API_KEY=your_serper_api_key_here
    ```

3. Run the script:
    ```bash
    python your_script_name.py
    ```

4. Follow the prompts:
    - Enter keywords (e.g. "chargeback automation / card processing")
    - Select file type (PDF or URL)
    - Choose country code (e.g. `us`, `fr`, `gb`)
    - Specify start date and number of results

5. The results will be saved as an Excel file in the current directory.

Output

- `rfp_analysis_deepseek_<timestamp>.xlsx` containing:
  - Keywords, titles, links, AI analysis, and extracted content
  - Conditional formatting for easy review
  - Separate sheet for failed extractions

Example Use Case

Perfect for sales/marketing professionals at PayTic Connect or similar fintech companies seeking qualified RFP leads.
