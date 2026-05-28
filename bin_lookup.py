import pandas as pd
import os
import glob
from urllib.parse import urlparse

# --- KONFIGURACE ---
BIN_FILE = "data/bin-list-data.csv"
INPUT_DIR = "data/input"
FINAL_RESULTS = "data/final_results.csv"
BIN_CONFIRMED_FILE = "data/bin_confirmed_domains.txt"  # Sem se uloží domény potvrzené BINem

# Firmy které jsou v BIN listu jako technický správce BIN čísel,
# ale NEVYDÁVAJÍ karty přímo zákazníkům — tyto přeskočíme i přes BIN shodu.
BIN_EXCLUSION_LIST = {
    # === KARTOVÉ SÍTĚ (propojují banky, ale samy karty nevydávají) ===
    "mastercard.com", "mastercard.fr", "mastercard.us",
    "brand.mastercard.com",             # Maestro International
    "visa.com", "visa.ca", "visa.co.nz", "visa.se", "visa.hu",
    "visa.com.ar", "usa.visa.com", "visajapan.gr.jp",
    "en.unionpay.com",                  # China UnionPay
    "discoverglobalnetwork.com",        # Discover Network (síť) — discover.com je přímý vydavatel
    "bccard.com",                       # Korejská kartová síť (obdoba Visa/MC v Koreji)

    # === B2B PROCESORY A BANKOVNÍ SOFTWARE ===
    "fiserv.com",                       # Největší světový procesor bankovního softwaru
    "jackhenry.com",                    # Bankovní software a zpracování plateb
    "fisglobal.com",                    # FIS — bankovní technologie
    "cscu.net",                         # Card Services for Credit Unions (B2B)
    "pscu.com",                         # Payment Systems for Credit Unions (B2B)
    "csiweb.com",                       # Computer Services, Inc. — banking tech
    "eurokartensysteme.de",             # Euro Kartensysteme (německý platební systém)
    "psa.at",                           # PSA Payment Services Austria
    "shazam.net",                       # SHAZAM platební síť
    "alliancedata.com",                 # Alliance Data Systems — private label procesor
    "ncr.com",                          # NCR payment solutions
    "icscards.nl",                      # International Card Services (B2B NL)
    "six-payment-services.com",         # SIX Payment Services
    "ftpsllc.com",                      # Fifth Third Processing Solutions
    "nationalprocessing.com",           # National Processing Company
    "starprocessing.com", "star.com",   # STAR processing network
    "www1.firstdata.com.ar",            # First Data (Fiserv) Argentina
    "wirecard-cardsolutions.co.uk",     # Wirecard Card Solutions (B2B)
    "electronicpayments.com",           # Electronic Payment Services
    "usapaymentsystems.com",            # USA Payment Systems
    "jcc.com.cy",                       # JCC Payment Systems Cyprus
    "nationalbankcard.com",             # National Bankcard Services
    "nexi.it",                          # Nexi Payments — italský procesor/acquirer
    "galileo-ft.com",                   # Galileo — B2B card issuing platform
    "marqeta.com",                      # Marqeta — B2B card issuing platform
    "lithic.com",                       # Lithic — B2B card issuing API
    "thredd.com",                       # Thredd — B2B card issuing platform
    "worldpay.com",                     # Worldpay — payment processor
    "adyen.com",                        # Adyen — payment processor
    "stripe.com",                       # Stripe — payment processor
    "temenos.com",                      # Temenos — core banking software
    "mambu.com",                        # Mambu — core banking SaaS

    # === OBCHODNÍ ASOCIACE (ne banky) ===
    "icba.org",                         # Independent Community Bankers Association
    "icbabancard.org",                  # ICBA Bancard (B2B)
    "prvea.com",                        # Base Interchange System Association
    "chiginkyo.or.jp",                  # Regional Banks Association of Japan

    # === LETECKÉ SPOLEČNOSTI (co-branded karty vydává banka, ne aerolinka) ===
    "aa.com",                           # American Airlines
    "delta.com",                        # Delta Air Lines
    "united.com",                       # United Airlines
    "lufthansa.com",                    # Lufthansa
    "aerlingus.com",                    # Aer Lingus
    "aircanada.com",                    # Air Canada
    "airnewzealand.com",                # Air New Zealand
    "qantas.com",                       # Qantas
    "jal.co.jp",                        # Japan Airlines
    "ana.co.jp",                        # ANA
    "finnair.com",                      # Finnair
    "iberia.com",                       # Iberia
    "jetstar.com",                      # Jetstar
    "mabuhaymiles.com",                 # Philippine Airlines miles program
}

def normalize_domain(url):
    """Extrahuje holou doménu z URL (bez www, http, cesty, portu)."""
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        domain = domain.replace("www.", "")
        domain = domain.split(":")[0]  # Odstraní port
        return domain
    except:
        return ""

def build_bin_domain_set(bin_file):
    """Načte BIN CSV a vytvoří sadu normalizovaných domén vydavatelů."""
    print(f"📂 Načítám BIN list ({bin_file})... (může chvíli trvat)")
    df = pd.read_csv(bin_file, low_memory=False, usecols=["IssuerUrl"])

    domains = set()
    for url in df["IssuerUrl"].dropna():
        domain = normalize_domain(url)
        if domain:
            domains.add(domain)

    print(f"✅ BIN list načten — {len(domains)} unikátních domén vydavatelů karet.")
    return domains

