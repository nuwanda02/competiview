import re
from flask import Flask, request, render_template, redirect, url_for, session, flash
from flask_session import Session
import os
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, TrackedItem, AlertSetting, CompetitorItem


app = Flask(__name__)
app.secret_key = 'secret_key_here'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///competiview.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
Session(app)


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    password = db.Column(db.String, nullable=False)

    @staticmethod
    def get(user_id):
        return User.query.get(user_id)


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'



@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


with app.app_context():
    db.create_all()

with app.app_context():
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    full_path = os.path.abspath(db_path)
    print("➡ DB full path:", full_path)
    print("➡ File exists before test insert?", os.path.isfile(full_path))

    print("➡ File exists after test insert?", os.path.isfile(full_path))


def clean_text(text):
    return re.sub(r'[^\w\s]', '', text.lower())

from urllib.parse import urlparse

def canonical_url(url):
    """
    Normalize product URLs so that different variations resolve to the same form.
    Removes query params and standardizes path.
    """
    parsed = urlparse(url)
    path = parsed.path

    # --- AMAZON ---
    if "amazon." in parsed.netloc:
        parts = path.split('/')
        if 'dp' in parts:
            idx = parts.index('dp')
            asin = parts[idx + 1] if idx + 1 < len(parts) else ''
            canonical = f"https://{parsed.netloc}/dp/{asin}"
            return canonical.lower()

    # --- EBAY ---
    if "ebay." in parsed.netloc:
        parts = path.strip('/').split('/')
        item_id = ''
        for part in reversed(parts):
            if part.isdigit():
                item_id = part
                break
        if item_id:
            canonical = f"https://{parsed.netloc}/itm/{item_id}"
            return canonical.lower()

    # --- Default fallback ---
    return f"{parsed.scheme}://{parsed.netloc}{path}".lower()

def hash_url(url):
    from hashlib import md5
    normalized = canonical_url(url)
    return md5(normalized.encode('utf-8')).hexdigest()

def detect_platform(url):
    if "amazon.com" in url:
        return "amazon"
    elif "ebay.com" in url:
        return "ebay"
    else:
        return "unknown"
def extract_asin(url):
    match = re.search(r"/dp/([A-Z0-9]{10})", url)
    return match.group(1) if match else None

def normalize_amazon_url(url):
    asin = extract_asin(url)
    return f"https://www.amazon.com/dp/{asin}" if asin else url


