"""
Synthetic Dataset Generator Subsystem.
Implements Stage 1 (Shared Dimensions) and Stage 2 (Event Stream & Satellite/Derived Table Population).
"""
import uuid
import random
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
from duckdb import DuckDBPyConnection

from src.utils import sample_pareto_click_position

JOURNEY_INACTIVITY_THRESHOLD_SECONDS = 864_000  # 10 days


class SyntheticDatasetGenerator:
    def __init__(self, conn: DuckDBPyConnection, seed: int = 42):
        self.conn = conn
        self.seed = seed
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)

    @staticmethod
    def _new_journey_id(dataset_version: str) -> str:
        return f"JRN_{dataset_version}_{uuid.uuid4().hex[:8]}"

    def resolve_journey_id(self, prev_state: dict | None, session_start: datetime, dataset_version: str) -> str:
        """
        Journey grouping rule per (user_id, dataset_version):
        a journey continues while inactivity <= 10 days (864,000s) and the
        previous session did not end in a purchase; otherwise a new journey opens.
        """
        if prev_state is None:
            return self._new_journey_id(dataset_version)
        time_gap_seconds = (session_start - prev_state["last_event_time"]).total_seconds()
        if time_gap_seconds <= JOURNEY_INACTIVITY_THRESHOLD_SECONDS and not prev_state["last_was_purchase"]:
            return prev_state["journey_id"]
        return self._new_journey_id(dataset_version)

    def generate_shared_dimensions(self, num_customers: int = 1000, num_products: int = 100):
        """
        Stage 1: Generates shared CUSTOMERS and PRODUCTS dimensions ONCE.
        Both PRE and POST event generation runs read from these exact tables.
        """
        # 1. Generate CUSTOMERS
        cities = ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Seattle", "Austin", "Boston"]
        countries = ["USA", "Canada", "UK", "Germany", "France"]
        genders = ["Male", "Female", "Non-Binary"]
        device_types = ["Desktop", "Mobile", "Tablet"]
        traffic_sources = ["Direct", "Organic Search", "Paid Ad", "Email", "Social"]
        membership_statuses = ["Standard", "Gold", "Platinum"]

        customer_rows = []
        base_date = datetime(2025, 1, 1, tzinfo=timezone.utc)

        for i in range(1, num_customers + 1):
            user_id = f"USR_{i:05d}"
            customer_name = f"Customer_{i}"
            age = self.rng.randint(18, 70)
            city = self.rng.choice(cities)
            country = self.rng.choice(countries)
            gender = self.rng.choice(genders)
            device_type = self.rng.choice(device_types)
            traffic_source = self.rng.choice(traffic_sources)
            membership_status = self.rng.choice(membership_statuses)
            sign_up_date = (base_date + timedelta(days=self.rng.randint(0, 365))).strftime("%Y-%m-%d")

            customer_rows.append((
                user_id, customer_name, age, city, country, gender,
                device_type, traffic_source, membership_status, sign_up_date
            ))

        customers_df = pd.DataFrame(customer_rows, columns=[
            "user_id", "customer_name", "age", "city", "country", "gender",
            "device_type", "traffic_source", "membership_status", "sign_up_date"
        ])
        
        self.conn.register("df_customers_temp", customers_df)
        self.conn.execute("INSERT OR REPLACE INTO CUSTOMERS SELECT * FROM df_customers_temp")

        # 2. Generate PRODUCTS with Zipf-distributed popularity_seed
        categories = [
            ("CAT_01", "electronics", "Electronics"),
            ("CAT_02", "apparel", "Apparel"),
            ("CAT_03", "home_goods", "Home & Kitchen"),
            ("CAT_04", "books", "Books & Media"),
        ]
        brands = ["Aura", "Zenith", "Apex", "Vanguard", "Nexus", "Starlight"]

        # Draw Zipf popularity seeds (s = 1.1)
        zipf_seeds = self.np_rng.zipf(a=1.1, size=num_products).astype(float)
        # Add small float noise to ensure zero ties
        zipf_seeds += self.np_rng.uniform(0.001, 0.999, size=num_products)

        product_rows = []
        for i in range(1, num_products + 1):
            product_id = f"PROD_{i:03d}"
            product_name = f"Product_{i}"
            cat_id, cat_code, _ = self.rng.choice(categories)
            brand = self.rng.choice(brands)
            current_price = round(self.rng.uniform(10.0, 500.0), 2)
            popularity_seed = float(zipf_seeds[i - 1])

            product_rows.append((
                product_id, product_name, cat_id, cat_code, brand,
                current_price, popularity_seed, 0
            ))

        products_df = pd.DataFrame(product_rows, columns=[
            "product_id", "product_name", "category_id", "category_code", "brand",
            "current_price", "popularity_seed", "total_units_sold"
        ])

        self.conn.register("df_products_temp", products_df)
        self.conn.execute("INSERT OR REPLACE INTO PRODUCTS SELECT * FROM df_products_temp")

    def generate_event_stream(self, dataset_version: str = "PRE", total_sessions: int = 500):
        """
        Stage 2: Generates event stream for a specific dataset_version ('PRE' or 'POST')
        using the shared CUSTOMERS and PRODUCTS dimensions.
        """
        customers_df = self.conn.execute("SELECT user_id, device_type, traffic_source, membership_status, country, gender FROM CUSTOMERS").fetchdf()
        # Deterministic search ordering: popularity_seed DESC with product_id tie-break (edge-case 5.3)
        products_df = self.conn.execute("SELECT product_id, current_price, popularity_seed FROM PRODUCTS ORDER BY popularity_seed DESC, product_id ASC").fetchdf()

        user_list = customers_df["user_id"].tolist()
        device_map = dict(zip(customers_df["user_id"], customers_df["device_type"]))
        traffic_map = dict(zip(customers_df["user_id"], customers_df["traffic_source"]))
        member_map = dict(zip(customers_df["user_id"], customers_df["membership_status"]))
        country_map = dict(zip(customers_df["user_id"], customers_df["country"]))
        gender_map = dict(zip(customers_df["user_id"], customers_df["gender"]))
        product_list = products_df["product_id"].tolist()
        price_map = dict(zip(products_df["product_id"], products_df["current_price"]))

        start_base_time = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

        # Attribute-conditioned cohort probabilities per user, so that path choice
        # correlates with who the user is (segment profiles differentiate by cohort).
        weights_map = {
            u: self._cohort_weights(device_map[u], traffic_map[u], member_map[u],
                                    country_map[u], gender_map[u])
            for u in user_list
        }

        # Track previous session end time and journey_id per user for 10-day inactivity grouping
        # Key: (user_id, dataset_version) -> {"last_event_time": datetime, "journey_id": str, "last_was_purchase": bool}
        user_journey_state = {}

        tx_events = []
        search_events = []
        page_events = []
        search_satellites = []
        page_satellites = []

        # Pass 1: schedule all sessions (user assignment + arrival timestamp).
        # Congestion: 15% flash sale peak clustering at hour 14.
        scheduled_sessions = []
        for s_idx in range(1, total_sessions + 1):
            user_id = self.rng.choice(user_list)
            session_id = f"SESS_{dataset_version}_{s_idx:06d}"
            if self.rng.random() < 0.15:
                session_start = start_base_time + timedelta(days=self.rng.randint(0, 30), hours=14, minutes=self.rng.randint(0, 59))
            else:
                session_start = start_base_time + timedelta(days=self.rng.randint(0, 30), hours=self.rng.randint(0, 23), minutes=self.rng.randint(0, 59))
            scheduled_sessions.append((user_id, session_id, session_start))

        # Process each user's sessions in chronological order so the 10-day
        # inactivity rule is evaluated along the user's real event timeline.
        scheduled_sessions.sort(key=lambda s: (s[0], s[2]))

        for user_id, session_id, session_start in scheduled_sessions:
            device_type = device_map[user_id]

            # Evaluate journey_id assignment rule per (user_id, dataset_version)
            state_key = (user_id, dataset_version)
            prev_state = user_journey_state.get(state_key)
            journey_id = self.resolve_journey_id(prev_state, session_start, dataset_version)

            # Determine cohort: attribute-weighted around the 50/25/25 baseline
            cohort = self.rng.choices(
                ["Look-and-Leave", "Fast-Buy", "Research-Heavy"], weights_map[user_id]
            )[0]

            current_time = session_start
            session_event_records = []  # List of dicts for time_spent_seconds derivation

            # Select target product
            target_product = self.rng.choice(product_list)
            target_price = price_map[target_product]

            # Build the session's step sequence. Sessions can exit at any point:
            # all-or-nothing templates would make every funnel flat until the
            # final step. ~20% of converting sessions apply a coupon between
            # add_to_cart and login.
            # POST feature release = guest checkout: ~55% of converting POST
            # sessions skip login entirely (new variants like
            # view_product|add_to_cart|purchase emerge; the login paths shrink),
            # and post-login bail disappears for them.
            post_release = dataset_version == "POST"

            def finish_checkout(steps):
                """append cart-abandonment or checkout completion after add_to_cart"""
                r = self.rng.random()
                if r < 0.07:
                    return               # silent cart abandonment
                if r < 0.22:
                    steps.append("remove_from_cart")
                    return               # explicit cart abandonment
                if self.rng.random() < 0.20:
                    steps.append("apply_coupon")
                if post_release and self.rng.random() < 0.55:
                    steps.append("purchase")   # guest checkout: no login needed
                    return
                steps.append("login")
                if self.rng.random() >= 0.05:   # 5% bail after login
                    steps.append("purchase")

            if cohort == "Look-and-Leave":
                steps = ["view_product"]
            elif cohort == "Fast-Buy":
                steps = ["view_product"]
                if self.rng.random() >= 0.08:   # 8% exit right after viewing
                    steps.append("add_to_cart")
                    finish_checkout(steps)
            else:  # Research-Heavy: taper at each engagement step
                steps = ["search_query"]
                for step, bail in (("view_product", 0.06), ("view_reviews", 0.10),
                                   ("expand_description", 0.12), ("add_to_cart", 0.10)):
                    if self.rng.random() < bail:
                        break
                    steps.append(step)
                if steps[-1] == "add_to_cart":
                    finish_checkout(steps)

            purchased_in_session = steps[-1] == "purchase"
            checkout_steps = {"add_to_cart", "apply_coupon", "login"}

            for step in steps:
                e_id = f"EVT_{dataset_version}_{len(tx_events) + len(search_events) + len(page_events) + len(session_event_records) + 1:08d}"
                if step == "search_query":
                    table = "search"
                elif step in {"add_to_cart", "remove_from_cart", "purchase", "apply_coupon", "login"}:
                    table = "tx"
                else:
                    table = "page"

                session_event_records.append({
                    "event_id": e_id, "table": table, "event_type": step,
                    "time": current_time, "product_id": target_product, "price": target_price
                })

                if step in checkout_steps:
                    # Slow-step congestion: Gaussian checkout delay (mu=45s, sigma=15s)
                    delay = max(5.0, float(self.np_rng.normal(45.0, 15.0)))
                elif cohort == "Look-and-Leave":
                    delay = float(self.rng.randint(5, 30))
                elif cohort == "Fast-Buy":
                    delay = float(self.rng.randint(5, 20))
                else:
                    # Device friction: Mobile users take longer on engagement steps
                    multiplier = 1.8 if device_type == "Mobile" and step in {"view_reviews", "expand_description"} else 1.0
                    delay = max(5.0, float(self.rng.randint(10, 40)) * multiplier)
                current_time += timedelta(seconds=delay)

            # Process session event records into table lists & compute PAGE_ENGAGEMENT_SATELLITE.time_spent_seconds
            num_session_events = len(session_event_records)
            for idx, rec in enumerate(session_event_records):
                e_id = rec["event_id"]
                e_time = rec["time"]
                e_type = rec["event_type"]
                p_id = rec.get("product_id")

                # Time spent seconds delta to next event in session
                if idx < num_session_events - 1:
                    time_spent = (session_event_records[idx + 1]["time"] - e_time).total_seconds()
                else:
                    time_spent = 0.0

                if rec["table"] == "tx":
                    tx_events.append((
                        e_id, dataset_version, e_time, e_type, p_id,
                        rec.get("price", 0.0), user_id, session_id, journey_id
                    ))
                elif rec["table"] == "search":
                    search_events.append((
                        e_id, dataset_version, e_time, e_type,
                        "wireless product", "filter_brand", None, None,
                        self.rng.randint(0, 100), user_id, session_id, journey_id
                    ))
                    # Populate SEARCH_SATELLITE
                    num_results = self.rng.randint(5, 50)
                    pos_clicked = sample_pareto_click_position(num_results, self.rng)
                    search_satellites.append((e_id, dataset_version, num_results, pos_clicked))
                else:  # page
                    page_events.append((
                        e_id, dataset_version, e_time, e_type, p_id,
                        session_id, user_id, journey_id
                    ))
                    # Populate PAGE_ENGAGEMENT_SATELLITE
                    page_satellites.append((e_id, dataset_version, time_spent))

            # Update journey state for user
            user_journey_state[state_key] = {
                "last_event_time": current_time,
                "journey_id": journey_id,
                "last_was_purchase": purchased_in_session
            }

        # Bulk insert into DuckDB for this dataset_version
        if tx_events:
            tx_df = pd.DataFrame(tx_events, columns=[
                "event_id", "dataset_version", "event_time", "event_type",
                "product_id", "price", "user_id", "user_session", "journey_id"
            ])
            self.conn.register("df_tx_temp", tx_df)
            self.conn.execute("INSERT INTO TRANSACTION_EVENTS SELECT * FROM df_tx_temp")

        if search_events:
            search_df = pd.DataFrame(search_events, columns=[
                "event_id", "dataset_version", "event_time", "event_type",
                "search_term", "search_filter_1", "search_filter_2", "search_filter_3",
                "scroll_position", "user_id", "user_session", "journey_id"
            ])
            self.conn.register("df_search_temp", search_df)
            self.conn.execute("INSERT INTO SEARCH_FILTER_EVENTS SELECT * FROM df_search_temp")

        if page_events:
            page_df = pd.DataFrame(page_events, columns=[
                "event_id", "dataset_version", "event_time", "event_type",
                "product_id", "session_id", "user_id", "journey_id"
            ])
            self.conn.register("df_page_temp", page_df)
            self.conn.execute("INSERT INTO PAGE_ENGAGEMENT_EVENTS SELECT * FROM df_page_temp")

        if search_satellites:
            search_sat_df = pd.DataFrame(search_satellites, columns=[
                "event_id", "dataset_version", "results_returned", "result_position_clicked"
            ])
            self.conn.register("df_search_sat_temp", search_sat_df)
            self.conn.execute("INSERT INTO SEARCH_SATELLITE SELECT * FROM df_search_sat_temp")

        if page_satellites:
            page_sat_df = pd.DataFrame(page_satellites, columns=[
                "event_id", "dataset_version", "time_spent_seconds"
            ])
            self.conn.register("df_page_sat_temp", page_sat_df)
            self.conn.execute("INSERT INTO PAGE_ENGAGEMENT_SATELLITE SELECT * FROM df_page_sat_temp")

        # Derive and populate BEHAVIORAL_METRICS per (user_id, dataset_version)
        self.populate_behavioral_metrics(dataset_version)

        # Update PRODUCTS.total_units_sold post-hoc
        self.update_post_hoc_product_sales()

    @staticmethod
    def _cohort_weights(device_type: str, traffic_source: str, membership_status: str,
                        country: str = "", gender: str = ""):
        """
        Cohort probabilities conditioned on user attributes, around the spec'd
        50/25/25 baseline (Look-and-Leave, Fast-Buy, Research-Heavy):
        mobile friction and low-intent traffic push toward bouncing; returning
        and premium members push toward fast purchase. Country/gender add mild
        tilts so every segment dimension carries some path signal.
        """
        ll, fb, rh = 0.50, 0.25, 0.25
        if device_type == "Mobile":
            ll += 0.25; fb -= 0.15; rh -= 0.10
        elif device_type == "Tablet":
            ll += 0.10; fb -= 0.10
        if traffic_source in ("Paid Ad", "Social"):
            ll += 0.20; fb -= 0.10; rh -= 0.10
        elif traffic_source in ("Email", "Direct"):
            fb += 0.20; ll -= 0.15; rh -= 0.05
        if membership_status in ("Gold", "Platinum"):
            fb += 0.20; ll -= 0.15; rh -= 0.05
        if country in ("Germany", "UK"):
            rh += 0.15; ll -= 0.10; fb -= 0.05
        elif country == "USA":
            fb += 0.12; ll -= 0.12
        if gender == "Female":
            rh += 0.08; ll -= 0.08
        total = ll + fb + rh
        return (ll / total, fb / total, rh / total)

    def populate_behavioral_metrics(self, dataset_version: str):
        """
        Aggregates event data for dataset_version and populates BEHAVIORAL_METRICS per (user_id, dataset_version).
        """
        query = f"""
        INSERT OR REPLACE INTO BEHAVIORAL_METRICS
        SELECT 
            c.user_id,
            '{dataset_version}' AS dataset_version,
            COALESCE(s.total_sessions, 0) AS total_sessions,
            ROUND(COALESCE(s.total_sessions, 0) / 30.0, 2) AS login_frequency,
            COALESCE(p.page_visits, 0) AS page_visits,
            COALESCE(r.reviews_given, 0) AS reviews_given,
            COALESCE(d.discount_usage, 0) AS discount_usage,
            COALESCE(dur.avg_duration, 45.0) AS session_duration_average,
            ROUND(COALESCE(p.page_visits, 0) / GREATEST(COALESCE(s.total_sessions, 1), 1), 2) AS pages_per_session,
            CASE WHEN COALESCE(cart.cart_sessions, 0) > 0 
                 THEN ROUND(1.0 - (COALESCE(pur.purchases, 0) * 1.0 / cart.cart_sessions), 2)
                 ELSE 0.0 END AS cart_abandonment_rate,
            -- CRM-style attributes with no event source: deterministic per (user_id, dataset_version)
            CAST(hash(c.user_id || ':wishlist:' || '{dataset_version}') % 11 AS INTEGER) AS wishlist_items,
            COALESCE(pur.purchases, 0) AS total_purchases,
            ROUND(COALESCE(pur.total_spent, 0.0) / GREATEST(COALESCE(pur.purchases, 1), 1), 2) AS average_order_value,
            CAST(DATE_DIFF('day', lp.last_purchase_time, ds.max_time) AS INTEGER) AS days_since_last_purchase,
            ROUND((hash(c.user_id || ':email:' || '{dataset_version}') % 100) / 100.0, 2) AS email_open_rate,
            CAST(hash(c.user_id || ':svc:' || '{dataset_version}') % 4 AS INTEGER) AS customer_service_calls,
            CAST(hash(c.user_id || ':pay:' || '{dataset_version}') % 3 + 1 AS INTEGER) AS payment_mode_diversity,
            COALESCE(pur.total_spent, 0.0) AS lifetime_value
        FROM CUSTOMERS c
        LEFT JOIN (
            SELECT user_id, COUNT(DISTINCT user_session) AS total_sessions 
            FROM (
                SELECT user_id, user_session FROM TRANSACTION_EVENTS WHERE dataset_version = '{dataset_version}'
                UNION
                SELECT user_id, user_session FROM SEARCH_FILTER_EVENTS WHERE dataset_version = '{dataset_version}'
                UNION
                SELECT user_id, session_id AS user_session FROM PAGE_ENGAGEMENT_EVENTS WHERE dataset_version = '{dataset_version}'
            ) GROUP BY user_id
        ) s ON c.user_id = s.user_id
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS page_visits FROM PAGE_ENGAGEMENT_EVENTS WHERE dataset_version = '{dataset_version}' GROUP BY user_id
        ) p ON c.user_id = p.user_id
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS reviews_given FROM PAGE_ENGAGEMENT_EVENTS WHERE event_type = 'view_reviews' AND dataset_version = '{dataset_version}' GROUP BY user_id
        ) r ON c.user_id = r.user_id
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS discount_usage FROM TRANSACTION_EVENTS WHERE event_type = 'apply_coupon' AND dataset_version = '{dataset_version}' GROUP BY user_id
        ) d ON c.user_id = d.user_id
        LEFT JOIN (
            SELECT user_id, COUNT(DISTINCT user_session) AS cart_sessions FROM TRANSACTION_EVENTS WHERE event_type = 'add_to_cart' AND dataset_version = '{dataset_version}' GROUP BY user_id
        ) cart ON c.user_id = cart.user_id
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS purchases, SUM(price) AS total_spent FROM TRANSACTION_EVENTS WHERE event_type = 'purchase' AND dataset_version = '{dataset_version}' GROUP BY user_id
        ) pur ON c.user_id = pur.user_id
        LEFT JOIN (
            SELECT user_id, MAX(event_time) AS last_purchase_time FROM TRANSACTION_EVENTS WHERE event_type = 'purchase' AND dataset_version = '{dataset_version}' GROUP BY user_id
        ) lp ON c.user_id = lp.user_id
        CROSS JOIN (
            SELECT MAX(event_time) AS max_time FROM TRANSACTION_EVENTS WHERE dataset_version = '{dataset_version}'
        ) ds
        LEFT JOIN (
            SELECT user_id, AVG(time_spent_seconds) AS avg_duration FROM PAGE_ENGAGEMENT_SATELLITE s JOIN PAGE_ENGAGEMENT_EVENTS e ON s.event_id = e.event_id WHERE e.dataset_version = '{dataset_version}' GROUP BY user_id
        ) dur ON c.user_id = dur.user_id
        """
        self.conn.execute(query)

    def update_post_hoc_product_sales(self):
        """
        Updates PRODUCTS.total_units_sold post-hoc based on actual completed purchase events across all runs.
        """
        query = """
        UPDATE PRODUCTS
        SET total_units_sold = COALESCE(sales.units_sold, 0)
        FROM (
            SELECT product_id, COUNT(*) AS units_sold
            FROM TRANSACTION_EVENTS
            WHERE event_type = 'purchase'
            GROUP BY product_id
        ) sales
        WHERE PRODUCTS.product_id = sales.product_id;
        """
        self.conn.execute(query)