def domain_matches_bin(input_domain, bin_domains):
    """
    Vrátí (True, matched_domain) pokud vstupní doména odpovídá BIN záznamu.
    Kontroluje:
      1. Přesnou shodu: 'cibc.com' == 'cibc.com'
      2. BIN je subdoména vstupu: input='cmbchina.com', BIN='english.cmbchina.com' → shoda
      3. Vstup je subdoména BINu: input='personal.hsbc.com', BIN='hsbc.com' → shoda
    """
    if not input_domain:
        return False, ""

    # 1. Přesná shoda
    if input_domain in bin_domains:
        return True, input_domain

    # 2. BIN doména je subdoménou vstupní domény (english.cmbchina.com → cmbchina.com)
    for bin_domain in bin_domains:
        if bin_domain.endswith("." + input_domain):
            return True, bin_domain

    # 3. Vstupní doména je subdoménou BIN domény (personal.hsbc.com → hsbc.com)
    for bin_domain in bin_domains:
        if input_domain.endswith("." + bin_domain):
            return True, bin_domain

    return False, ""

def load_confirmed_domains():
    """Načte seznam domén potvrzených BINem (pro ostatní skripty)."""
    if not os.path.exists(BIN_CONFIRMED_FILE):
        return set()
    with open(BIN_CONFIRMED_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def main():
    # --- Načti vstupní soubor ---
    files = glob.glob(os.path.join(INPUT_DIR, "*.csv")) + glob.glob(os.path.join(INPUT_DIR, "*.xlsx"))
    if not files:
        print("❌ Žádný vstupní soubor nenalezen v data/input/")
        return

    df = pd.read_csv(files[0]) if files[0].endswith(".csv") else pd.read_excel(files[0])
    df.columns = [c.lower().strip() for c in df.columns]
    for col in df.columns:
        if any(x in col for x in ["web", "url", "site"]):
            df = df.rename(columns={col: "website"})
        if any(x in col for x in ["company", "name", "firma"]):
            df = df.rename(columns={col: "company_name"})

    df = df.dropna(subset=["website"])
    df = df[df["website"].astype(str).str.lower() != "none"]

    # --- Zkontroluj BIN soubor ---
    if not os.path.exists(BIN_FILE):
        print(f"⚠️ BIN soubor nenalezen: {BIN_FILE}")
        print("   Zkontroluj cestu v BIN_FILE v konfigurace skriptu.")
        # Vytvořit prázdný soubor aby ostatní skripty nepadaly
        open(BIN_CONFIRMED_FILE, "w").close()
        return

    bin_domains = build_bin_domain_set(BIN_FILE)

    # --- Resume: přeskoč firmy, které jsou již v final_results.csv ---
    already_done = set()
    if os.path.exists(FINAL_RESULTS):
        try:
            df_done = pd.read_csv(FINAL_RESULTS)
            already_done = set(df_done["website"].astype(str).unique())
        except Exception:
            pass

    # --- Porovnej vstupní domény s BIN listem ---
    print(f"\n🔍 Porovnávám {len(df)} firem s BIN listem...")
    confirmed = []
    confirmed_domains = set()

    for _, row in df.iterrows():
        website = str(row.get("website", "")).strip()
        if website in already_done:
            continue

        input_domain = normalize_domain(website)
        found, matched = domain_matches_bin(input_domain, bin_domains)

        # Přeskoč firmy na exclusion listu (procesory, sítě, B2B platformy)
        if found and input_domain in BIN_EXCLUSION_LIST:
            print(f"  ⏭️  BIN SHODA ALE VYLOUČENO (exclusion list): {website}")
            found = False

        if found:
            company = str(row.get("company_name", website))
            print(f"  ✅ BIN SHODA: {website}  (BIN doména: {matched})")
            confirmed.append({
                "website": website,
                "search_has_cards": True,
                "search_top_link": "",
                "search_snippet": f"Potvrzeno BIN listem (shoda: {matched})",
                "scraped_text": "[BIN CONFIRMED]",
                "category": "B2C_RETAIL_BANK",
                "is_relevant": True,
                "confidence": 100,
                "needs_review": False,
                "reason": f"Potvrzeno BIN listem — doména nalezena mezi vydavateli karet (shoda: {matched})."
            })
            confirmed_domains.add(website)

    # --- Zapiš výsledky do final_results.csv ---
    if confirmed:
        new_df = pd.DataFrame(confirmed)
        file_exists = os.path.isfile(FINAL_RESULTS)
        new_df.to_csv(FINAL_RESULTS, mode="a", index=False, header=not file_exists)
        print(f"\n🏆 {len(confirmed)} firem potvrzeno BINem a zapsáno přímo do final_results.csv")
    else:
        print("\nℹ️ Žádná firma z vstupního souboru nenalezena v BIN listu.")

    # --- Uložit seznam potvrzených domén (pro přeskočení v dalších krocích) ---
    with open(BIN_CONFIRMED_FILE, "w") as f:
        for domain in confirmed_domains:
            f.write(domain + "\n")
    print(f"📝 Potvrzené domény uloženy do {BIN_CONFIRMED_FILE} — scraper a classifier je přeskočí.")

if __name__ == "__main__":
    main()
