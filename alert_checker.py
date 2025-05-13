import re
import time
import asyncio
import datetime
from playwright.async_api import async_playwright
from flask import Flask
import os
from urllib.parse import urlparse
from notifications import build_full_html_email, send_email
from models import db, TrackedItem, CompetitorItem
from models import AlertSetting

# --- Set up Flask app and DB ---
app = Flask(__name__)
db_filename = 'competiview.db'
db_full_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'instance', db_filename))
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_full_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# --- Utility Functions ---
def canonical_url(url):
    parsed = urlparse(url)
    path = parsed.path
    if "amazon." in parsed.netloc:
        parts = path.split('/')
        if 'dp' in parts:
            idx = parts.index('dp')
            asin = parts[idx + 1] if idx + 1 < len(parts) else ''
            return f"https://{parsed.netloc}/dp/{asin}".lower()
    if "ebay." in parsed.netloc:
        parts = path.strip('/').split('/')
        item_id = ''
        for part in reversed(parts):
            if part.isdigit():
                item_id = part
                break
        if item_id:
            return f"https://{parsed.netloc}/itm/{item_id}".lower()
    return f"{parsed.scheme}://{parsed.netloc}{path}".lower()

def extract_item_id(url):
    match = re.search(r"/itm/(\d+)", url)
    return match.group(1) if match else None

def extract_asin(url):
    match = re.search(r"/dp/([A-Z0-9]{10})", url, re.IGNORECASE)
    return match.group(1).upper() if match else None

def build_search_url(platform, keywords):
    query = '+'.join(keywords.split()[:6])
    if platform == "amazon":
        return f"https://www.amazon.com/s?k={query}"
    return f"https://www.ebay.com/sch/i.html?_nkw={query}"

async def fetch_search_results(page, search_url):
    await page.goto(search_url)
    await page.wait_for_selector(".s-item", timeout=10000)
    items = await page.query_selector_all(".s-item")
    listings = {}
    for item in items:
        try:
            link_element = await item.query_selector(".s-item__link")
            price_element = await item.query_selector(".s-item__price")
            if not link_element or not price_element:
                continue
            url = await link_element.get_attribute("href")
            price_text = await price_element.text_content()
            price_match = re.search(r"[\d,.]+", price_text)
            price = float(price_match.group(0).replace(",", "")) if price_match else None
            if url and price is not None:
                item_id = extract_item_id(url)
                if item_id:
                    listings[item_id] = {"full_url": canonical_url(url), "price": price}
        except Exception:
            continue
    return listings

async def fetch_amazon_results(page, search_url, min_price=None, max_price=None, max_items=50):
    await page.goto(search_url, timeout=20000)
    await page.wait_for_load_state('domcontentloaded')
    await page.wait_for_selector("div.s-main-slot div.s-result-item", timeout=10000)
    items = await page.query_selector_all("div.s-main-slot div.s-result-item")
    print(f"📦 Amazon Listings found: {len(items)}")
    listings = {}
    added = 0
    for item in items:
        if added >= max_items:
            break
        try:
            if await item.get_attribute("data-component-type") == "sp-sponsored-result":
                continue
            link_elements = await item.query_selector_all("a.a-link-normal[href]")
            link = None
            for le in link_elements:
                href = await le.get_attribute("href")
                if href and "/dp/" in href:
                    link = href
                    break
            if not link:
                continue
            if not link.startswith("http"):
                link = "https://www.amazon.com" + link
            link = re.split(r"[?&]", link)[0]
            price_element = await item.query_selector(".a-price .a-offscreen")
            if not price_element:
                continue
            price_str = await price_element.text_content()
            price = float(re.sub(r"[^0-9.]", "", price_str))
            # Try extracting the full H2 first (it sometimes holds full product title)
            h2_element = await item.query_selector("h2")
            title = await h2_element.inner_text() if h2_element else "No title"

            if len(title.strip()) < 10 or title.strip().lower() in {"spaceaid", "minboo", "lelelinky"}:
                continue
            if (min_price is not None and price < min_price) or (max_price is not None and price > max_price):
                continue
            asin = extract_asin(link)
            if not asin:
                continue
            listings[asin] = {
                "full_url": canonical_url(link),
                "price": price,
                "title": title
            }
            added += 1
        except Exception as e:
            print(f"⚠️ Error processing Amazon listing: {e}")
            continue
    return listings

