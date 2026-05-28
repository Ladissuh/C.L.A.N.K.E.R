import os
import json
import asyncio
import pandas as pd
from openai import AsyncOpenAI
from dotenv import load_dotenv

# --- KONFIGURACE ---
SCRAPED_DATA = "data/scraped_data.csv"
OUTPUT_FILE = "data/final_results.csv"
MAX_CONCURRENT_QUERIES = 5
# Firmy s confidence pod tímto prahem budou označeny jako "NEEDS_REVIEW" pro ruční kontrolu
CONFIDENCE_REVIEW_THRESHOLD = 65

load_dotenv()
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def classify_task(semaphore, row):
    async with semaphore:
        company = str(row.get('company_name', row.get('website', 'Neznámá')))
        website = str(row.get('website', ''))
        raw_scraped = str(row.get('scraped_text', ''))
        scraped_text = " ".join(raw_scraped.split())[:15000]
        search_snippet = str(row.get('search_snippet', ''))
        search_link = str(row.get('search_top_link', ''))

        # Pokud scraper nic nezískal, dáme AI jasný signál
        if not scraped_text or scraped_text.lower() in ('nan', 'none', '', 'timeout_error'):
            scraped_text = "[OBSAH WEBU NENÍ K DISPOZICI — použij své vlastní znalosti o firmě]"

        # PROMPT — hodnotí jak B2C (osobní) tak B2B (firemní) vydavatele karet
        prompt = f"""
        Analyzuj subjekt "{company}" (web: {website}).
        Rozhodni, zda vydává platební kartu (debetní nebo kreditní) PŘÍMO koncovým zákazníkům — tedy fyzickým osobám nebo firmám — jako součást vlastního účtu nebo produktu.

        VSTUPY:
        - URL: {search_link}
        - Úryvek z vyhledávání: {search_snippet}
        - Obsah webu: {scraped_text[:15000]}

        ===== DŮLEŽITÉ: JAK PRACOVAT S TEXTEM =====
        Obsah webu může být prázdný, nebo může jít o korporátní/investor stránku (výroční zprávy, press releases, "For Institutions").
        V takovém případě IGNORUJ prázdný text a použij své vlastní znalosti o firmě "{company}" ({website}).
        Pokud víš, že jde o banku vydávající karty (např. JPMorgan Chase, Citibank, PNC, USAA, Deutsche Bank, CIBC atd.),
        klasifikuj ji jako relevantní i bez obsahu webu. Scraped text slouží jako pomůcka, ne jako jediný zdroj pravdy.

        ===== KLÍČOVÁ OTÁZKA =====
        Může si u této firmy NORMÁLNÍ ČLOVĚK nebo FIRMA otevřít účet a dostat k němu kartu?
        Pokud ANO → is_relevant = true.
        Pokud NE (firma prodává software, zpracovává platby, poskytuje technologie jiným bankám) → is_relevant = false.

        ===== DŮLEŽITÉ PRAVIDLO O BANKÁCH =====
        Pokud web jasně ukazuje, že jde o banku nebo finanční instituci nabízející CHECKING ACCOUNT
        nebo DIGITAL BANKING přímo zákazníkům, pak je firma AUTOMATICKY relevantní.
        Důvod: každá banka s checking account k němu nabízí debetní kartu — i když to na webu není explicitně napsáno.
        POZOR: Savings account (spořicí účet) sám o sobě kartu neznamená — hledej checking account nebo current account.
        Příznaky: "open an account", "digital banking", "24/7 banking", "online banking", "mobile banking app",
        "checking account", "current account", "FDIC insured", "credit union membership".
        Toto platí i pro specializované banky (pro právníky, lékaře, firmy atd.).

        ===== ZAMÍTNI (is_relevant = false) =====

        A) KARTOVÉ SÍTĚ — propojují banky, ale kartu NEVYDÁVAJÍ sami:
           Příklady: Visa, Mastercard, American Express (síť), UnionPay.
           Poznat podle: "network", "scheme", "accept our card everywhere" — BANKA vydává kartu, ne Visa/MC.

        B) PLATFORMA PRO VYDÁVÁNÍ KARET (Issuing-as-a-Service) — prodávají technologii vydávání karet jiným bankám/fintechům:
           Příklady: Marqeta, Galileo, Lithic, Stripe Issuing, Thredd, Episode Six.
           Poznat podle: "card issuing API", "issue cards for your business", "power your card program", "BIN sponsorship".
           POZOR: I když web říká "issue cards" nebo "card issuing", jde o B2B infrastrukturu pokud jejich ZÁKAZNÍKEM je fintech/banka!

        C) BANKOVNÍ SOFTWARE A CORE BANKING — dodávají software bankám:
           Příklady: Temenos, Mambu, Jack Henry, Fiserv, FIS, Finastra, ebankIT, Corelation, DCI.
           Poznat podle: "core banking platform", "digital banking solution for banks", "SaaS for financial institutions".

        D) VÝROBCI A TISKÁRNY KARET — fyzicky vyrábějí plastové karty:
           Příklady: CPI Card Group, Thales, Composecure, Valid.

        E) PLATEBNÍ BRÁNY A ACQUIRING — zpracovávají platby pro obchodníky, ale NEVYDÁVAJÍ karty zákazníkům:
           Příklady: Checkout.com, Worldpay, Razorpay, Adyen, Stripe (jako gateway), Dwolla.
           Poznat podle: "accept payments", "payment gateway", "merchant acquiring".

        F) KREDITNÍ BUREAUX, SCORING, FRAUD — hodnotí úvěruschopnost, ale kartu NEVYDÁVAJÍ:
           Příklady: Equifax, Experian (scoring část), TransUnion, FICO, Socure.

        G) REGULÁTOŘI A VLÁDNÍ ÚŘADY:
           Příklady: FCA, SEC, ECB, SBA, centrální banky.

        H) NEFINANČNÍ FIRMY: Technologické firmy, pojišťovny bez karty, média, e-commerce platformy, poradenství.
           Příklady: Google, Samsung, Sony, Tesla, Shopify, Investopedia, Bloomberg, Django.
           POZOR: Pojišťovny (Progressive, Nationwide) typicky NEVYDÁVAJÍ platební karty — pouze pojišťují.

        I) POUZE B2B PLATBY (ne vydávání karet): Firmy řešící mezifiremní platby bez toho, aby vydávaly kartu zákazníkům.
           Příklady: Convera (FX platby), Checkbook.io (digitální šeky), Dwolla.

        ===== SCHVAL (is_relevant = true) =====

        Firma JE relevantní pokud zákazník (člověk nebo firma) si u ní PŘÍMO otevře účet a dostane k němu kartu.

        OSOBNÍ (B2C_RETAIL_BANK): Banka, neobanka, fintech s osobním účtem + debetní/kreditní kartou.
        Příklady: Wells Fargo, HSBC, SoFi, KOHO, Revolut, Chime, Discover.

        FIREMNÍ (B2B_CARD_ISSUER): Firma nabízí business checking účet nebo spend management platformu,
        kde FIRMA SAMOTNÁ dostane kartu pro své výdaje. Jde o přímý vztah firma→zákazník, ne firma→banka.
        Příklady: Bluevine, Brex, Ramp, Mercury, Relay, Tide.
        POZOR: Corpay vydává fleet karty přímo firmám → B2B_CARD_ISSUER ✓.
        CardWorks vydává kreditní karty pro spotřebitele přes partnery → B2C_RETAIL_BANK ✓.

        ===== VÝSLEDNÉ KATEGORIE =====
        - "B2C_RETAIL_BANK": Vydavatel karet pro fyzické osoby.
        - "B2B_CARD_ISSUER": Vydavatel karet přímo firmám/podnikatelům (ne přes API pro jiné banky).
        - "B2B_INFRASTRUCTURE": Technologický dodavatel pro banky, kartová síť, issuing platforma.
        - "GOVERNMENT_REGULATOR": Regulátor, centrální banka, vládní agentura.
        - "NON_FINANCIAL": Nesouvisí s finančními kartami.

        Odpověz POUZE v JSON bez dalšího textu:
        {{
            "category": "B2C_RETAIL_BANK" | "B2B_CARD_ISSUER" | "B2B_INFRASTRUCTURE" | "GOVERNMENT_REGULATOR" | "NON_FINANCIAL",
            "is_relevant": true/false,
            "confidence": 0-100,
            "reason": "Jednověté zdůvodnění v češtině."
        }}

        is_relevant = true POUZE pro B2C_RETAIL_BANK a B2B_CARD_ISSUER.
        is_relevant = false pro B2B_INFRASTRUCTURE, GOVERNMENT_REGULATOR a NON_FINANCIAL.
        """

        try:
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0  # Maximální stabilita, žádná kreativita
            )
            data = json.loads(response.choices[0].message.content)

            # Pojistka: pokud URL odkazuje na jobs/news stránku, zamítni bez ohledu na AI
            if any(x in search_link.lower() for x in ["jobs.", "careers.", "news.", "blog.", "press."]):
                data["is_relevant"] = False
                data["category"] = "NON_FINANCIAL"
                data["reason"] = "Detekována poddoména pro zprávy nebo kariéru v URL."

            # Přidáme příznak pro ruční kontrolu u nízkého confidence
            confidence = data.get("confidence", 100)
            data["needs_review"] = confidence < CONFIDENCE_REVIEW_THRESHOLD

            if data["needs_review"]:
                print(f"⚠️  {company} [{confidence}% jistota — POTŘEBUJE KONTROLU]: {data['reason']}")
            else:
                print(f"{'✅' if data['is_relevant'] else '❌'} {company} [{confidence}%]: {data['reason']}")

            return {**row.to_dict(), **data}
        except Exception as e:
            print(f"❌ Chyba AI pro {company}: {str(e)[:80]}")
            return {**row.to_dict(), "is_relevant": False, "confidence": 0, "needs_review": True, "reason": "Chyba AI — zkontroluj ručně"}

