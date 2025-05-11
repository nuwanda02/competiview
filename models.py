from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class TrackedItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    url = db.Column(db.Text, nullable=False)
    title = db.Column(db.Text)
    keywords = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    last_checked = db.Column(db.DateTime)
    last_alert_summary = db.Column(db.Text)
    price_min = db.Column(db.Float)
    price_max = db.Column(db.Float)
    max_competitors = db.Column(db.Integer)

class AlertSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracked_item_id = db.Column(db.Integer, db.ForeignKey('tracked_item.id'))
    user_id = db.Column(db.Integer, nullable=False)  # ✅ Add this line
    price_alert_enabled = db.Column(db.Boolean, default=False)
    stock_alert_enabled = db.Column(db.Boolean, default=False)
    new_competitor_alert_enabled = db.Column(db.Boolean, default=False)
    email_address = db.Column(db.Text)
    telegram_handle = db.Column(db.Text)


class CompetitorItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracked_item_id = db.Column(db.Integer, db.ForeignKey('tracked_item.id'))
    url = db.Column(db.Text)
    title = db.Column(db.Text)
    price = db.Column(db.Float)