async def check_alerts():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        with app.app_context():
            tracked_items = TrackedItem.query.all()
            for item in tracked_items:
                try:
                    platform = "amazon" if "amazon.com" in item.url else "ebay"
                    search_url = build_search_url(platform, item.keywords or "")
                    is_amazon = platform == "amazon"
                    competitors = CompetitorItem.query.filter_by(tracked_item_id=item.id).all()
                    min_price = item.price_min if item.price_min is not None else 0
                    max_price = item.price_max if item.price_max is not None else float('inf')
                    max_competitors = item.max_competitors if item.max_competitors is not None else 10
                    extract_id = extract_asin if is_amazon else extract_item_id
                    current_listings = await fetch_amazon_results(page, search_url, min_price, max_price, max_competitors) if is_amazon else await fetch_search_results(page, search_url)

                    original_id = extract_id(item.url)
                    stored_ids = {extract_id(c.url): c for c in competitors if extract_id(c.url)}
                    current_ids = {
                        k: v for k, v in current_listings.items()
                        if k != original_id and k not in (None, '')
                    }

                    disappeared = [stored_ids[k] for k in stored_ids if k not in current_ids]
                    still_active = [stored_ids[k] for k in stored_ids if k in current_ids]

                    # 🟢 Update price/title for still-active
                    price_changes = []
                    for comp in still_active:
                        cid = extract_id(comp.url)
                        new_price = current_ids[cid]["price"]
                        new_title = current_ids[cid].get("title", comp.title)
                        if abs(comp.price - new_price) >= 0.01:
                            price_changes.append({"url": comp.url, "old_price": comp.price, "new_price": new_price})
                            comp.price = new_price
                        if not comp.title and new_title:
                            comp.title = new_title

                    # 🔴 Delete disappeared
                    for comp in disappeared:
                        db.session.delete(comp)

                    # 🆕 Add new competitors (based on active count, not stale IDs)
                    existing_ids = set(stored_ids.keys())
                    current_valid_ids = set(current_ids.keys()) - existing_ids
                    new_competitors = []
                    for cid in current_valid_ids:
                        if len(still_active) + len(new_competitors) >= max_competitors:
                            break
                        info = current_ids[cid]
                        new_comp = CompetitorItem(
                            tracked_item_id=item.id,
                            url=info["full_url"],
                            title=info.get("title", ""),
                            price=info["price"]
                        )
                        db.session.add(new_comp)
                        new_competitors.append(info["full_url"])

                    # Update item
                    item.last_checked = datetime.datetime.now()
                    summary = []
                    if price_changes:
                        summary.append(f"💲 {len(price_changes)} price change(s)")
                    if disappeared:
                        summary.append(f"🚨 {len(disappeared)} removed")
                    if new_competitors:
                        summary.append(f"🆕 {len(new_competitors)} new competitor(s)")
                    if not summary:
                        summary.append("✅ No changes detected")
                    item.last_alert_summary = " • ".join(summary)

                    # 🔔 Send notifications
                    plain_message = f"🛒 Tracking Item: {item.title or item.url}\n\n" + "\n".join(summary)
                    plain_message += f"\n📅 Checked on: {item.last_checked.strftime('%Y-%m-%d %H:%M')}"
                    alert = AlertSetting.query.filter_by(tracked_item_id=item.id).first()
                    if alert.email_address:
                        html_body = build_full_html_email(price_changes, [c.url for c in disappeared], new_competitors,
                                                          no_changes=not any([
                                                              price_changes, disappeared, new_competitors]))
                        send_email(alert.email_address, f"CompetiView Alert - {item.title}", plain_message, html_body)


                    db.session.commit()
                    print(f"✅ {item.title or item.url}: {len(price_changes)} price changes, {len(disappeared)} removed, {len(new_competitors)} added")
                except Exception as e:
                    print(f"❌ Error processing item ID {item.id}: {e}")
                    continue
        await browser.close()

if __name__ == "__main__":
    while True:
        asyncio.run(check_alerts())
        print("⏳ Waiting 24 hours until next run...")
        time.sleep(60 * 60 * 24)