async def main():
    if not os.path.exists(SCRAPED_DATA):
        print("❌ Soubor scraped_data.csv nenalezen. Spusť nejdříve scraper.")
        return

    df_all = pd.read_csv(SCRAPED_DATA)

    # --- RESUME LOGIKA: přeskočíme firmy, které už byly klasifikovány ---
    already_done = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_done = pd.read_csv(OUTPUT_FILE)
            already_done = set(df_done['website'].astype(str).unique())
            print(f"ℹ️ Nalezen existující soubor. Přeskakuji {len(already_done)} již klasifikovaných firem.")
        except Exception:
            pass

    df = df_all[~df_all['website'].astype(str).isin(already_done)]

    if df.empty:
        print("✅ Všechny firmy jsou již klasifikovány.")
        return

    print(f"🚀 Klasifikuji {len(df)} firem (model: gpt-4o-mini)...")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)
    tasks = [classify_task(semaphore, row) for _, row in df.iterrows()]
    results = await asyncio.gather(*tasks)

    # Průběžné ukládání — přilepíme k existujícímu souboru
    new_df = pd.DataFrame(results)
    file_exists = os.path.isfile(OUTPUT_FILE)
    new_df.to_csv(OUTPUT_FILE, mode='a', index=False, header=not file_exists)

    # Shrnutí výsledků
    relevant = sum(1 for r in results if r.get('is_relevant'))
    needs_review = sum(1 for r in results if r.get('needs_review'))
    print(f"\n🏆 Hotovo!")
    print(f"   ✅ Relevantních: {relevant} / {len(results)}")
    print(f"   ⚠️  Potřebuje ruční kontrolu (nízká jistota): {needs_review}")
    print(f"   📂 Výsledky v: {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(main())