def extract_title_and_competitors(url, max_items=10, price_min=None, price_max=None):
    title = None
    keywords = []
    results = []
    search_url = None

    platform = detect_platform(url)
    print(f"🔍 Detected platform: {platform}")

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            def tokenize_keywords(title):
                blacklist = {'inch', 'inches', 'x', 'store', 'with', 'and', 'or'}
                tokens = clean_text(title).split()
                return [t for t in tokens if not t.isdigit() and t not in blacklist]

            if platform == "ebay":
                page.goto(url, timeout=15000)
                page.wait_for_timeout(1000)
                try:
                    title = page.locator("h1#itemTitle").inner_text(timeout=5000)
                    title = title.replace("Details about  \xa0", "").strip()
                except:
                    title = page.title().split("|")[0].strip()

                keywords = tokenize_keywords(title)
                query = "+".join(keywords[:6])
                search_url = f"https://www.ebay.com/sch/i.html?_nkw={query}"

                page.goto(search_url, timeout=15000)
                items = page.locator("li.s-item")
                item_count = items.count()
                print("📦 eBay Listings found:", item_count)

                added = 0
                for i in range(item_count):
                    if added >= max_items:
                        break

                    print(f"\n🔍 Processing eBay item {i}")
                    item = items.nth(i)

                    try:
                        title_text = item.locator("div.s-item__title span[role='heading']").text_content(
                            timeout=3000).strip()
                        title_text = title_text.replace("New Listing", "")
                        print("✅ Title:", title_text)
                        if not title_text or "shop on ebay" in title_text.lower():
                            print("❌ Skipped: Invalid title")
                            continue
                    except Exception as e:
                        print("❌ Skipped: Failed to get title —", e)
                        continue

                    try:
                        price_text = item.locator("span.s-item__price").first.inner_text(timeout=3000).strip()
                        print("✅ Raw price text:", price_text)
                        price_str = price_text.split('-')[0].strip()
                        price = float(re.sub(r"[^\d.]", "", price_str))
                        print("✅ Parsed price:", price)
                    except Exception as e:
                        print("❌ Skipped: Failed to get price —", e)
                        continue

                    try:
                        link = item.locator("a.s-item__link").get_attribute("href")
                        print("✅ Link:", link)
                        if link and '?' in link:
                            link = link.split('?')[0]
                    except Exception as e:
                        print("❌ Skipped: Failed to get link —", e)
                        continue

                    if not link or canonical_url(link) == canonical_url(url):
                        print("❌ Skipped: Duplicate or invalid link")
                        continue

                    html_preview = item.inner_html()

                    if price_min is not None and price < price_min:
                        print(f"❌ Skipped: Price {price} < min {price_min}")
                        continue
                    if price_max is not None and price > price_max:
                        print(f"❌ Skipped: Price {price} > max {price_max}")
                        continue

                    print("✅ Competitor added.")
                    results.append({
                        "title": title_text,
                        "price": price,
                        "url": normalize_amazon_url(link)  # or canonical_url(link) if you're not on Amazon
                    })
                    added += 1


            elif platform == "amazon":
                page.goto(url, timeout=20000)
                page.wait_for_load_state("networkidle")
                try:
                    title = page.locator("span#productTitle").inner_text(timeout=5000).strip()
                except:
                    title = page.title().split(":")[0].strip()

                print(f"🛒 Amazon product title: {title}")
                keywords = tokenize_keywords(title)
                print(keywords)
                query = "+".join(keywords[:6])
                search_url = f"https://www.amazon.com/s?k={query}"

                try:
                    page.goto(search_url, timeout=20000)
                    page.wait_for_selector("div.s-main-slot div.s-result-item", timeout=10000)
                except Exception as e:
                    print(f"❌ Amazon search page failed to load: {e}")
                    browser.close()
                    return title, keywords, results, search_url

                items = page.locator("div.s-main-slot div.s-result-item")
                item_count = items.count()
                print("📦 Amazon Listings found:", item_count)

                added = 0
                for i in range(item_count):
                    if added >= max_items:
                        break
                    try:
                        item = items.nth(i)
                        if item.get_attribute("data-component-type") == "sp-sponsored-result":
                            continue

                        link_elements = item.locator("a.a-link-normal[href]")
                        link = None
                        for j in range(link_elements.count()):
                            href = link_elements.nth(j).get_attribute("href")
                            if href and "/dp/" in href:
                                link = href
                                break
                        if not link:
                            continue
                        if not link.startswith("http"):
                            link = "https://www.amazon.com" + link
                        # Strip query params from Amazon link
                        link = re.split(r'[?&]', link)[0]

                        price_locator = item.locator(".a-price .a-offscreen")
                        if not price_locator.count():
                            continue
                        try:
                            price_str = price_locator.first.inner_text()
                            price = float(re.sub(r'[^0-9.]', '', price_str))

                            if price_min is not None and price < price_min:
                                print(f"⛔ Filtered (too low): {price}")
                                continue
                            if price_max is not None and price > price_max:
                                print(f"⛔ Filtered (too high): {price}")
                                continue
                        except Exception as e:
                            print(f"⚠️ Skipping item {i} due to price parse error: {e}")
                            continue

                        title_text = item.locator("h2.a-size-base-plus").inner_text(timeout=3000)
                        results.append({
                            "title": title_text,
                            "price": price,
                            "url": normalize_amazon_url(link)
                        })

                        added += 1
                    except Exception as e:
                        print(f"⚠️ Error processing Amazon item {i}: {e}")

                browser.close()

    except Exception as e:
        print(f"🚨 Error in extract_title_and_competitors: {e}")

    return title, keywords, results, search_url

