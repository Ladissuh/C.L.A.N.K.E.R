import asyncio
import os
import glob
import pandas as pd
from playwright.async_api import async_playwright
import trafilatura
from urllib.parse import urlparse

# --- KONFIGURACE ---
INPUT_FILE = "data/search_results.csv" 
OUTPUT_FILE = "data/scraped_data.csv"
TIMEOUT = 30000 # Sníženo na 30s (pokud se web nenačte do 30s, většinou je mrtvý)

PRIORITY_KEYWORDS = ["card", "karta", "karty", "personal", "osobní", "app", "account", "účet"]
IGNORE_KEYWORDS = ["career", "jobs", "news", "blog", "press", "login", "signin", "pdf"]

def get_domain_name(url):
    try:
        if not isinstance(url, str): return ""
        netloc = urlparse(url if "://" in url else "https://" + url).netloc
        return netloc.replace("www.", "").split('.')[0]
    except: return ""

async def get_page_text(page, url):
    """Získá text s blokováním zbytečných zdrojů pro maximální rychlost."""
    raw_url = str(url).split(';')[0].split(',')[0].strip()
    if not raw_url or ".pdf" in raw_url.lower(): return ""
    if not raw_url.startswith("http"): raw_url = "https://" + raw_url

    # Zkusíme rovnou nejvíc pravděpodobnou variantu
    # (Většina bank vyžaduje SSL, zkusíme nejdřív to, co přišlo)
    try:
        print(f"      ... stahuji: {raw_url}")
        # wait_until="domcontentloaded" je mnohem rychlejší než "load"
        response = await page.goto(raw_url, timeout=TIMEOUT, wait_until="domcontentloaded")
        
        # Pokud se nic nenačetlo (např. chyba SSL u holé domény), zkusíme www
        if not response or not response.ok:
            parsed = urlparse(raw_url)
            if not parsed.netloc.startswith("www."):
                new_url = parsed._replace(netloc="www." + parsed.netloc).geturl()
                print(f"      ... zkouším alternativu: {new_url}")
                await page.goto(new_url, timeout=TIMEOUT, wait_until="domcontentloaded")

        # Krátká pauza na vykreslení JS (sníženo na 1s)
        await page.wait_for_timeout(1000) 
        content = await page.content()
        text = trafilatura.extract(content, include_comments=False, include_tables=False)
        
        return text if text and len(text) > 150 else await page.inner_text("body")
    except Exception as e:
        print(f"      ⚠️ Chyba: {str(e)[:50]}")
        return ""

async def find_product_links(page, base_url):
    """Najde 2 podstránky, ignoruje smetí."""
    try:
        base_domain = get_domain_name(base_url)
        links = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a'))
                .filter(a => a.href.startsWith('http'))
                .map(a => ({ text: a.innerText.toLowerCase(), href: a.href }))
        }''')
        
        candidates = []
        seen = {base_url.rstrip('/')}
        for link in links:
            href = link['href'].split('#')[0].rstrip('/')
            if base_domain not in get_domain_name(href) or href in seen: continue
            if any(bad in href.lower() or bad in link['text'] for bad in IGNORE_KEYWORDS): continue

            score = sum(1 for kw in PRIORITY_KEYWORDS if kw in href.lower() or kw in link['text'])
            if score > 0:
                candidates.append((score, href))
                seen.add(href)

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [c[1] for c in candidates[:2]]
    except: return []

async def process_company(page, row):
    homepage_url = str(row.get('website', ''))
    deep_link_url = row.get('search_top_link')
    
    company_data = []
    visited = set()

    # 1. Homepage
    if homepage_url and homepage_url.lower() != "none":
        print(f"🔍 [HOMEPAGE] {homepage_url}")
        text = await get_page_text(page, homepage_url)
        if text:
            company_data.append(f"--- HOMEPAGE ---\n{text}")
            visited.add(page.url.rstrip('/'))

    # 2. Deep Link (jen pokud je jiný než HP)
    if pd.notna(deep_link_url) and isinstance(deep_link_url, str) and len(deep_link_url) > 10:
        if deep_link_url.rstrip('/') not in visited:
            print(f"🔍 [DEEP LINK] {deep_link_url}")
            text = await get_page_text(page, deep_link_url)
            if text:
                company_data.append(f"--- DEEP LINK ---\n{text}")
                visited.add(page.url.rstrip('/'))

    # 3. Product Links
    p_links = await find_product_links(page, page.url)
    for link in p_links:
        if link.rstrip('/') not in visited:
            text = await get_page_text(page, link)
            if text: company_data.append(f"--- PRODUCT ---\n{text}")

    full_report = "\n\n".join(company_data)
    return full_report[:18000]

async def main():
    if not os.path.exists(INPUT_FILE): return
    df_all = pd.read_csv(INPUT_FILE).dropna(subset=['website'])
    
    # --- LOGIKA POKRAČOVÁNÍ (RESUME) ---
    processed_urls = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            # Načteme už hotové weby, abychom je podruhé neskrapovali
            df_done = pd.read_csv(OUTPUT_FILE)
            processed_urls = set(df_done['website'].astype(str).unique())
            print(f"ℹ️ Nalezen existující soubor. Přeskakuji {len(processed_urls)} hotových webů.")
        except:
            pass

    # Odfiltrujeme to, co už máme
    df = df_all[~df_all['website'].astype(str).isin(processed_urls)]

    # Přeskoč domény již potvrzené BIN lookupem
    BIN_CONFIRMED_FILE = "data/bin_confirmed_domains.txt"
    if os.path.exists(BIN_CONFIRMED_FILE):
        with open(BIN_CONFIRMED_FILE, "r") as f:
            bin_confirmed = set(line.strip() for line in f if line.strip())
        before = len(df)
        df = df[~df['website'].astype(str).isin(bin_confirmed)]
        skipped = before - len(df)
        if skipped > 0:
            print(f"ℹ️ Přeskakuji {skipped} domén potvrzených BIN lookupem.")

    if df.empty:
        print("✅ Všechny domény jsou již vyscrapovány.")
        return

    print(f"🚀 Spouštím CÍLENÝ scraping pro {len(df)} zbývajících firem...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            ignore_https_errors=True
        )
        page = await context.new_page()

        await page.route("**/*", lambda route: route.abort() 
            if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
            else route.continue_())

        for index, row in df.iterrows():
            try:
                # Obaleno timeoutem, aby se to dalo "přeskočit", když web visí
                text = await asyncio.wait_for(process_company(page, row), timeout=45.0)
                
                # --- PRŮBĚŽNÉ UKLÁDÁNÍ ---
                # Vytvoříme DataFrame z jednoho řádku a hned ho "přilepíme" do CSV
                temp_df = pd.DataFrame([row])
                temp_df['scraped_text'] = text
                
                file_exists = os.path.isfile(OUTPUT_FILE)
                temp_df.to_csv(OUTPUT_FILE, mode='a', index=False, header=not file_exists)
                
            except asyncio.TimeoutError:
                print(f"      🛑 TIMEOUT: Přeskakuji {row['website']}")
                # Zapíšeme i chybu, aby se to při příštím runu už nezkoušelo
                temp_df = pd.DataFrame([row])
                temp_df['scraped_text'] = "TIMEOUT_ERROR"
                temp_df.to_csv(OUTPUT_FILE, mode='a', index=False, header=not os.path.isfile(OUTPUT_FILE))
            except Exception as e:
                print(f"      ❌ Chyba: {e}")
        
        await browser.close()
    
    print(f"✅ HOTOVO! Výsledky jsou v {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(main())

if __name__ == "__main__":
    asyncio.run(main())
