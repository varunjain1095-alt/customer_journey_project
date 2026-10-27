"""
DuckDB Database Manager and DDL Definitions.
"""
import duckdb
from typing import Optional


class DatabaseManager:
    """
    Manages DuckDB database connection, schema initialization, and table DDLs.
    """
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = duckdb.connect(self.db_path)
        self.init_schema()

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        return self.conn

    def init_schema(self):
        """Initializes all 9 relational tables."""
        queries = [
            # 1. Customer Attributes (Shared Dimension Table)
            """
            CREATE TABLE IF NOT EXISTS CUSTOMERS (
                user_id VARCHAR PRIMARY KEY,
                customer_name VARCHAR,
                age INTEGER,
                city VARCHAR,
                country VARCHAR,
                gender VARCHAR,
                device_type VARCHAR,
                traffic_source VARCHAR,
                membership_status VARCHAR,
                sign_up_date DATE
            );
            """,
            # 2. Customer Behavioral Metrics (Derived Table per dataset_version)
            """
            CREATE TABLE IF NOT EXISTS BEHAVIORAL_METRICS (
                user_id VARCHAR,
                dataset_version VARCHAR,
                total_sessions INTEGER,
                login_frequency DOUBLE,
                page_visits INTEGER,
                reviews_given INTEGER,
                discount_usage INTEGER,
                session_duration_average DOUBLE,
                pages_per_session DOUBLE,
                cart_abandonment_rate DOUBLE,
                wishlist_items INTEGER,
                total_purchases INTEGER,
                average_order_value DOUBLE,
                days_since_last_purchase INTEGER,
                email_open_rate DOUBLE,
                customer_service_calls INTEGER,
                payment_mode_diversity INTEGER,
                lifetime_value DOUBLE,
                PRIMARY KEY (user_id, dataset_version),
                FOREIGN KEY (user_id) REFERENCES CUSTOMERS(user_id)
            );
            """,
            # 3. Products (Shared Dimension Table)
            """
            CREATE TABLE IF NOT EXISTS PRODUCTS (
                product_id VARCHAR PRIMARY KEY,
                product_name VARCHAR,
                category_id VARCHAR,
                category_code VARCHAR,
                brand VARCHAR,
                current_price DOUBLE,
                popularity_seed DOUBLE,
                total_units_sold INTEGER DEFAULT 0
            );
            """,
            # 4. Transactional & Session Events (Fact Table)
            """
            CREATE TABLE IF NOT EXISTS TRANSACTION_EVENTS (
                event_id VARCHAR PRIMARY KEY,
                dataset_version VARCHAR,
                event_time TIMESTAMP,
                event_type VARCHAR,
                product_id VARCHAR,
                price DOUBLE,
                user_id VARCHAR,
                user_session VARCHAR,
                journey_id VARCHAR,
                FOREIGN KEY (user_id) REFERENCES CUSTOMERS(user_id),
                FOREIGN KEY (product_id) REFERENCES PRODUCTS(product_id)
            );
            """,
            # 5. Search & Filter Events (Fact Table)
            """
            CREATE TABLE IF NOT EXISTS SEARCH_FILTER_EVENTS (
                event_id VARCHAR PRIMARY KEY,
                dataset_version VARCHAR,
                event_time TIMESTAMP,
                event_type VARCHAR,
                search_term VARCHAR,
                search_filter_1 VARCHAR,
                search_filter_2 VARCHAR,
                search_filter_3 VARCHAR,
                scroll_position INTEGER,
                user_id VARCHAR,
                user_session VARCHAR,
                journey_id VARCHAR,
                FOREIGN KEY (user_id) REFERENCES CUSTOMERS(user_id)
            );
            """,
            # 6. Product Page Engagement Events (Fact Table)
            """
            CREATE TABLE IF NOT EXISTS PAGE_ENGAGEMENT_EVENTS (
                event_id VARCHAR PRIMARY KEY,
                dataset_version VARCHAR,
                event_time TIMESTAMP,
                event_type VARCHAR,
                product_id VARCHAR,
                session_id VARCHAR,
                user_id VARCHAR,
                journey_id VARCHAR,
                FOREIGN KEY (user_id) REFERENCES CUSTOMERS(user_id),
                FOREIGN KEY (product_id) REFERENCES PRODUCTS(product_id)
            );
            """,
            # 7. Search Events Satellite
            """
            CREATE TABLE IF NOT EXISTS SEARCH_SATELLITE (
                event_id VARCHAR PRIMARY KEY,
                dataset_version VARCHAR,
                results_returned INTEGER,
                result_position_clicked INTEGER,
                FOREIGN KEY (event_id) REFERENCES SEARCH_FILTER_EVENTS(event_id)
            );
            """,
            # 8. Product Page Engagement Satellite
            """
            CREATE TABLE IF NOT EXISTS PAGE_ENGAGEMENT_SATELLITE (
                event_id VARCHAR PRIMARY KEY,
                dataset_version VARCHAR,
                time_spent_seconds DOUBLE,
                FOREIGN KEY (event_id) REFERENCES PAGE_ENGAGEMENT_EVENTS(event_id)
            );
            """,
            # 9. Starred Paths
            """
            CREATE TABLE IF NOT EXISTS STARRED_PATHS (
                pm_user_id VARCHAR,
                path_id VARCHAR,
                dataset_version VARCHAR,
                starred_at TIMESTAMP,
                PRIMARY KEY (pm_user_id, path_id, dataset_version)
            );
            """,
            # 10. Leak Hypotheses (mirrors STARRED_PATHS for Feature 4 close-the-loop)
            """
            CREATE TABLE IF NOT EXISTS LEAK_HYPOTHESES (
                pm_user_id VARCHAR,
                edge_key VARCHAR,
                dataset_version VARCHAR,
                hypothesis VARCHAR,
                experiment_url VARCHAR,
                baseline_lost_volume INTEGER,
                baseline_edge_count INTEGER,
                baseline_dropoff_pct DOUBLE,
                updated_at TIMESTAMP,
                PRIMARY KEY (pm_user_id, edge_key, dataset_version)
            );
            """,
        ]
        for query in queries:
            self.conn.execute(query)

    def close(self):
        self.conn.close()
