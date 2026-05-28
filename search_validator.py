import pandas as pd
import time
import os
import glob
import ssl
import warnings
from ddgs import DDGS
from urllib.parse import urlparse

# --- OPRAVA SSL PRO MAC (Stručně a jasně) ---
warnings.filterwarnings('ignore')
os.environ['CURL_CA_BUNDLE'] = ''
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass

# --- KONFIGURACE ---
INPUT_DIR = "data/input"
OUTPUT_FILE = "data/search_results.csv"

# Jen ti nejotravnější paraziti
BLACKLIST = ["similarweb.com", "topsitessearch.com", "zawya.com", "easycounter.com", "webrate.org", "facebook.com"]

def clean_name(name):
    if not isinstance(name, str): return ""
    return name.split(';')[0].split(',')[0].strip()

def check_company(name, website):
    c_name = clean_name(name)
    raw_url = str(website).split(';')[0].split(',')[0].strip().lower()
    
    if not raw_url or raw_url == "none" or len(c_name) < 2:
        return False, "", ""

    # Získání domény pro kontrolu
    parsed = urlparse(raw_url if "://" in raw_url else "https://" + raw_url)
    domain = parsed.netloc.replace("www.", "").lower()

    # JEDEN JEDNODUCHÝ A ÚČINNÝ DOTAZ
    query = f"{c_name} personal banking cards"
    print(f"🔎 Prověřuji: {c_name}")
    
    try:
        with DDGS(timeout=15) as ddgs:
            results = list(ddgs.text(query, max_results=5))
            
            for r in results:
                href = r['href'].lower()
                hostname = urlparse(href).netloc.lower()
                
                # Musí to být jejich web a ne blacklist
                if domain in hostname and not any(b in hostname for b in BLACKLIST):
                    # Pokud je v odkazu nebo titulku klíčové slovo, máme vítěze
                    if any(kw in href or kw in r['title'].lower() for kw in ['card', 'debit', 'credit', 'personal', 'retail', 'karta']):
                        print(f"   🎯 ZÁSAH: {r['href']}")
                        return True, r['href'], r['body']
            
            # Fallback: Pokud jsme nenašli kartu, vezmeme první odkaz z jejich webu
            for r in results:
                if domain in urlparse(r['href'].lower()).netloc:
                    return False, r['href'], r['body']
                    
    except Exception as e:
        print(f"   ⚠️ Síťová chyba (DDG nás na chvíli omezil)")
        
    return False, "", ""

def main():
    files = glob.glob(os.path.join(INPUT_DIR, "*.csv")) + glob.glob(os.path.join(INPUT_DIR, "*.xlsx"))
    if not files: return
    df = pd.read_csv(files[0]) if files[0].endswith(".csv") else pd.read_excel(files[0])
    
    # Sjednocení názvů sloupců
    df.columns = [c.lower().strip() for c in df.columns]
    for col in df.columns:
        if any(x in col for x in ["web", "url", "site"]): df = df.rename(columns={col: 'website'})
        if any(x in col for x in ["company", "name", "firma"]): df = df.rename(columns={col: 'company_name'})

    df = df.dropna(subset=['website'])
    df = df[df['website'].astype(str).str.lower() != 'none']

    # Přeskoč domény již potvrzené BIN lookupem
    BIN_CONFIRMED_FILE = "data/bin_confirmed_domains.txt"
    bin_confirmed = set()
    if os.path.exists(BIN_CONFIRMED_FILE):
        with open(BIN_CONFIRMED_FILE, "r") as f:
            bin_confirmed = set(line.strip() for line in f if line.strip())
        print(f"ℹ️ Přeskakuji {len(bin_confirmed)} domén potvrzených BIN lookupem.")

    df = df[~df['website'].astype(str).isin(bin_confirmed)]

    results_data = []
    print(f"🚀 Spouštím rychlou validaci pro {len(df)} firem...")

    for index, row in df.iterrows():
        found, link, snip = check_company(row.get('company_name', row['website']), row['website'])
        results_data.append({
            'website': row['website'],
            'search_has_cards': found,
            'search_top_link': link,
            'search_snippet': snip
        })
        # Stačí krátká pauza, když neděláme tolik dotazů
        time.sleep(1.2)

    pd.DataFrame(results_data).to_csv(OUTPUT_FILE, index=False)
    print(f"✅ Hotovo! Výsledky v {OUTPUT_FILE}")

if __name__ == "__main__":
    main()