def suggest_price(competitors):
    prices = [c.price for c in competitors if c.price and c.price > 0]
    if not prices:
        return None

    avg_price = sum(prices) / len(prices)
    min_price = min(prices)

    # Suggest just below average or slightly undercut the lowest
    undercut_price = round(min_price - 0.10, 2)
    safe_price = round((avg_price + min_price) / 2, 2)

    # Simple heuristic: if competitors are close in price, undercut
    if max(prices) - min_price < 5:
        return undercut_price
    else:
        return safe_price


@app.route('/', methods=['GET', 'POST'])
@login_required

def track_new():
    title = None
    keywords = []
    competitors = []
    if request.method == 'POST':
        session.pop('selection_failed', None)
        try:
            max_items = int(request.form.get('limit', 10))
            if max_items not in [10, 20, 30]:
                max_items = 10
        except:
            max_items = 10

        try:
            price_min = float(request.form.get('price_min'))
        except:
            price_min = None
        try:
            price_max = float(request.form.get('price_max'))
        except:
            price_max = None

        url = request.form['url']
        session['source_url'] = canonical_url(url)
        session['min_price'] = price_min
        session['max_price'] = price_max
        session['max_competitors'] = max_items

        title, keywords, competitors, search_url = extract_title_and_competitors(url, max_items, price_min, price_max)
        session['extracted_keywords'] = keywords
        session['product_title'] = title
        session['search_url'] = search_url
        session['competitors_full'] = competitors

    return render_template('index.html', title=title, keywords=keywords, competitors=competitors, url=session.get('source_url'), selection_failed=session.pop('selection_failed', False))

@app.route('/track', methods=['POST'])
@login_required
def track():
    selected = request.form.getlist('track')
    url = request.form.get('source_url') or session.get('source_url')
    if not url:
        return redirect(url_for('index'))

    competitors_full = session.get('competitors_full', [])
    selected_competitors = [c for c in competitors_full if c.get("url") in selected]

    if not selected_competitors:
        session['selection_failed'] = True
        flash("No competitors selected. Please choose at least one to track.")
        return render_template('index.html',
                               title=session.get('product_title'),
                               keywords=session.get('extracted_keywords', []),
                               competitors=session.get('competitors_full', []),
                               url=session.get('source_url'),
                               selection_failed=True)

    # 🔥 ADD THIS — pull title and keywords from session
    title = session.get('product_title')
    keywords = session.get('extracted_keywords') or []

    # Check if already tracked
    existing = TrackedItem.query.filter_by(url=url, user_id=current_user.id).first()
    normalized_url = canonical_url(url)

    price_min = session.get('min_price')
    price_max = session.get('max_price')
    max_items = session.get('max_competitors', 10)

    if not existing:
        item = TrackedItem(
            user_id=current_user.id,
            url=normalized_url,
            title=title,
            keywords=", ".join(keywords),
            price_min=price_min,
            price_max=price_max,
            max_competitors=max_items
        )

        db.session.add(item)
        db.session.commit()
    else:
        item = existing

    CompetitorItem.query.filter_by(tracked_item_id=item.id).delete()

    # Save new competitors linked to the tracked item
    for comp in selected_competitors:
        clean_comp_url = canonical_url(comp.get('url'))
        competitor = CompetitorItem(
            tracked_item_id=item.id,
            url=clean_comp_url,
            title=comp.get('title'),
            price=comp.get('price')
        )
        db.session.add(competitor)

    db.session.commit()
    print(f"✅ Saved {len(selected_competitors)} competitors for tracked item ID {item.id}")

    session['track_count'] = len(selected_competitors)
    session['selection_done'] = True
    # Automatically create alert settings using user's registered email
    existing_alert = AlertSetting.query.filter_by(tracked_item_id=item.id).first()
    if not existing_alert:
        alert = AlertSetting(
            tracked_item_id=item.id,
            user_id=current_user.id,
            price_alert_enabled=True,
            stock_alert_enabled=True,
            new_competitor_alert_enabled=True,
            email_address=current_user.email  # ✅ Use user's email
        )
        db.session.add(alert)
        db.session.commit()

    session['alert_saved'] = True
    return redirect(url_for('track_confirmation'))


@app.route('/track/confirmation')
@login_required
def track_confirmation():
    count = session.get('track_count', 0)
    alert_saved = session.pop('alert_saved', False)
    return render_template('track_confirmation.html', count=count, alert_saved=alert_saved)

@app.route('/debug_competitors')
def debug_competitors():
    comps = CompetitorItem.query.all()
    html = "<h2>Competitors</h2><ul>"
    for c in comps:
        html += f"<li>Tracked ID: {c.tracked_item_id} | URL: {c.url} | Title: {c.title} | Price: {c.price}</li>"
    html += "</ul>"
    return html

@app.route('/delete_competitor/<int:comp_id>', methods=['POST'])
@login_required
def delete_competitor(comp_id):
    comp = CompetitorItem.query.get_or_404(comp_id)
    tracked_item = TrackedItem.query.get(comp.tracked_item_id)

    # Only allow delete if user owns the product
    if tracked_item.user_id != current_user.id:
        flash("Unauthorized", "danger")
        return redirect(url_for('dashboard'))

    db.session.delete(comp)
    db.session.commit()
    flash("Competitor deleted.", "success")
    return redirect(url_for('dashboard'))


@app.route('/reset')
def reset():
    session.clear()
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        hashed_pw = generate_password_hash(password)

        new_user = User(email=email, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()

        flash('Registered! Now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid login.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    items = TrackedItem.query.filter_by(user_id=current_user.id).all()

    enriched_items = []
    for item in items:
        competitors = CompetitorItem.query.filter_by(tracked_item_id=item.id).all()
        alert = AlertSetting.query.filter_by(tracked_item_id=item.id).first()
        suggested_price = suggest_price(competitors)

        enriched_items.append({
            "item": item,
            "competitor_count": len(competitors),
            "competitors": competitors,
            "alert": alert,
            "suggested_price": suggested_price  # ✅ add this
        })

    return render_template('dashboard.html', user=current_user, items=enriched_items)

@app.route('/refresh_competitors/<int:item_id>', methods=['POST'])
@login_required
def refresh_competitors(item_id):
    item = TrackedItem.query.get_or_404(item_id)
    if not item or item.user_id != current_user.id:
        flash("Unauthorized", "danger")
        return redirect(url_for('dashboard'))

    price_min = item.price_min
    price_max = item.price_max
    max_items = item.max_competitors or 10

    title, keywords, competitors, _ = extract_title_and_competitors(
        item.url, max_items, price_min, price_max, False
    )

    # Clear existing competitors
    CompetitorItem.query.filter_by(tracked_item_id=item.id).delete()

    # Add new competitors
    for comp in competitors:
        db.session.add(CompetitorItem(
            tracked_item_id=item.id,
            title=comp['title'],
            price=comp['price'],
            url=canonical_url(comp['url'])
        ))

    db.session.commit()
    flash("✅ Competitors refreshed successfully!", "success")
    return redirect(url_for('dashboard'))

@app.route('/toggle_alerts/<int:item_id>', methods=['POST'])
@login_required
def toggle_alerts(item_id):
    alert = AlertSetting.query.filter_by(tracked_item_id=item_id, user_id=current_user.id).first()
    if not alert:
        flash("Alert setting not found.", "danger")
        return redirect(url_for('dashboard'))

    status = request.form.get('status')
    if status == 'off':
        alert.email_address = None
        flash("✅ Email alerts deactivated.", "info")
    elif status == 'on':
        alert.email_address = current_user.email
        flash("✅ Email alerts activated.", "success")

    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/')
@login_required
def home_redirect():
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True)
